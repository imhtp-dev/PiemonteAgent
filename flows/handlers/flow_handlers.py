"""
Flow generation and navigation handlers
"""

import json
from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.get_flowNb import genera_flow
from models.requests import HealthService


async def generate_flow_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Generate decision flow for the selected service and transition to flow navigation"""
    try:
        # Get selected services from state
        selected_services = flow_manager.state.get("selected_services", [])
        if not selected_services:
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "No service selected"}, create_error_node("No service selected. Please restart booking.")
        
        # Get patient info
        gender = flow_manager.state.get("patient_gender")
        date_of_birth = flow_manager.state.get("patient_dob") 
        address = flow_manager.state.get("patient_address")
        
        if not all([gender, date_of_birth, address]):
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "Missing patient information"}, create_error_node("Missing patient information. Please restart booking.")
        
        # Use first selected service to generate initial flow
        primary_service = selected_services[0]

        # Store flow generation parameters for processing node
        flow_manager.state["pending_flow_params"] = {
            "primary_service": primary_service,
            "selected_services": selected_services,
            "gender": gender,
            "date_of_birth": date_of_birth,
            "address": address
        }

        # Create intermediate node with pre_actions for immediate TTS
        flow_generation_status_text = f"Sto analizzando {primary_service.name} per determinare se ci sono requisiti speciali o opzioni aggiuntive. Attendi..."

        from flows.nodes.patient_info import create_flow_processing_node
        return {
            "success": True,
            "message": f"Starting flow generation for {primary_service.name}"
        }, create_flow_processing_node(primary_service.name, flow_generation_status_text)

    except Exception as e:
        logger.error(f"❌ Flow generation initialization error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Flow generation failed. Please try again."
        }, create_error_node("Flow generation failed. Please try again.")


async def perform_flow_generation_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual flow generation after TTS message"""
    # Hardcoded health center UUID for flow generation (Tradate center)
    # This avoids API call - actual center search happens later in Stage 5
    HARDCODED_HC_UUID = "c5535638-6c18-444c-955d-89139d8276be"

    try:
        # Get stored flow parameters
        params = flow_manager.state.get("pending_flow_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": "Missing flow parameters"
            }, create_error_node("Missing flow parameters. Please start over.")

        # Extract parameters
        primary_service = params["primary_service"]
        selected_services = params["selected_services"]
        gender = params["gender"]
        date_of_birth = params["date_of_birth"]
        address = params["address"]

        logger.info(f"🔄 Generating decision flow for: {primary_service.name}")
        logger.info(f"🏥 Using hardcoded health center UUID for flow generation: {HARDCODED_HC_UUID}")

        # Use hardcoded health center UUID for flow generation
        # Actual center availability will be checked in Stage 5 with radius expansion
        hc_uuids = [HARDCODED_HC_UUID]
        logger.info(f"🔄 Calling genera_flow with: centers={hc_uuids}, service={primary_service.uuid}")

        import asyncio
        loop = asyncio.get_event_loop()
        logger.info(f"🔍 Starting non-blocking flow generation")
        generated_flow = await loop.run_in_executor(
            None,  # Use default thread pool executor
            genera_flow,
            hc_uuids,  # Pass list of health center UUIDs
            primary_service.uuid  # Pass medical exam ID
        )
        logger.info(f"✅ Flow generation completed")
        
        if not generated_flow:
            logger.warning(f"Failed to generate flow for {primary_service.name}, proceeding with direct booking")
            from flows.nodes.booking import create_final_center_search_node
            return {"success": True, "message": "Proceeding to center selection"}, create_final_center_search_node()
        
        # Store the generated flow in state
        flow_manager.state["generated_flow"] = generated_flow
        # Note: available_centers will be populated later in Stage 5 (center search with radius expansion)
        
        logger.success(f"✅ Generated decision flow for {primary_service.name}")
        
        result = {
            "success": True,
            "flow_generated": True,
            "service_name": primary_service.name,
            "message": f"Generated decision flow for {primary_service.name}"
        }
        
        # Transition to LLM-driven flow navigation
        from flows.nodes.booking import create_flow_navigation_node
        pending = flow_manager.state.get("pending_additional_request", "")
        return result, create_flow_navigation_node(generated_flow, primary_service.name, pending)
        
    except Exception as e:
        logger.error(f"Flow generation error: {e}")
        from flows.nodes.completion import create_error_node
        return {"success": False, "message": "Failed to generate decision flow"}, create_error_node("Failed to generate decision flow. Please try again.")


async def finalize_services_and_search_centers(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Extract final service selections from flow navigation and search centers"""
    try:
        # Get parameters from LLM flow navigation
        additional_services = args.get("additional_services", [])
        flow_path = args.get("flow_path", "")

        # Get existing selected services from state
        selected_services = flow_manager.state.get("selected_services", [])

        # Get the generated flow to understand what services were offered
        generated_flow = flow_manager.state.get("generated_flow", {})

        logger.info(f"🔍 Flow navigation complete:")
        logger.info(f"   Additional services: {additional_services}")
        logger.info(f"   Flow path: {flow_path}")
        logger.info(f"   Original services: {[s.name for s in selected_services]}")
        
        # Add additional services to state (avoid duplicates by UUID)
        existing_uuids = {service.uuid for service in selected_services}
        
        # Process additional services from the decision flow
        for additional_service in additional_services:
            service_uuid = additional_service.get("uuid")
            service_name = additional_service.get("name")
            service_code = additional_service.get("code")
            service_sector = additional_service.get("sector")

            logger.debug(f"🔍 Processing additional service from LLM:")
            logger.debug(f"   Name: {service_name}")
            logger.debug(f"   UUID: {service_uuid}")
            logger.debug(f"   Code: {service_code}")
            logger.debug(f"   Sector: {service_sector}")

            # Check for duplicates FIRST (before validation)
            # This handles cases where LLM includes the original service without proper code/sector
            if not service_uuid:
                logger.error(f"❌ Service missing UUID: {service_name}")
                continue

            if service_uuid in existing_uuids:
                logger.debug(f"⚠️ Service '{service_name}' (UUID: {service_uuid}) already in selected services, skipping")
                continue

            # Validate required fields for NEW services only
            if not service_code:
                logger.error(f"❌ Service '{service_name}' missing code field - LLM did not extract it from flow structure")
                continue

            if not service_sector:
                logger.error(f"❌ Service '{service_name}' missing sector field - LLM did not extract it from flow structure")
                continue

            # Create HealthService object with all required fields
            try:
                new_service = HealthService(
                    uuid=service_uuid,
                    name=service_name,
                    code=service_code,
                    synonyms=[],
                    sector=service_sector
                )
                selected_services.append(new_service)
                existing_uuids.add(service_uuid)
                logger.success(f"✅ Added additional service: {service_name} (sector: {service_sector}, code: {service_code})")
            except Exception as e:
                logger.error(f"❌ Failed to create HealthService for '{service_name}': {e}")

        # Ensure we have at least the original service
        if not selected_services:
            logger.warning("⚠️  No services in final selection, this shouldn't happen")
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "No services selected"}, create_error_node("No services selected. Please restart booking.")
        
        flow_manager.state["selected_services"] = selected_services

        # Check if pending additional service was matched during orange box navigation
        flow_path = args.get("flow_path", "")
        if "pending_matched" in flow_path:
            flow_manager.state["pending_additional_resolved"] = True
            flow_manager.state.pop("pending_additional_request", None)
            logger.info("✅ Pending additional service included via orange box flow")

        logger.success(f"🎯 Final service selection: {[s.name for s in selected_services]}")
        logger.success(f"📊 Service count: {len(selected_services)}")
        
        # Transition to final center search with all services
        from flows.nodes.booking import create_final_center_search_node
        return {
            "success": True,
            "final_services": [s.name for s in selected_services],
            "service_count": len(selected_services)
        }, create_final_center_search_node()
        
    except Exception as e:
        logger.error(f"Service finalization error: {e}")
        from flows.nodes.completion import create_error_node
        return {"success": False, "message": "Failed to finalize services"}, create_error_node("Failed to finalize services. Please try again.")