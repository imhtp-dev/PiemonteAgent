"""
Flow generation and navigation handlers
"""

import json
from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.get_flowNb import genera_flow
from services.cerba_api import cerba_api
from utils.api_retry import retry_api_call
from models.requests import HealthService

# Fallback HC UUID (Tradate) if center search finds nothing at max radius
FALLBACK_HC_UUID = "c5535638-6c18-444c-955d-89139d8276be"

# Silent radius expansion steps (no user interaction)
SILENT_RADIUS_STEPS = [None, 42, 62]  # None = API default 22km


async def perform_silent_center_search_and_generate_flow(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Silent center search + flow generation in one step. No TTS, no user interaction."""
    try:
        selected_services = flow_manager.state.get("selected_services", [])
        if not selected_services:
            from flows.nodes.completion import create_error_node
            return {"success": False}, create_error_node("No service selected. Please restart booking.")

        primary_service = selected_services[0]
        gender = flow_manager.state.get("patient_gender", "m")
        dob = flow_manager.state.get("patient_dob", "")
        address = flow_manager.state.get("patient_address", "")

        # Format DOB: "1979-06-19" → "19790619"
        dob_formatted = dob.replace("-", "") if dob else "19900811"

        # Get service UUIDs for center search
        service_uuids = [s.uuid for s in selected_services]

        logger.info(f"🔇 Silent center search for {len(service_uuids)} services near {address}")

        # --- Silent auto-expanding center search ---
        import asyncio
        loop = asyncio.get_event_loop()
        health_centers = []

        for radius in SILENT_RADIUS_STEPS:
            radius_display = radius if radius else "22 (default)"
            logger.info(f"🔍 Trying radius={radius_display}km")

            def _search(r=radius):
                result, error = retry_api_call(
                    api_func=cerba_api.get_health_centers,
                    max_retries=2,
                    retry_delay=1.0,
                    func_name=f"Silent HC Search (radius={radius_display}km)",
                    health_services=service_uuids,
                    gender=gender,
                    date_of_birth=dob_formatted,
                    address=address,
                    radius=r,
                )
                if error:
                    raise error
                return result

            try:
                health_centers = await loop.run_in_executor(None, _search)
                if health_centers:
                    logger.success(f"✅ Found {len(health_centers)} centers at radius={radius_display}km")
                    break
            except Exception as e:
                logger.warning(f"⚠️ Center search failed at radius={radius_display}km: {e}")

        # Extract HC UUIDs (or fallback)
        if health_centers:
            hc_uuids = [hc.uuid for hc in health_centers]
        else:
            logger.warning(f"⚠️ No centers found at max radius, falling back to Tradate UUID")
            hc_uuids = [FALLBACK_HC_UUID]

        flow_manager.state["orange_box_hc_uuids"] = hc_uuids
        logger.info(f"🏥 Using {len(hc_uuids)} HC UUIDs for genera_flow")

        # --- Generate flow ---
        logger.info(f"🔄 Calling genera_flow for {primary_service.name}")

        generated_flow = await loop.run_in_executor(
            None,
            genera_flow,
            hc_uuids,
            primary_service.uuid,
            gender,
            dob_formatted,
        )

        if not generated_flow:
            logger.warning(f"genera_flow returned empty for {primary_service.name}, skipping to center search")
            from flows.nodes.booking import create_final_center_search_node
            return {"success": True, "message": "No flow generated, proceeding to center search"}, create_final_center_search_node()

        # Print generated flow to terminal
        print(f"\n{'='*60}")
        print(f"📋 GENERATED FLOW for {primary_service.name}")
        print(f"{'='*60}")
        if isinstance(generated_flow, str):
            print(generated_flow)
        else:
            print(json.dumps(generated_flow, indent=2, ensure_ascii=False))
        print(f"{'='*60}\n")

        # Parse generated flow
        if isinstance(generated_flow, str):
            generated_flow = json.loads(generated_flow)

        # Check if list_health_services is empty → skip flow navigation
        list_hs = generated_flow.get("list_health_services", [])
        has_services = bool(list_hs) and list_hs != []

        # Store flow in state
        flow_manager.state["generated_flow"] = generated_flow

        if not has_services:
            logger.info(f"📋 No related services for {primary_service.name}, skipping flow navigation")
            from flows.nodes.booking import create_final_center_search_node
            return {
                "success": True,
                "flow_generated": True,
                "skipped_navigation": True,
                "message": f"No additional services for {primary_service.name}"
            }, create_final_center_search_node()

        logger.success(f"✅ Flow generated with services, proceeding to navigation")

        from flows.nodes.booking import create_flow_navigation_node
        pending = flow_manager.state.get("pending_additional_request", "")
        return {
            "success": True,
            "flow_generated": True,
            "message": f"Generated decision flow for {primary_service.name}"
        }, create_flow_navigation_node(generated_flow, primary_service.name, pending)

    except Exception as e:
        logger.error(f"❌ Silent center search + flow generation error: {e}")
        # Don't break the flow — fall through to center search
        from flows.nodes.booking import create_final_center_search_node
        return {"success": False, "message": "Flow generation failed, proceeding to center search"}, create_final_center_search_node()


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

        # Check if pending additional service was satisfied during orange box navigation
        pending_req = flow_manager.state.get("pending_additional_request", "")
        if pending_req:
            # Check 1: LLM explicitly flagged pending_matched=true
            matched = args.get("pending_matched", False)

            # Check 2: Programmatic fallback — check if any selected service name matches
            if not matched:
                pending_lower = pending_req.lower().strip()
                for svc in selected_services:
                    if pending_lower in svc.name.lower() or svc.name.lower().strip() in pending_lower:
                        matched = True
                        logger.info(f"✅ Programmatic match: '{svc.name}' matches pending '{pending_req}'")
                        break

            if matched:
                flow_manager.state["pending_additional_resolved"] = True
                flow_manager.state.pop("pending_additional_request", None)
                logger.info("✅ Pending additional service resolved via orange box flow")

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