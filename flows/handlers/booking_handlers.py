"""
Booking and slot management flow handlers
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, List
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.cerba_api import cerba_api
from services.slotAgenda import list_slot, create_slot, delete_slot
from utils.api_retry import retry_api_call
from models.requests import HealthService, HealthCenter
from services.llm_interpretation import interpret_sorting_scenario
from config.settings import settings


async def search_final_centers_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Search health centers with all selected services and transition to center selection"""
    try:
        # Get all selected services from state
        selected_services = flow_manager.state.get("selected_services", [])
        if not selected_services:
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "No services selected"}, create_error_node("No services selected. Please restart booking.")
        
        # Get patient information
        gender = flow_manager.state.get("patient_gender")
        date_of_birth = flow_manager.state.get("patient_dob") 
        address = flow_manager.state.get("patient_address")
        
        if not all([gender, date_of_birth, address]):
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "Missing patient information"}, create_error_node("Missing patient information. Please restart booking.")
        
        # Prepare service UUIDs
        service_uuids = [service.uuid for service in selected_services]
        service_names = [service.name for service in selected_services]
        
        logger.info(f"🏥 Final center search: services={service_names}, gender={gender}, dob={date_of_birth}, address={address}")
        
        # Store center search parameters for processing node
        flow_manager.state["pending_center_search_params"] = {
            "selected_services": selected_services,
            "service_uuids": service_uuids,
            "service_names": service_names,
            "gender": gender,
            "date_of_birth": date_of_birth,
            "address": address
        }

        # Create message based on service count
        if len(service_names) == 1:
            center_search_status_text = f"Sto cercando centri sanitari a {address} che forniscano {service_names[0]}. Attendi..."
        else:
            center_search_status_text = f"Sto cercando centri sanitari a {address} che offrano tutti i servizi selezionati. Attendi..."

        # Create intermediate node with pre_actions for immediate TTS
        from flows.nodes.booking import create_center_search_processing_node
        return {
            "success": True,
            "message": f"Starting center search in {address}"
        }, create_center_search_processing_node(address, center_search_status_text)

    except Exception as e:
        logger.error(f"❌ Center search initialization error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Center search failed. Please try again."
        }, create_error_node("Center search failed. Please try again.")


async def perform_center_search_action(action: dict, flow_manager) -> None:
    """Custom action handler: speak TTS, run center search, and transition directly.

    Handles TTS internally via queue_frame instead of relying on tts_say action,
    so there's no ActionFinishedFrame dependency that can be dropped by interruptions.
    """
    from pipecat.frames.frames import TTSSpeakFrame

    try:
        tts_text = action.get("tts_text", "")
        if tts_text:
            await flow_manager.task.queue_frame(TTSSpeakFrame(text=tts_text))
        params = flow_manager.state.get("pending_center_search_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            await flow_manager.set_node_from_config(
                create_error_node("Missing center search parameters. Please start over.")
            )
            return

        selected_services = params["selected_services"]
        service_uuids = params["service_uuids"]
        service_names = params["service_names"]
        gender = params["gender"]
        date_of_birth = params["date_of_birth"]
        address = params["address"]

        current_radius = flow_manager.state.get("current_search_radius", None)
        dob_formatted = date_of_birth.replace("-", "")

        import asyncio
        loop = asyncio.get_event_loop()

        radius_display = current_radius if current_radius else "22 (default)"
        logger.info(f"🔍 Searching health centers with radius={radius_display}km for {len(service_uuids)} services in {address}")

        def _search_centers():
            result, error = retry_api_call(
                api_func=cerba_api.get_health_centers,
                max_retries=2,
                retry_delay=1.0,
                func_name=f"Health Center Search API (radius={radius_display}km)",
                health_services=service_uuids,
                gender=gender,
                date_of_birth=dob_formatted,
                address=address,
                radius=current_radius
            )
            if error:
                raise error
            return result

        try:
            health_centers = await loop.run_in_executor(None, _search_centers)
        except Exception as e:
            logger.error(f"❌ API error during center search: {e}")
            from flows.nodes.transfer import create_transfer_node
            await flow_manager.set_node_from_config(create_transfer_node())
            return

        flow_manager.state["search_radius_used"] = current_radius or 22
        logger.info(f"✅ Health center search completed: found {len(health_centers) if health_centers else 0} centers")

        if health_centers:
            flow_manager.state["final_health_centers"] = health_centers[:3]
            flow_manager.state["expanded_search"] = current_radius is not None and current_radius > 22

            from flows.nodes.booking import create_final_center_selection_node
            await flow_manager.set_node_from_config(
                create_final_center_selection_node(
                    health_centers[:3],
                    selected_services,
                    expanded_search=flow_manager.state.get("expanded_search", False)
                )
            )
        else:
            services_text = ", ".join(service_names)

            if current_radius is None:
                logger.info(f"⚠️ No centers at default 22km, asking user to expand to 42km")
                from flows.nodes.booking import create_ask_expand_radius_node
                await flow_manager.set_node_from_config(
                    create_ask_expand_radius_node(address, services_text, current_radius=22, next_radius=42)
                )
            elif current_radius == 42:
                logger.info(f"⚠️ No centers at 42km, asking user to expand to 62km")
                from flows.nodes.booking import create_ask_expand_radius_node
                await flow_manager.set_node_from_config(
                    create_ask_expand_radius_node(address, services_text, current_radius=42, next_radius=62)
                )
            else:
                logger.warning(f"⚠️ No centers found even at maximum 62km radius")
                from flows.nodes.booking import create_no_centers_node
                await flow_manager.set_node_from_config(
                    create_no_centers_node(address, services_text)
                )

    except Exception as e:
        logger.error(f"Center search action error: {e}")
        from flows.nodes.completion import create_error_node
        await flow_manager.set_node_from_config(
            create_error_node("Unable to find health centers. Please try again.")
        )


async def perform_center_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual center search after TTS message with interactive radius expansion

    NOTE: This is the LEGACY handler used by LLM function calling pattern.
    New code should use perform_center_search_action instead.

    Radius progression (interactive, user must confirm each expansion):
    - Default: 22km (API default, no radius param)
    - First expansion: 42km (if user agrees)
    - Second expansion: 62km (if user agrees)
    """
    try:
        # Get stored center search parameters
        params = flow_manager.state.get("pending_center_search_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": "Missing center search parameters"
            }, create_error_node("Missing center search parameters. Please start over.")

        # Extract parameters
        selected_services = params["selected_services"]
        service_uuids = params["service_uuids"]
        service_names = params["service_names"]
        gender = params["gender"]
        date_of_birth = params["date_of_birth"]
        address = params["address"]

        # Check if this is a retry with expanded radius
        current_radius = flow_manager.state.get("current_search_radius", None)  # None = default 22km

        # Format date for API
        dob_formatted = date_of_birth.replace("-", "")

        import asyncio
        loop = asyncio.get_event_loop()

        radius_display = current_radius if current_radius else "22 (default)"
        logger.info(f"🔍 Searching health centers with radius={radius_display}km for {len(service_uuids)} services in {address}")

        def _search_centers():
            """Search health centers with current radius"""
            result, error = retry_api_call(
                api_func=cerba_api.get_health_centers,
                max_retries=2,
                retry_delay=1.0,
                func_name=f"Health Center Search API (radius={radius_display}km)",
                health_services=service_uuids,
                gender=gender,
                date_of_birth=dob_formatted,
                address=address,
                radius=current_radius  # None = API default 22km
            )
            if error:
                raise error
            return result

        try:
            health_centers = await loop.run_in_executor(None, _search_centers)
        except Exception as e:
            logger.error(f"❌ API error during center search: {e}")
            from flows.nodes.transfer import create_transfer_node
            return {
                "success": False,
                "error": str(e),
                "message": "Mi dispiace, c'è un problema tecnico. Ti trasferisco a un operatore."
            }, create_transfer_node()

        # Store current radius for tracking
        flow_manager.state["search_radius_used"] = current_radius or 22

        logger.info(f"✅ Health center search completed: found {len(health_centers) if health_centers else 0} centers")

        if health_centers:
            # Centers found! Show them to user
            flow_manager.state["final_health_centers"] = health_centers[:3]
            flow_manager.state["expanded_search"] = current_radius is not None and current_radius > 22

            centers_data = []
            for center in health_centers[:3]:
                centers_data.append({
                    "name": center.name,
                    "city": center.city,
                    "address": center.address,
                    "uuid": center.uuid
                })

            # Create message based on whether search was expanded
            if flow_manager.state.get("expanded_search"):
                message = f"Found {len(centers_data)} health centers in a broader area around {address}"
            else:
                message = f"Found {len(centers_data)} health centers near {address}"

            result = {
                "success": True,
                "count": len(centers_data),
                "centers": centers_data,
                "services": service_names,
                "radius_km": current_radius or 22,
                "expanded_search": flow_manager.state.get("expanded_search", False),
                "message": message
            }

            from flows.nodes.booking import create_final_center_selection_node
            return result, create_final_center_selection_node(
                health_centers[:3],
                selected_services,
                expanded_search=flow_manager.state.get("expanded_search", False)
            )
        else:
            # No centers found - determine next action based on current radius
            services_text = ", ".join(service_names)

            if current_radius is None:
                # First search (22km default) failed - ask to expand to 42km
                logger.info(f"⚠️ No centers at default 22km, asking user to expand to 42km")
                from flows.nodes.booking import create_ask_expand_radius_node
                return {
                    "success": False,
                    "message": f"No centers found within 22km of {address}",
                    "next_radius": 42
                }, create_ask_expand_radius_node(address, services_text, current_radius=22, next_radius=42)

            elif current_radius == 42:
                # 42km search failed - ask to expand to 62km
                logger.info(f"⚠️ No centers at 42km, asking user to expand to 62km")
                from flows.nodes.booking import create_ask_expand_radius_node
                return {
                    "success": False,
                    "message": f"No centers found within 42km of {address}",
                    "next_radius": 62
                }, create_ask_expand_radius_node(address, services_text, current_radius=42, next_radius=62)

            else:
                # 62km search failed - maximum reached, end booking
                logger.warning(f"⚠️ No centers found even at maximum 62km radius")
                from flows.nodes.booking import create_no_centers_node
                return {
                    "success": False,
                    "message": f"No health centers found within 62km of {address} for: {services_text}",
                    "centers": [],
                    "max_radius_searched": 62
                }, create_no_centers_node(address, services_text)

    except Exception as e:
        logger.error(f"Final center search error: {e}")
        from flows.nodes.completion import create_error_node
        return {"success": False, "message": "Unable to find health centers"}, create_error_node("Unable to find health centers. Please try again.")


async def handle_radius_expansion_response(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle user's response to radius expansion question"""
    expand = args.get("expand", False)
    next_radius = args.get("next_radius", 42)

    if expand:
        # User wants to expand - set the new radius and re-trigger search
        flow_manager.state["current_search_radius"] = next_radius
        logger.info(f"📍 User agreed to expand search to {next_radius}km")

        # Get address for TTS message
        params = flow_manager.state.get("pending_center_search_params", {})
        address = params.get("address", "your location")

        # Create processing node with TTS message about expanded search
        tts_message = f"Sto cercando centri sanitari in un raggio di {next_radius} chilometri da {address}. Attendi..."

        from flows.nodes.booking import create_center_search_processing_node
        return {
            "success": True,
            "message": f"Expanding search to {next_radius}km"
        }, create_center_search_processing_node(address, tts_message)
    else:
        # User declined expansion - end the booking flow
        logger.info(f"❌ User declined radius expansion")
        params = flow_manager.state.get("pending_center_search_params", {})
        service_names = params.get("service_names", [])
        address = params.get("address", "your location")
        services_text = ", ".join(service_names)

        from flows.nodes.booking import create_no_centers_node
        return {
            "success": False,
            "message": "User declined to expand search radius"
        }, create_no_centers_node(address, services_text)


async def select_center_and_book(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle center selection and proceed to booking confirmation"""
    center_uuid = args.get("center_uuid", "").strip()
    
    if not center_uuid:
        return {"success": False, "message": "Please select a health center"}, None
    
    # Find the selected center from stored centers
    final_centers = flow_manager.state.get("final_health_centers", [])
    selected_center = None
    
    for center in final_centers:
        if center.uuid == center_uuid:
            selected_center = center
            break
    
    if not selected_center:
        return {"success": False, "message": "Health center not found"}, None
    
    # Store selected center
    flow_manager.state["selected_center"] = selected_center

    logger.info(f"🏥 Center selected: {selected_center.name} in {selected_center.city}")

    # ============================================================================
    # SORTING API INTEGRATION
    # Call the sorting API to get optimized service packages for this center
    # ============================================================================

    from services.sorting_api import call_sorting_api

    # Get required data from state
    selected_services = flow_manager.state.get("selected_services", [])
    patient_gender = flow_manager.state.get("patient_gender", "m")
    patient_dob = flow_manager.state.get("patient_dob", "")

    # Format DOB for API (remove dashes if present: "1980-04-13" -> "19800413")
    dob_formatted = patient_dob.replace("-", "") if patient_dob else "19800413"

    logger.info(f"🔄 Initiating sorting API call:")
    logger.info(f"   Center: {selected_center.name} ({selected_center.uuid})")
    logger.info(f"   Services: {len(selected_services)}")
    logger.info(f"   Gender: {patient_gender}, DOB: {dob_formatted}")

    # Log service details
    for idx, service in enumerate(selected_services):
        logger.debug(f"   [{idx}] {service.name} (sector: {service.sector})")

    try:
        # Call sorting API
        sorting_result = await call_sorting_api(
            health_center_uuid=selected_center.uuid,
            gender=patient_gender,
            date_of_birth=dob_formatted,
            selected_services=selected_services
        )

        if sorting_result.get("success"):
            # Store sorting API response in state for later use
            flow_manager.state["sorting_api_response"] = sorting_result["data"]
            flow_manager.state["sorting_api_package_detected"] = sorting_result.get("package_detected", False)

            # ================================================================
            # PARSE SORTING API RESPONSE INTO SERVICE GROUPS
            # ================================================================

            api_response_data = sorting_result["data"]

            try:
                logger.info("🔄 Parsing sorting API response into service groups...")

                service_groups = []

                if not api_response_data or not isinstance(api_response_data, list):
                    logger.error("❌ Invalid sorting API response format - expected list")
                    raise ValueError("Invalid response format")

                for group_idx, group_data in enumerate(api_response_data):
                    if not isinstance(group_data, dict):
                        logger.warning(f"⚠️ Group {group_idx} is not a dict, skipping")
                        continue

                    health_services = group_data.get("health_services", [])
                    is_group = group_data.get("group", False)

                    if not health_services:
                        logger.warning(f"⚠️ Group {group_idx} has no health_services, skipping")
                        continue

                    # Create HealthService objects for each service in the group
                    services = []
                    for svc_idx, svc in enumerate(health_services):
                        if not isinstance(svc, dict):
                            logger.warning(f"⚠️ Service {svc_idx} in group {group_idx} is not a dict, skipping")
                            continue

                        service_uuid = svc.get("uuid")
                        service_name = svc.get("name")
                        service_code = svc.get("health_service_code", "")

                        if not service_uuid or not service_name:
                            logger.warning(f"⚠️ Service {svc_idx} missing uuid or name, skipping")
                            continue

                        try:
                            service = HealthService(
                                uuid=service_uuid,
                                name=service_name,
                                code=service_code,
                                synonyms=[],
                                sector="health_services"  # Sector not needed after sorting
                            )
                            services.append(service)
                            logger.debug(f"   ✅ Parsed service: {service_name} ({service_uuid})")
                        except Exception as e:
                            logger.error(f"❌ Failed to create HealthService for {service_name}: {e}")
                            continue

                    if services:
                        service_groups.append({
                            "services": services,
                            "is_group": is_group
                        })
                        logger.debug(f"   ✅ Added group {group_idx}: {len(services)} service(s), is_group={is_group}")

                if not service_groups:
                    logger.error("❌ No valid service groups parsed from sorting API response")
                    raise ValueError("No valid groups")

                # Log parsed groups
                logger.info("📦 Parsed sorting API response:")
                logger.info(f"   Total groups: {len(service_groups)}")
                for idx, group in enumerate(service_groups):
                    logger.info(f"   Group {idx+1}: {len(group['services'])} service(s), is_group={group['is_group']}")
                    for svc in group['services']:
                        logger.info(f"      - {svc.name} (UUID: {svc.uuid}, Code: {svc.code})")

                # Store in state
                flow_manager.state["service_groups"] = service_groups

                # Use LLM to determine booking scenario
                try:
                    logger.info("🤖 Using LLM to interpret sorting API response...")

                    # Get OpenAI API key
                    openai_api_key = settings.api_keys["openai"]

                    # Call LLM interpretation service
                    llm_interpretation = await interpret_sorting_scenario(
                        api_response_data=api_response_data,
                        service_groups=service_groups,
                        openai_api_key=openai_api_key
                    )

                    # Extract LLM decision
                    booking_scenario = llm_interpretation["booking_scenario"]
                    reasoning = llm_interpretation["reasoning"]
                    num_appointments = llm_interpretation["num_appointments"]
                    service_summary = llm_interpretation["service_summary"]

                    # Log LLM decision
                    logger.info("🎯 LLM INTERPRETATION RESULT:")
                    logger.info(f"   Scenario: {booking_scenario.upper()}")
                    logger.info(f"   Reasoning: {reasoning}")
                    logger.info(f"   Appointments needed: {num_appointments}")
                    logger.info(f"   Summary: {service_summary}")

                    # Store LLM reasoning in state for debugging
                    flow_manager.state["llm_interpretation_reasoning"] = reasoning
                    flow_manager.state["llm_interpretation_summary"] = service_summary

                except Exception as e:
                    # LLM interpretation failed - raise error, no fallback
                    logger.error(f"❌ LLM interpretation failed: {e}")
                    from flows.nodes.completion import create_error_node
                    return {
                        "success": False,
                        "message": f"Failed to interpret booking scenario: {e}"
                    }, create_error_node("Technical issue processing your request. Please try again.")

                flow_manager.state["booking_scenario"] = booking_scenario
                flow_manager.state["current_group_index"] = 0  # Initialize for scenario 3

                # Log package detection for debugging
                if sorting_result.get("package_detected"):
                    logger.info("🎁 === PACKAGE DETECTED ===")
                    logger.info("   Services have been replaced with sorting API response")
                    logger.info(f"   Original services: {sorting_result.get('original_services', [])}")
                    logger.info(f"   Response services: {sorting_result.get('response_services', [])}")
                else:
                    logger.info("✅ No package detected - services confirmed as requested")

                logger.success("✅ Services replaced with sorting API response")

            except Exception as e:
                logger.error(f"❌ Failed to parse sorting API response: {e}")
                logger.exception("Full traceback:")
                logger.warning("⚠️ Falling back to legacy mode with original selected_services")
                flow_manager.state["booking_scenario"] = "legacy"
                flow_manager.state["service_groups"] = []

            logger.success("✅ Sorting API call completed successfully")

        else:
            # Sorting API failed - log but continue anyway (non-blocking)
            error_msg = sorting_result.get("error", "Unknown error")
            status_code = sorting_result.get("status_code", "N/A")

            logger.warning(f"⚠️ Sorting API call failed (non-blocking): {error_msg}")
            logger.warning(f"   Status code: {status_code}")
            logger.warning("   Continuing with booking flow without sorting optimization")

            # Store failure info in state for debugging
            flow_manager.state["sorting_api_error"] = error_msg
            flow_manager.state["sorting_api_success"] = False

    except Exception as e:
        # Unexpected error calling sorting API - log but continue (non-blocking)
        logger.error(f"❌ Unexpected error calling sorting API: {e}")
        logger.exception("Full traceback:")
        logger.warning("   Continuing with booking flow without sorting optimization")

        # Store error in state for debugging
        flow_manager.state["sorting_api_error"] = str(e)
        flow_manager.state["sorting_api_success"] = False

    # ============================================================================
    # END SORTING API INTEGRATION
    # ============================================================================

    from flows.nodes.booking import create_cerba_membership_node
    return {
        "success": True,
        "center_name": selected_center.name,
        "center_city": selected_center.city,
        "center_address": selected_center.address,
        "sorting_api_called": True
    }, create_cerba_membership_node()


async def check_cerba_membership_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Check if user is a Cerba member and transition to date/time collection"""
    is_member = args.get("is_cerba_member", False)
    
    # Store membership status for pricing calculations
    flow_manager.state["is_cerba_member"] = is_member
    
    logger.info(f"💳 Cerba membership status: {'Member' if is_member else 'Non-member'}")

    # Get service name for first appointment from state
    first_service_name = "your appointment"  # Default fallback

    # Try to get service name from various state locations
    if "service_groups" in flow_manager.state and flow_manager.state["service_groups"]:
        service_groups = flow_manager.state["service_groups"]
        booking_scenario = flow_manager.state.get("booking_scenario", "separate")

        if booking_scenario == "separate":
            # For separate appointments, get the first group's services
            first_group_services = service_groups[0]["services"]
            first_service_name = " più ".join([svc.name for svc in first_group_services])
        elif booking_scenario in ["bundle", "combined"]:
            # For bundle/combined, get all services in the first (and only) group
            first_group_services = service_groups[0]["services"]
            first_service_name = " più ".join([svc.name for svc in first_group_services])
    elif "selected_services" in flow_manager.state and flow_manager.state["selected_services"]:
        # Legacy fallback
        first_service = flow_manager.state["selected_services"][0]
        first_service_name = first_service.name

    logger.info(f"📅 Asking for date/time for first appointment: {first_service_name}")

    from flows.nodes.booking import create_collect_datetime_node
    return {
        "success": True,
        "is_cerba_member": is_member,
        "membership_status": "member" if is_member else "non-member"
    }, create_collect_datetime_node(first_service_name, False)



async def collect_datetime_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect preferred date and optional time preference for appointment"""
    # Use (value or "") to handle explicit None from LLM (dict.get default only works for missing keys)
    preferred_date = (args.get("preferred_date") or "").strip()
    preferred_time = (args.get("preferred_time") or "").strip()
    time_preference = (args.get("time_preference") or "any").strip().lower()
    first_available_mode = args.get("first_available_mode", False)

    if not preferred_date:
        return {"success": False, "message": "Please provide a date for your appointment"}, None

    try:
        # Handle "FIRST AVAILABLE" mode - USE TOMORROW'S DATE
        if first_available_mode:
            # Calculate tomorrow's date in Italian timezone
            from zoneinfo import ZoneInfo
            italian_tz = ZoneInfo("Europe/Rome")
            today = datetime.now(italian_tz)
            tomorrow = today + timedelta(days=1)
            tomorrow_date = tomorrow.strftime('%Y-%m-%d')

            # Override preferred_date with tomorrow's date
            preferred_date = tomorrow_date

            flow_manager.state["preferred_date"] = preferred_date
            flow_manager.state["first_available_mode"] = True
            flow_manager.state["start_time"] = None
            flow_manager.state["end_time"] = None
            flow_manager.state["time_preference"] = "any time"
            flow_manager.state["preferred_time"] = "first available"
            logger.info(f"🎯 FIRST AVAILABLE MODE ACTIVATED - searching from TOMORROW: {preferred_date}")

            # Get required data for slot search
            selected_services = flow_manager.state.get("selected_services", [])
            selected_center = flow_manager.state.get("selected_center")
            patient_gender = flow_manager.state.get("patient_gender", 'm')
            patient_dob = flow_manager.state.get("patient_dob", '1980-04-13')
            current_service_index = flow_manager.state.get("current_service_index", 0)
            current_service = selected_services[current_service_index] if selected_services else None
            service_name = current_service.name if current_service else "il servizio"

            # CRITICAL: Set pending_slot_search_params for perform_slot_search_and_transition
            flow_manager.state["pending_slot_search_params"] = {
                "selected_center": selected_center,
                "selected_services": selected_services,
                "preferred_date": preferred_date,
                "start_time": None,
                "end_time": None,
                "time_preference": "any time",
                "patient_gender": patient_gender,
                "patient_dob": patient_dob,
                "current_service_index": current_service_index,
                "current_service": current_service
            }
            logger.info(f"✅ Set pending_slot_search_params for first available mode")

            from flows.nodes.booking import create_slot_search_processing_node
            return {
                "success": True,
                "preferred_date": preferred_date,
                "time_preference": "first available",
                "first_available_mode": True
            }, create_slot_search_processing_node(
                service_name=service_name,
                tts_message=f"Cerco subito la prima disponibilità per {service_name}. Attendi un momento."
            )

        # Parse and validate date (for normal date selection, not first available)
        date_obj = datetime.strptime(preferred_date, "%Y-%m-%d")
        current_date = datetime.now().date()
        if date_obj.date() < current_date:
            return {"success": False, "message": f"Please select a future date after {current_date.strftime('%Y-%m-%d')}."}, None

        # Store date
        flow_manager.state["preferred_date"] = preferred_date

        # Handle time preferences - use database time format directly (no timezone conversion)

        if time_preference == "morning" or "morning" in preferred_time.lower():
            # 08:00-12:00 time range (database format)
            flow_manager.state["start_time"] = f"{preferred_date} 08:00:00+00"
            flow_manager.state["end_time"] = f"{preferred_date} 12:00:00+00"
            flow_manager.state["time_preference"] = "morning (08:00-12:00)"
            flow_manager.state["preferred_time"] = "morning"
            logger.info(f"📅 Date/Time collected: {preferred_date} - Morning (08:00-12:00)")
        elif time_preference == "afternoon" or "afternoon" in preferred_time.lower():
            # 12:00-19:00 time range (database format)
            flow_manager.state["start_time"] = f"{preferred_date} 12:00:00+00"
            flow_manager.state["end_time"] = f"{preferred_date} 19:00:00+00"
            flow_manager.state["time_preference"] = "afternoon (12:00-19:00)"
            flow_manager.state["preferred_time"] = "afternoon"
            logger.info(f"📅 Date/Time collected: {preferred_date} - Afternoon (12:00-19:00)")
        elif preferred_time and time_preference == "specific":
            # Parse specific time
            time_str = preferred_time.lower().replace("am", "").replace("pm", "").strip()
            if ":" in time_str:
                hour, minute = map(int, time_str.split(":"))
            else:
                hour = int(time_str)
                minute = 0

            # Handle PM times if needed
            if "pm" in preferred_time.lower() and hour != 12:
                hour += 12
            elif "am" in preferred_time.lower() and hour == 12:
                hour = 0

            # Use database time format directly (no timezone conversion)
            end_hour = (hour + 2) % 24  # Add 2 hours for slot window

            flow_manager.state["start_time"] = f"{preferred_date} {hour:02d}:{minute:02d}:00+00"
            flow_manager.state["end_time"] = f"{preferred_date} {end_hour:02d}:{minute:02d}:00+00"
            flow_manager.state["preferred_time"] = f"{hour:02d}:{minute:02d}"
            flow_manager.state["time_preference"] = f"specific time ({hour:02d}:{minute:02d})"
            logger.info(f"📅 Date/Time collected: {preferred_date} at {hour:02d}:{minute:02d}")
        else:
            # No specific time preference - use full day range
            flow_manager.state["start_time"] = None
            flow_manager.state["end_time"] = None
            flow_manager.state["time_preference"] = "any time"
            flow_manager.state["preferred_time"] = "any"
            logger.info(f"📅 Date collected: {preferred_date} - No time preference")

        # Get required data for slot search
        selected_services = flow_manager.state.get("selected_services", [])
        selected_center = flow_manager.state.get("selected_center")
        patient_gender = flow_manager.state.get("patient_gender", 'm')
        patient_dob = flow_manager.state.get("patient_dob", '1980-04-13')
        current_service_index = flow_manager.state.get("current_service_index", 0)
        current_service = selected_services[current_service_index] if selected_services else None
        service_name = current_service.name if current_service else "il servizio"
        time_pref = flow_manager.state.get("time_preference", "any time")

        # CRITICAL: Set pending_slot_search_params for perform_slot_search_and_transition
        flow_manager.state["pending_slot_search_params"] = {
            "selected_center": selected_center,
            "selected_services": selected_services,
            "preferred_date": preferred_date,
            "start_time": flow_manager.state.get("start_time"),
            "end_time": flow_manager.state.get("end_time"),
            "time_preference": time_pref,
            "patient_gender": patient_gender,
            "patient_dob": patient_dob,
            "current_service_index": current_service_index,
            "current_service": current_service
        }
        logger.info(f"✅ Set pending_slot_search_params for date: {preferred_date}, time_pref: {time_pref}")

        from flows.nodes.booking import create_slot_search_processing_node
        return {
            "success": True,
            "preferred_date": preferred_date,
            "time_preference": time_pref
        }, create_slot_search_processing_node(
            service_name=service_name,
            tts_message=f"Cerco gli appuntamenti disponibili per {service_name}. Attendi un momento."
        )

    except (ValueError, TypeError) as e:
        logger.error(f"Date/time parsing error: {e}")
        return {"success": False, "message": "Invalid date format. Please use a valid date like 'November 21' or '2025-11-21'"}, None


async def update_date_and_search_slots(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Update date preference and immediately search for slots - optimized for date selection flow"""
    # Use (value or "") to handle explicit None from LLM
    preferred_date = (args.get("preferred_date") or "").strip()
    time_preference = (args.get("time_preference") or "preserve_existing").strip()

    if not preferred_date:
        return {"success": False, "message": "Please provide a date for your appointment"}, None

    try:
        # Parse and validate date
        date_obj = datetime.strptime(preferred_date, "%Y-%m-%d")
        current_date = datetime.now().date()
        if date_obj.date() < current_date:
            return {"success": False, "message": f"Please select a future date after {current_date.strftime('%Y-%m-%d')}."}, None

        # Store new date
        flow_manager.state["preferred_date"] = preferred_date
        logger.info(f"📅 Updated preferred date to: {preferred_date}")

        # Handle time preference
        if time_preference == "preserve_existing":
            # Keep existing time preference if available
            existing_time_pref = flow_manager.state.get("time_preference", "any time")
            logger.info(f"🕐 Preserving existing time preference: {existing_time_pref}")
        else:
            # Check if this is an automatic search for 2nd+ service (separate scenario)
            auto_start_time = flow_manager.state.get("auto_start_time")

            if auto_start_time:
                # Automatic scheduling for 2nd+ services: start from calculated time
                flow_manager.state["start_time"] = f"{preferred_date} {auto_start_time}:00+00"
                flow_manager.state["end_time"] = None  # No end time constraint
                flow_manager.state["time_preference"] = f"any time from {auto_start_time} onwards"
                logger.info(f"⏰ AUTOMATIC TIME CONSTRAINT: Starting from {auto_start_time}")
            elif time_preference == "morning":
                flow_manager.state["start_time"] = f"{preferred_date} 08:00:00+00"
                flow_manager.state["end_time"] = f"{preferred_date} 12:00:00+00"
                flow_manager.state["time_preference"] = "morning (08:00-12:00)"
            elif time_preference == "afternoon":
                flow_manager.state["start_time"] = f"{preferred_date} 12:00:00+00"
                flow_manager.state["end_time"] = f"{preferred_date} 19:00:00+00"
                flow_manager.state["time_preference"] = "afternoon (12:00-19:00)"
            else:
                # 'any' preference - no time constraints
                flow_manager.state["start_time"] = None
                flow_manager.state["end_time"] = None
                flow_manager.state["time_preference"] = "any time"

            logger.info(f"🕐 Updated time preference to: {flow_manager.state.get('time_preference')}")

        # Immediately perform slot search with updated parameters
        selected_center = flow_manager.state.get("selected_center")
        selected_services = flow_manager.state.get("selected_services", [])
        start_time = flow_manager.state.get("start_time")
        end_time = flow_manager.state.get("end_time")
        patient_gender = flow_manager.state.get("patient_gender", 'm')
        patient_dob = flow_manager.state.get("patient_dob", "1980-04-13")

        if not selected_center or not selected_services:
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "Missing booking details"}, create_error_node("Missing booking details. Please start over.")

        # Format DOB for API
        dob_formatted = patient_dob.replace("-", "")

        # Determine service UUIDs - ALWAYS use current_group_index to get correct service
        current_group_index = flow_manager.state.get("current_group_index", 0)
        service_groups = flow_manager.state.get("service_groups", [])
        selected_services = flow_manager.state.get("selected_services", [])

        # Use service groups if available (bundle/combined/separate scenarios)
        if service_groups and current_group_index < len(service_groups):
            current_group = service_groups[current_group_index]
            current_group_services = current_group["services"]
            uuid_exam = [svc.uuid for svc in current_group_services]
            current_service_name = " più ".join([svc.name for svc in current_group_services])
            logger.info(f"🔍 DATE UPDATE: Using service group {current_group_index} - {current_service_name}")
        else:
            # Fallback to legacy single-service logic
            current_service_index = flow_manager.state.get("current_service_index", 0)
            if selected_services and current_service_index < len(selected_services):
                current_service = selected_services[current_service_index]
                uuid_exam = [current_service.uuid]
                current_service_name = current_service.name
                logger.info(f"🔍 DATE UPDATE: Using legacy service {current_service_index} - {current_service_name}")
            else:
                logger.error("❌ No service found for slot search!")
                return {"success": False, "message": "Service not found"}, None

        logger.info(f"🔍 Searching slots for {current_service_name} on {preferred_date}")

        # Call list_slot directly
        from services.slotAgenda import list_slot
        slots_response = list_slot(
            health_center_uuid=selected_center.uuid,
            date_search=preferred_date,
            uuid_exam=uuid_exam,
            gender=patient_gender,
            date_of_birth=dob_formatted,
            start_time=start_time,
            end_time=end_time
        )

        if slots_response and len(slots_response) > 0:
            # Store available slots
            flow_manager.state["available_slots"] = slots_response

            logger.success(f"✅ Found {len(slots_response)} available slots for {preferred_date}")

            # Create new slot selection node with the found slots
            from flows.nodes.booking import create_slot_selection_node

            user_preferred_date = flow_manager.state.get("preferred_date")
            time_preference_state = flow_manager.state.get("time_preference", "any time")

            # Get booking scenario from state
            booking_scenario = flow_manager.state.get("booking_scenario", "legacy")

            # Create display service object
            if booking_scenario != "legacy":
                display_service = type('DisplayService', (), {
                    'name': current_service_name,
                    'uuid': uuid_exam[0] if len(uuid_exam) == 1 else ','.join(uuid_exam),
                    'code': 'MULTI' if len(uuid_exam) > 1 else 'N/A'
                })()
            else:
                display_service = current_service

            return {
                "success": True,
                "slots_count": len(slots_response),
                "service_name": current_service_name,
                "message": f"Found {len(slots_response)} available slots for {preferred_date}"
            }, create_slot_selection_node(
                slots=slots_response,
                service=display_service,
                is_cerba_member=flow_manager.state.get("is_cerba_member", False),
                user_preferred_date=user_preferred_date,
                time_preference=time_preference_state
            )
        else:
            error_message = f"No available slots found for {current_service_name} on {preferred_date}"
            logger.warning(f"⚠️ {error_message}")

            # Go to no slots node with suggestion for different dates
            from flows.nodes.booking import create_no_slots_node
            return {
                "success": False,
                "message": error_message
            }, create_no_slots_node(preferred_date, flow_manager.state.get("time_preference", "any time"))

    except (ValueError, TypeError) as e:
        logger.error(f"Date parsing error: {e}")
        return {"success": False, "message": "Invalid date format. Please use format YYYY-MM-DD (e.g., '2025-11-26')"}, None


async def search_slots_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Search for available slots and transition to slot selection or direct booking"""
    try:
        # Get booking details from state
        selected_center = flow_manager.state.get("selected_center")
        selected_services = flow_manager.state.get("selected_services", [])
        preferred_date = flow_manager.state.get("preferred_date")
        start_time = flow_manager.state.get("start_time")  # Optional
        end_time = flow_manager.state.get("end_time")      # Optional
        time_preference = flow_manager.state.get("time_preference", "any time")
        patient_gender = flow_manager.state.get("patient_gender", 'm')
        patient_dob = flow_manager.state.get("patient_dob", '1980-04-13')
        current_service_index = flow_manager.state.get("current_service_index", 0)
        
        if not all([selected_center, selected_services, preferred_date]):
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "Missing booking information"}, create_error_node("Missing booking information. Please restart.")
        
        # Get current service being processed
        current_service = selected_services[current_service_index]

        # Store slot search parameters for processing node
        flow_manager.state["pending_slot_search_params"] = {
            "selected_center": selected_center,
            "selected_services": selected_services,
            "preferred_date": preferred_date,
            "start_time": start_time,
            "end_time": end_time,
            "time_preference": time_preference,
            "patient_gender": patient_gender,
            "patient_dob": patient_dob,
            "current_service_index": current_service_index,
            "current_service": current_service
        }

        # Create status message based on service count
        if len(selected_services) > 1:
            status_text = f"Ricerca di slot disponibili per {current_service.name}, servizio {current_service_index + 1} di {len(selected_services)}. Attendi..."
        else:
            status_text = f"Ricerca di slot disponibili per {current_service.name}. Attendi..."

        # Create intermediate node with pre_actions for immediate TTS
        from flows.nodes.booking import create_slot_search_processing_node
        return {
            "success": True,
            "message": f"Starting slot search for {current_service.name}"
        }, create_slot_search_processing_node(current_service.name, status_text)

    except Exception as e:
        logger.error(f"❌ Slot search initialization error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Slot search failed. Please try again."
        }, create_error_node("Slot search failed. Please try again.")


async def perform_slot_search_action(action: dict, flow_manager) -> None:
    """Custom action handler: speak TTS, run slot search, and transition directly.

    Handles TTS internally via queue_frame instead of relying on tts_say action,
    so there's no ActionFinishedFrame dependency that can be dropped by interruptions.
    """
    from pipecat.frames.frames import TTSSpeakFrame

    try:
        # Speak the TTS message directly (fire-and-forget into pipeline)
        tts_text = action.get("tts_text", "")
        if tts_text:
            await flow_manager.task.queue_frame(TTSSpeakFrame(text=tts_text))

        params = flow_manager.state.get("pending_slot_search_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            await flow_manager.set_node_from_config(
                create_error_node("Missing slot search parameters. Please start over.")
            )
            return

        # Delegate to the existing handler logic, then transition with its result
        result, next_node = await perform_slot_search_and_transition({}, flow_manager)
        await flow_manager.set_node_from_config(next_node)

    except Exception as e:
        logger.error(f"Slot search action error: {e}")
        from flows.nodes.completion import create_error_node
        await flow_manager.set_node_from_config(
            create_error_node("Failed to search slots. Please try again.")
        )


async def perform_slot_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual slot search after TTS message.

    NOTE: Also called internally by perform_slot_search_action.
    """
    try:
        # Get stored slot search parameters
        params = flow_manager.state.get("pending_slot_search_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": "Missing slot search parameters"
            }, create_error_node("Missing slot search parameters. Please start over.")

        # Extract parameters
        selected_center = params["selected_center"]
        selected_services = params["selected_services"]
        preferred_date = params["preferred_date"]
        start_time = params["start_time"]
        end_time = params["end_time"]
        time_preference = params["time_preference"]
        patient_gender = params["patient_gender"]
        patient_dob = params["patient_dob"]
        current_service_index = params["current_service_index"]
        current_service = params["current_service"]

        # Format date of birth for API (remove dashes)
        dob_formatted = patient_dob.replace("-", "")

        # === STEP 2.1: Determine booking scenario and service UUIDs ===
        logger.info("=" * 80)
        logger.info("🔍 SLOT SEARCH: Determining booking scenario...")
        logger.info("=" * 80)

        booking_scenario = flow_manager.state.get("booking_scenario", "legacy")
        service_groups = flow_manager.state.get("service_groups", [])

        logger.info(f"📋 Booking Scenario: {booking_scenario}")
        logger.info(f"📊 Service Groups Count: {len(service_groups)}")

        # Determine uuid_exam and service_name based on scenario
        uuid_exam = []
        current_service_name = ""

        if booking_scenario == "bundle":
            # Scenario 1: Multiple services bundled together (group=true)
            # Pass ALL UUIDs from the single group as a list
            logger.info("🎁 BUNDLE SCENARIO: Multiple services bundled together")

            if not service_groups or len(service_groups) == 0:
                logger.error("❌ Bundle scenario but no service groups found!")
                raise ValueError("Bundle scenario but no service groups")

            all_services = service_groups[0]["services"]
            uuid_exam = [svc.uuid for svc in all_services]
            current_service_name = " più ".join([svc.name for svc in all_services])

            logger.info(f"   Services in bundle: {len(all_services)}")
            for idx, svc in enumerate(all_services):
                logger.info(f"   [{idx+1}] {svc.name} (UUID: {svc.uuid})")
            logger.info(f"   UUID list for API: {uuid_exam}")

        elif booking_scenario == "combined":
            # Scenario 2: Services combined into single service (single group, group=false)
            # Pass single UUID
            logger.info("🔗 COMBINED SCENARIO: Services combined into one service")

            if not service_groups or len(service_groups) == 0:
                logger.error("❌ Combined scenario but no service groups found!")
                raise ValueError("Combined scenario but no service groups")

            single_service = service_groups[0]["services"][0]
            uuid_exam = [single_service.uuid]
            current_service_name = single_service.name

            logger.info(f"   Combined service: {single_service.name}")
            logger.info(f"   UUID: {single_service.uuid}")

        elif booking_scenario == "separate":
            # Scenario 3: Multiple groups, each needs separate booking (all group=false)
            # Pass current group's UUIDs
            logger.info("📦 SEPARATE SCENARIO: Multiple groups, booking separately")

            current_group_index = flow_manager.state.get("current_group_index", 0)
            logger.info(f"   Current group index: {current_group_index} of {len(service_groups)}")

            if current_group_index >= len(service_groups):
                logger.error(f"❌ Invalid group index {current_group_index} for {len(service_groups)} groups!")
                raise ValueError("Invalid group index")

            current_group = service_groups[current_group_index]
            current_group_services = current_group["services"]
            uuid_exam = [svc.uuid for svc in current_group_services]
            current_service_name = " più ".join([svc.name for svc in current_group_services])

            logger.info(f"   Services in current group: {len(current_group_services)}")
            for idx, svc in enumerate(current_group_services):
                logger.info(f"   [{idx+1}] {svc.name} (UUID: {svc.uuid})")
            logger.info(f"   UUID list for API: {uuid_exam}")

        else:  # legacy fallback
            # Legacy mode: Use original selected_services approach (pre-sorting API)
            logger.info("🔄 LEGACY SCENARIO: Using original selected_services")
            logger.warning("⚠️  Sorting API was not used or failed - falling back to legacy mode")

            if not selected_services or len(selected_services) == 0:
                logger.error("❌ Legacy mode but no selected_services found!")
                raise ValueError("No services available for booking")

            current_service = selected_services[current_service_index]
            uuid_exam = [current_service.uuid]
            current_service_name = current_service.name

            logger.info(f"   Service: {current_service.name}")
            logger.info(f"   UUID: {current_service.uuid}")

        logger.info(f"🎯 Final slot search parameters:")
        logger.info(f"   Service(s): {current_service_name}")
        logger.info(f"   UUID(s): {uuid_exam}")
        logger.info(f"   Date: {preferred_date}")
        logger.info(f"   Time preference: {time_preference}")
        logger.info(f"   Health Center: {selected_center.name}")
        logger.info(f"👤 Patient: Gender={patient_gender}, DOB={dob_formatted}")
        logger.info("=" * 80)

        # === STEP 2.2: Call slot search API with determined UUIDs (with retry) ===
        logger.info(f"🔍 Calling list_slot API with retry...")

        slots_response, slot_error = retry_api_call(
            api_func=list_slot,
            max_retries=2,
            retry_delay=1.0,
            func_name="Slot Search API",
            health_center_uuid=selected_center.uuid,
            date_search=preferred_date,
            uuid_exam=uuid_exam,  # List of 1 or more UUIDs based on scenario
            gender=patient_gender,
            date_of_birth=dob_formatted,
            start_time=start_time,  # Will be None if no time preference
            end_time=end_time       # Will be None if no time preference
        )

        # Handle API failure after all retries
        if slot_error:
            logger.error(f"❌ Slot search failed after 2 retries: {slot_error}")
            from flows.nodes.transfer import create_transfer_node
            return {
                "success": False,
                "error": str(slot_error),
                "message": "Mi dispiace, c'è un problema tecnico con il sistema di prenotazione. Ti trasferisco a un operatore che potrà aiutarti."
            }, create_transfer_node()

        # CRITICAL: Client-side filtering if start_time constraint exists
        # The API doesn't always respect start_time parameter, so we filter client-side
        if start_time and slots_response:
            from datetime import datetime
            original_count = len(slots_response)

            # Parse constraint time
            try:
                # start_time format: "2025-11-17 14:40:00+00"
                constraint_dt = datetime.fromisoformat(start_time.replace('+00', '+00:00'))

                # Filter slots: only keep slots that start at or after the constraint time
                filtered_slots = []
                for slot in slots_response:
                    slot_start = slot.get("start_time", "")
                    if slot_start:
                        slot_dt = datetime.fromisoformat(slot_start)
                        if slot_dt >= constraint_dt:
                            filtered_slots.append(slot)

                slots_response = filtered_slots
                logger.info(f"🕐 CLIENT-SIDE TIME FILTER:")
                logger.info(f"   Constraint: slots must start at or after {start_time}")
                logger.info(f"   Original slots: {original_count}")
                logger.info(f"   Filtered slots: {len(slots_response)}")
                logger.info(f"   Removed: {original_count - len(slots_response)} slots before constraint time")
            except Exception as e:
                logger.error(f"❌ Failed to apply client-side time filter: {e}")
                # Continue with unfiltered results if filtering fails

        if slots_response and len(slots_response) > 0:
            # Store available slots and current service name
            flow_manager.state["available_slots"] = slots_response
            flow_manager.state["current_service_index"] = current_service_index
            flow_manager.state["current_service_name"] = current_service_name  # Store for display

            logger.success(f"✅ Found {len(slots_response)} available slots for {current_service_name}")

            from flows.nodes.booking import create_slot_selection_node

            # Pass user preferences for smart filtering
            user_preferred_date = flow_manager.state.get("preferred_date")

            # Check if first available mode is active
            first_available_mode = flow_manager.state.get("first_available_mode", False)

            logger.info(f"🚀 SMART FILTERING: Calling slot selection with:")
            logger.info(f"   - user_preferred_date: {user_preferred_date}")
            logger.info(f"   - time_preference: {time_preference}")
            logger.info(f"   - first_available_mode: {first_available_mode}")
            logger.info(f"   - total_slots: {len(slots_response)}")

            # === STEP 2.3: Create service object for display ===
            # For bundle/combined/separate: create a dummy service object with combined name
            # For legacy: use the existing current_service
            if booking_scenario != "legacy":
                # Create a minimal service-like dict for slot selection node
                display_service = type('DisplayService', (), {
                    'name': current_service_name,
                    'uuid': uuid_exam[0] if len(uuid_exam) == 1 else ','.join(uuid_exam),
                    'code': 'MULTI' if len(uuid_exam) > 1 else service_groups[0]["services"][0].code if service_groups else 'N/A'
                })()
                logger.info(f"📋 Created display service object: {display_service.name}")
            else:
                display_service = current_service
                logger.info(f"📋 Using legacy service object: {display_service.name}")

            # CACHE ALL SLOTS FOR "SHOW MORE" REQUESTS (Hybrid First Available)
            if first_available_mode:
                flow_manager.state["cached_all_slots"] = slots_response
                flow_manager.state["cached_search_params"] = {
                    "preferred_date": user_preferred_date,
                    "time_preference": time_preference,
                    "service": {"name": current_service_name, "uuid": uuid_exam},  # Store as dict
                    "is_cerba_member": flow_manager.state.get("is_cerba_member", False)
                }
                logger.info(f"💾 CACHED: Stored {len(slots_response)} slots in state for 'show more' requests")

            # Check if this is automatic search for 2nd+ service
            is_automatic_search = False
            first_appointment_date = None

            if booking_scenario == "separate" and flow_manager.state.get("current_group_index", 0) > 0:
                auto_start_time = flow_manager.state.get("auto_start_time")
                if auto_start_time:
                    is_automatic_search = True
                    booked_slots = flow_manager.state.get("booked_slots", [])
                    if booked_slots:
                        first_appointment_date = booked_slots[0]["start_time"][:10]
                        logger.info(f"🤖 SLOT SELECTION: Automatic search for 2nd+ service, first appointment: {first_appointment_date}")

            return {
                "success": True,
                "slots_count": len(slots_response),
                "service_name": current_service_name,
                "time_preference": time_preference,
                "message": "Slot search completed"
            }, create_slot_selection_node(
                slots=slots_response,
                service=display_service,
                is_cerba_member=flow_manager.state.get("is_cerba_member", False),
                user_preferred_date=user_preferred_date,
                time_preference=time_preference,
                first_available_mode=first_available_mode,
                is_automatic_search=is_automatic_search,
                first_appointment_date=first_appointment_date
            )
        else:
            error_message = f"No available slots found for {current_service_name} on {preferred_date}"
            if time_preference != "any time":
                error_message += f" for {time_preference}"

            logger.warning(f"⚠️ {error_message}")

            # Check if this is a multi-service booking (2nd+ appointment)
            first_appointment_date = None
            is_automatic_search = False  # Flag to indicate if this is automatic search for 2nd+ service

            if booking_scenario == "separate" and flow_manager.state.get("current_group_index", 0) > 0:
                # This is 2nd+ service - get first appointment date constraint
                booked_slots = flow_manager.state.get("booked_slots", [])
                if booked_slots:
                    first_appointment_date = booked_slots[0]["start_time"][:10]  # Extract YYYY-MM-DD
                    logger.info(f"🚫 DATE CONSTRAINT: 2nd appointment must be on/after {first_appointment_date}")

                # Check if this was an automatic search (user didn't choose the date)
                auto_start_time = flow_manager.state.get("auto_start_time")
                if auto_start_time:
                    is_automatic_search = True
                    logger.info(f"🤖 AUTOMATIC SEARCH: This is 2nd+ service with auto date/time")

            from flows.nodes.booking import create_no_slots_node
            return {
                "success": False,
                "message": error_message
            }, create_no_slots_node(preferred_date, time_preference, first_appointment_date, is_automatic_search)
            
    except Exception as e:
        logger.error(f"Slot search error: {e}")
        from flows.nodes.completion import create_error_node
        return {"success": False, "message": "Failed to search for available slots"}, create_error_node("Failed to search slots. Please try again.")


async def select_slot_and_book(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle slot selection and proceed to booking creation"""
    # Use (value or "") to handle explicit None from LLM
    providing_entity_availability_uuid = (args.get("providing_entity_availability_uuid") or "").strip()
    selected_time_raw = (args.get("selected_time") or "").strip()
    selected_date = (args.get("selected_date") or "").strip()

    # Convert Italian words to numeric format if needed (e.g., "quattordici e quaranta" → "14:40")
    from utils.italian_time import italian_words_to_time
    numeric_time = italian_words_to_time(selected_time_raw)
    if numeric_time:
        selected_time = numeric_time
        logger.info(f"🔄 Converted Italian time '{selected_time_raw}' → '{selected_time}'")
    else:
        selected_time = selected_time_raw

    # COMPREHENSIVE DEBUG LOGGING FOR SLOT SELECTION
    logger.info("🔍 DEBUG: === SLOT SELECTION STARTED ===")
    logger.info(f"🔍 DEBUG: Args received: {args}")
    logger.info(f"🔍 DEBUG: providing_entity_availability_uuid = '{providing_entity_availability_uuid}'")
    logger.info(f"🔍 DEBUG: selected_time = '{selected_time}'")
    logger.info(f"🔍 DEBUG: selected_date = '{selected_date}'")

    if not providing_entity_availability_uuid:
        logger.error("❌ DEBUG: No providing_entity_availability_uuid provided!")
        return {"success": False, "message": "Please select a time slot"}, None

    # Find selected slot from available slots using both UUID and time for precise matching
    available_slots = flow_manager.state.get("available_slots", [])
    selected_slot = None

    logger.info(f"🔍 DEBUG: available_slots count = {len(available_slots) if available_slots else 0}")
    logger.info(f"🔍 DEBUG: available_slots = {available_slots}")

    logger.info(f"🔍 Searching for slot: UUID={providing_entity_availability_uuid}, Time={selected_time}, Date={selected_date}")

    # SMART LOOKUP: Check if we have time→UUID mapping from smart filtering
    from flows.nodes.booking import _current_session_slots
    if selected_time and selected_time in _current_session_slots:
        logger.info(f"🎯 SMART LOOKUP: Found slot by time '{selected_time}' in filtered session slots")
        selected_slot = _current_session_slots[selected_time]['original']
        logger.info(f"✅ Using smart-filtered slot: UUID={selected_slot.get('providing_entity_availability_uuid')}")
    else:
        logger.info(f"🔍 FALLBACK: Using traditional UUID/time matching in all {len(available_slots)} slots")

        for slot in available_slots:
            if slot.get("providing_entity_availability_uuid") == providing_entity_availability_uuid:
                # If we have time info, use it for precise matching
                if selected_time:
                    # IMPORTANT: Convert UTC database times to Italian local time for comparison
                    # because user selected Italian time but database has UTC times
                    from services.timezone_utils import utc_to_italian_display

                    italian_start = utc_to_italian_display(slot.get("start_time", ""))
                    italian_end = utc_to_italian_display(slot.get("end_time", ""))

                    try:
                        if not italian_start or not italian_end:
                            # Fallback to original method if conversion fails
                            logger.warning(f"⚠️ Timezone conversion failed for slot comparison, using UTC times")
                            start_time_str = slot.get("start_time", "").replace("T", " ").replace("+00:00", "")
                            end_time_str = slot.get("end_time", "").replace("T", " ").replace("+00:00", "")
                            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                            end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                        else:
                            # Use converted Italian times for comparison
                            start_dt = datetime.strptime(italian_start, "%Y-%m-%d %H:%M:%S")
                            end_dt = datetime.strptime(italian_end, "%Y-%m-%d %H:%M:%S")

                        # Format slot time to match selected_time format (H:MM - H:MM)
                        slot_time_full = f"{start_dt.strftime('%-H:%M')} - {end_dt.strftime('%-H:%M')}"
                        slot_time_start = start_dt.strftime('%-H:%M')

                        # Normalize times for comparison (remove leading zeros from both)
                        normalized_selected = selected_time.lstrip('0').replace(':0', ':') if selected_time.startswith('0') else selected_time
                        normalized_slot_start = slot_time_start

                        # Also try parsing selected_time to check if it falls within the slot range
                        selected_dt = None
                        try:
                            # Parse the selected time on the same date
                            selected_time_clean = selected_time.replace(':', ':').strip()
                            if ':' in selected_time_clean:
                                hour_min = selected_time_clean.split(':')
                                hour = int(hour_min[0])
                                minute = int(hour_min[1]) if len(hour_min) > 1 else 0
                                selected_dt = start_dt.replace(hour=hour, minute=minute)
                        except Exception:
                            pass

                        logger.info(f"🕐 Comparing times: slot='{slot_time_full}' (Italian) vs selected='{selected_time}' (normalized: '{normalized_selected}') vs slot_start='{normalized_slot_start}'")

                        # Match multiple ways:
                        # 1. Exact slot start time match (normalized)
                        # 2. Selected time falls within slot time range
                        # 3. Full format match
                        time_matches = (
                            normalized_slot_start == normalized_selected or  # Start time match
                            normalized_slot_start == selected_time or        # Direct match
                            slot_time_start == selected_time or              # Exact match
                            slot_time_full == selected_time or               # Full range match
                            (selected_dt and start_dt <= selected_dt < end_dt)  # Falls within range
                        )

                        if time_matches:
                            selected_slot = slot
                            logger.info(f"✅ Found exact time match: {slot_time_full}")
                            break
                    except Exception as e:
                        logger.warning(f"⚠️ Time parsing error for slot: {e}")
                        # Continue to check other slots
                        continue
                else:
                    # Fallback to first match by UUID (old behavior)
                    selected_slot = slot
                    logger.warning(f"⚠️ Using UUID-only matching (no time provided)")
                    break

    if not selected_slot:
        logger.error(f"❌ DEBUG: Slot not found: UUID={providing_entity_availability_uuid}, Time={selected_time}")

        # Debug: Log all available UUIDs for comparison
        logger.error("❌ DEBUG: Available slot UUIDs:")
        for i, slot in enumerate(available_slots):
            uuid = slot.get("providing_entity_availability_uuid", "MISSING_UUID")
            logger.error(f"   [{i}] UUID: {uuid}")

        # Provide more helpful error message with available times (in Italian local time)
        if available_slots:
            available_times = []
            from services.timezone_utils import utc_to_italian_display

            for slot in available_slots[:5]:  # Show first 5 available times
                try:
                    # Convert UTC to Italian time for user display
                    italian_start = utc_to_italian_display(slot.get("start_time", ""))
                    if italian_start:
                        start_dt = datetime.strptime(italian_start, "%Y-%m-%d %H:%M:%S")
                        available_times.append(start_dt.strftime('%-H:%M'))
                    else:
                        # Fallback to UTC if conversion fails
                        start_time_str = slot.get("start_time", "").replace("T", " ").replace("+00:00", "")
                        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                        available_times.append(start_dt.strftime('%-H:%M'))
                except:
                    continue

            if available_times:
                times_text = ", ".join(available_times)
                error_message = f"Sorry, that time is not available. Available times include: {times_text}. Please choose one of these times."
            else:
                error_message = "Sorry, that time slot is not available. Please choose from the available times shown above."
        else:
            error_message = "No available slots found. Please try a different date."

        return {"success": False, "message": error_message}, None

    # Store selected slot
    logger.info(f"🔍 DEBUG: STORING selected_slot in state: {selected_slot}")
    flow_manager.state["selected_slot"] = selected_slot
    logger.info(f"🔍 DEBUG: State after storing selected_slot: selected_slot key exists = {'selected_slot' in flow_manager.state}")

    # Extract pricing based on Cerba membership
    is_cerba_member = flow_manager.state.get("is_cerba_member", False)
    health_services = selected_slot.get("health_services", [])

    logger.info(f"🔍 DEBUG: is_cerba_member = {is_cerba_member}")
    logger.info(f"🔍 DEBUG: health_services = {health_services}")

    # CRITICAL: Extract and store price for this slot
    slot_price = 0
    if health_services:
        service = health_services[0]
        slot_price = service.get("cerba_card_price") if is_cerba_member else service.get("price")
        if slot_price is None:
            # Fallback: try to get price (non-Cerba) if cerba_card_price is None
            slot_price = service.get("price", 0)
        logger.info(f"💰 Price from slot health_services: {slot_price}")

    # Store price in state for booking
    flow_manager.state["slot_price"] = slot_price
    logger.info(f"💰 Stored slot_price in state: {slot_price}")

    logger.info(f"🎯 Slot selected: {selected_slot['start_time']} to {selected_slot['end_time']}")
    logger.info(f"🔍 DEBUG: === SLOT SELECTION COMPLETED SUCCESSFULLY ===")

    # NOTE: Slot reservation will happen in the next step (perform_slot_booking_and_transition)
    # This avoids double reservation attempts

    # Get required data for booking summary
    selected_services = flow_manager.state.get("selected_services", [])
    selected_center = flow_manager.state.get("selected_center")

    if not selected_services or not selected_center:
        return {"success": False, "message": "Missing booking information"}, None

    # Calculate total cost
    total_cost = 0
    selected_slots = [selected_slot]  # For now, single slot

    for service_data in health_services:
        price = service_data.get("cerba_card_price") if is_cerba_member else service_data.get("price", 0)
        total_cost += price

    # Store slot price for later use
    individual_slot_price = 0
    if health_services:
        service_data = health_services[0]
        individual_slot_price = service_data.get("cerba_card_price") if is_cerba_member else service_data.get("price", 0)

    flow_manager.state["slot_price"] = individual_slot_price

    # Go to slot booking creation first (this will reserve the slot)
    from flows.nodes.booking import create_slot_booking_processing_node

    # Debug logging to track slot booking data
    logger.info(f"🎯 Going to slot booking creation:")
    logger.info(f"   Selected slot time: {selected_slot['start_time']} to {selected_slot['end_time']}")
    logger.info(f"   Individual price: {individual_slot_price} euro")
    logger.info(f"   Total cost: {total_cost} euro")
    logger.info(f"   Center: {selected_center.name}")

    # Store slot booking parameters for the processing node
    flow_manager.state["pending_slot_booking_params"] = {
        "selected_slot": selected_slot,
        "selected_services": selected_services,
        "current_service_index": 0  # Single service booking
    }

    # Create status message for slot booking
    current_service_name = selected_services[0].name if selected_services else "your appointment"
    slot_creation_status_text = f"Prenotazione della fascia oraria per {current_service_name}. Attendi..."

    return {
        "success": True,
        "slot_time": f"{selected_slot['start_time']} to {selected_slot['end_time']}",
        "providing_entity_availability_uuid": providing_entity_availability_uuid,
        "message": f"Starting slot reservation for {current_service_name}"
    }, create_slot_booking_processing_node(current_service_name, slot_creation_status_text)


async def create_booking_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Create the slot reservation using create_slot function"""
    confirm_booking = args.get("confirm_booking", False)
    
    if not confirm_booking:
        from flows.nodes.completion import create_restart_node
        return {"success": False, "message": "Booking cancelled"}, create_restart_node()
    
    try:
        selected_slot = flow_manager.state.get("selected_slot")
        selected_services = flow_manager.state.get("selected_services", [])
        current_service_index = flow_manager.state.get("current_service_index", 0)
        
        if not selected_slot:
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "No slot selected"}, create_error_node("No slot selected.")
        
        # Store slot booking parameters for processing node
        flow_manager.state["pending_slot_booking_params"] = {
            "selected_slot": selected_slot,
            "selected_services": selected_services,
            "current_service_index": current_service_index
        }

        # Create status message
        current_service_name = selected_services[current_service_index].name if current_service_index < len(selected_services) else "your appointment"
        slot_creation_status_text = f"Prenotazione della fascia oraria per {current_service_name}. Attendi..."

        # Create intermediate node with pre_actions for immediate TTS
        from flows.nodes.booking import create_slot_booking_processing_node
        return {
            "success": True,
            "message": f"Starting slot booking for {current_service_name}"
        }, create_slot_booking_processing_node(current_service_name, slot_creation_status_text)

    except Exception as e:
        logger.error(f"❌ Slot booking initialization error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Slot booking failed. Please try again."
        }, create_error_node("Slot booking failed. Please try again.")


async def perform_slot_booking_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual slot booking after TTS message"""
    try:
        # Get stored slot booking parameters
        params = flow_manager.state.get("pending_slot_booking_params", {})
        if not params:
            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": "Missing slot booking parameters"
            }, create_error_node("Missing slot booking parameters. Please start over.")

        # Extract parameters
        selected_slot = params["selected_slot"]
        selected_services = params["selected_services"]
        current_service_index = params["current_service_index"]

        # Extract booking details
        start_time = selected_slot["start_time"]
        end_time = selected_slot["end_time"]
        providing_entity_availability = selected_slot["providing_entity_availability_uuid"]

        # Convert datetime format for create_slot function
        start_slot = start_time.replace("T", " ").replace("+00:00", "")
        end_slot = end_time.replace("T", " ").replace("+00:00", "")

        # DETAILED SLOT VERIFICATION LOGGING
        logger.info("=" * 80)
        logger.info("🔍 SLOT RESERVATION VERIFICATION")
        logger.info("=" * 80)
        logger.info(f"📋 Full Selected Slot Data:")
        logger.info(f"   Raw slot object: {selected_slot}")
        logger.info(f"   Start Time (original): {start_time}")
        logger.info(f"   End Time (original): {end_time}")
        logger.info(f"   PEA UUID: {providing_entity_availability}")
        logger.info(f"   Converted Start: {start_slot}")
        logger.info(f"   Converted End: {end_slot}")

        # Verify against available_slots to confirm LLM didn't hallucinate
        available_slots = flow_manager.state.get("available_slots", [])
        slot_found_in_available = False
        for idx, avail_slot in enumerate(available_slots):
            if (avail_slot.get("start_time") == start_time and
                avail_slot.get("providing_entity_availability_uuid") == providing_entity_availability):
                slot_found_in_available = True
                logger.info(f"✅ VERIFIED: Slot exists in available_slots at index {idx}")
                logger.info(f"   Available slot data: {avail_slot}")
                break

        if not slot_found_in_available:
            logger.error(f"❌ WARNING: Selected slot NOT found in available_slots!")
            logger.error(f"   This might be LLM hallucination!")
            logger.error(f"   Available slots count: {len(available_slots)}")
            logger.error(f"   First 3 available slots:")
            for idx, avail_slot in enumerate(available_slots[:3]):
                logger.error(f"      [{idx}] Start: {avail_slot.get('start_time')}, PEA: {avail_slot.get('providing_entity_availability_uuid')}")

        logger.info("=" * 80)
        logger.info(f"📝 Proceeding with slot reservation: {start_slot} to {end_slot}")

        # Call create_slot function with retry logic (this reserves the slot)
        def _create_slot_wrapper():
            return create_slot(start_slot, end_slot, providing_entity_availability)

        slot_result, slot_error = retry_api_call(
            api_func=_create_slot_wrapper,
            max_retries=2,
            retry_delay=1.0,
            func_name="Slot Reservation API"
        )

        # Handle API failure after all retries
        if slot_error:
            logger.error(f"❌ Slot reservation failed after 2 retries: {slot_error}")
            from flows.nodes.transfer import create_transfer_node
            return {
                "success": False,
                "error": str(slot_error),
                "message": "Mi dispiace, c'è un problema tecnico con la prenotazione. Ti trasferisco a un operatore."
            }, create_transfer_node()

        status_code, slot_uuid, created_at = slot_result

        if status_code == 200 or status_code == 201:

            # Store slot reservation information
            if "booked_slots" not in flow_manager.state:
                flow_manager.state["booked_slots"] = []

            # Get the current service name (which may be combined for bundle/separate scenarios)
            current_service_name = flow_manager.state.get("current_service_name", "")
            if not current_service_name:
                # Fallback to legacy behavior
                current_service_name = selected_services[current_service_index].name if selected_services else "Service"

            # Extract base price from current selected_slot
            is_cerba_member = flow_manager.state.get("is_cerba_member", False)
            health_services = selected_slot.get("health_services", [])

            slot_price = 0
            if health_services:
                service = health_services[0]
                base_price = service.get("cerba_card_price") if is_cerba_member else service.get("price", 0)
                if base_price is None:
                    base_price = service.get("price", 0)

                # BUNDLED SERVICE PRICE MULTIPLICATION
                # If this is a bundled group with multiple services, multiply the base price
                booking_scenario = flow_manager.state.get("booking_scenario", "legacy")
                service_groups = flow_manager.state.get("service_groups", [])
                current_group_index = flow_manager.state.get("current_group_index", 0)

                if booking_scenario in ["bundle", "separate"] and service_groups and current_group_index < len(service_groups):
                    current_group = service_groups[current_group_index]
                    is_bundled = current_group.get("is_group", False)
                    service_count = len(current_group["services"])

                    if is_bundled and service_count > 1:
                        # Multiply base price by number of services in bundle
                        slot_price = base_price * service_count
                        logger.info(f"💰 BUNDLED PRICING: {base_price}€ × {service_count} services = {slot_price}€")
                    else:
                        slot_price = base_price
                        logger.info(f"💰 SINGLE SERVICE PRICING: {slot_price}€")
                else:
                    # Legacy scenario or no bundling
                    slot_price = base_price
                    logger.info(f"💰 LEGACY PRICING: {slot_price}€")

            logger.info(f"📝 Storing booked slot: {current_service_name} at {start_time} - Price: {slot_price}€")

            flow_manager.state["booked_slots"].append({
                "slot_uuid": slot_uuid,
                "service_name": current_service_name,  # Use combined name for bundle/separate
                "start_time": start_time,
                "end_time": end_time,
                "price": slot_price  # Use extracted price, not cached state["slot_price"]
            })

            logger.success(f"✅ Slot reserved successfully: {slot_uuid}")

            # === STEP 3.1: Check for multi-group booking (Scenario 3: Separate) ===
            logger.info("=" * 80)
            logger.info("🔍 BOOKING COMPLETION: Checking for more groups to book...")
            logger.info("=" * 80)

            booking_scenario = flow_manager.state.get("booking_scenario", "legacy")
            service_groups = flow_manager.state.get("service_groups", [])
            current_group_index = flow_manager.state.get("current_group_index", 0)

            logger.info(f"📋 Booking Scenario: {booking_scenario}")
            logger.info(f"📊 Total Service Groups: {len(service_groups)}")
            logger.info(f"📍 Current Group Index: {current_group_index}")

            # Scenario 3 (Separate): Check if more groups remain
            if booking_scenario == "separate" and current_group_index + 1 < len(service_groups):
                # More groups to book - automatically proceed with next service
                next_group_index = current_group_index + 1
                flow_manager.state["current_group_index"] = next_group_index

                next_group = service_groups[next_group_index]
                next_group_services = next_group["services"]
                next_group_service_names = " più ".join([svc.name for svc in next_group_services])

                total_groups = len(service_groups)
                progress_text = f"Appointment {next_group_index + 1} of {total_groups}"

                logger.info(f"📦 MULTI-GROUP BOOKING: Moving to next group")
                logger.info(f"   Next group index: {next_group_index}")
                logger.info(f"   Next group services: {next_group_service_names}")
                logger.info(f"   Progress: {progress_text}")
                logger.info(f"   Remaining groups: {total_groups - next_group_index}")

                # Get the first booked slot to calculate automatic date/time
                booked_slots = flow_manager.state.get("booked_slots", [])
                if booked_slots:
                    first_slot = booked_slots[0]
                    first_slot_end_time = first_slot.get("end_time")  # UTC time string

                    # Calculate automatic date/time: same date, +1 hour from first service end
                    try:
                        from datetime import datetime, timedelta
                        from zoneinfo import ZoneInfo

                        # Parse first service end time (UTC)
                        first_end_dt = datetime.fromisoformat(first_slot_end_time.replace('Z', '+00:00'))

                        # Add 1 hour buffer
                        auto_start_dt = first_end_dt + timedelta(hours=1)

                        # Store automatic date/time in state for slot search
                        auto_date = auto_start_dt.strftime("%Y-%m-%d")
                        auto_time = auto_start_dt.strftime("%H:%M")

                        # Convert to Italian time for user display
                        italian_tz = ZoneInfo("Europe/Rome")
                        auto_start_italian = auto_start_dt.astimezone(italian_tz)
                        auto_time_italian = auto_start_italian.strftime("%H:%M")

                        flow_manager.state["auto_date"] = auto_date
                        flow_manager.state["auto_start_time"] = auto_time
                        flow_manager.state["preferred_date"] = auto_date  # For slot search
                        flow_manager.state["time_preference"] = "any"  # Search any time after auto start

                        # CRITICAL: Set start_time in the correct format for the slot API
                        flow_manager.state["start_time"] = f"{auto_date} {auto_time}:00+00"
                        flow_manager.state["end_time"] = None  # No end time constraint

                        # CRITICAL FIX: Update pending_slot_search_params with the new start_time constraint
                        # This ensures perform_slot_search_and_transition uses the updated time constraint
                        if "pending_slot_search_params" in flow_manager.state:
                            flow_manager.state["pending_slot_search_params"]["start_time"] = f"{auto_date} {auto_time}:00+00"
                            flow_manager.state["pending_slot_search_params"]["end_time"] = None
                            flow_manager.state["pending_slot_search_params"]["preferred_date"] = auto_date
                            logger.info(f"   ✅ Updated pending_slot_search_params with time constraint")

                        logger.info(f"⏰ AUTOMATIC SCHEDULING:")
                        logger.info(f"   First service ended at: {first_slot_end_time}")
                        logger.info(f"   Auto date: {auto_date}")
                        logger.info(f"   Auto start time (UTC): {auto_time}")
                        logger.info(f"   Auto start time (Italian): {auto_time_italian}")
                        logger.info(f"   ✅ Set start_time constraint: {auto_date} {auto_time}:00+00")
                        logger.info("=" * 80)

                    except Exception as e:
                        logger.error(f"❌ Failed to calculate automatic date/time: {e}")
                        # Fallback: use first service date
                        auto_date = first_slot.get("start_time", "").split("T")[0]
                        flow_manager.state["auto_date"] = auto_date
                        flow_manager.state["preferred_date"] = auto_date
                        flow_manager.state["time_preference"] = "any"

                # Clear slot-related state for next booking
                flow_manager.state.pop("available_slots", None)
                flow_manager.state.pop("cached_all_slots", None)
                flow_manager.state.pop("cached_search_params", None)
                flow_manager.state.pop("first_available_mode", None)

                # Create automatic slot search node (skip date/time collection)
                from flows.nodes.booking import create_automatic_slot_search_node

                # Enhanced user message with clear service indicators
                just_booked_ordinal = current_group_index + 1  # The appointment we just booked (1-based)
                next_appointment_ordinal = next_group_index + 1  # The appointment we're about to book (1-based)
                user_message = f"Perfetto! L'appuntamento {just_booked_ordinal} di {total_groups} è stato prenotato con successo: {current_service_name}. Ora prenoto l'appuntamento {next_appointment_ordinal} di {total_groups}: {next_group_service_names}. Cerco orari disponibili nello stesso giorno, a partire da 1 ora dopo il primo appuntamento."


                return {
                    "success": True,
                    "booking_id": slot_uuid,
                    "has_more_groups": True,
                    "next_group_services": next_group_service_names,
                    "progress": progress_text,
                    "current_group": next_group_index + 1,
                    "total_groups": total_groups,
                    "message": user_message
                }, create_automatic_slot_search_node(
                    service_name=next_group_service_names,
                    tts_message=user_message
                )

            # Legacy scenario: Check if there are more services to book (old behavior)
            elif booking_scenario == "legacy" and current_service_index + 1 < len(selected_services):
                # More services to book - continue with slot creation for next service
                flow_manager.state["current_service_index"] = current_service_index + 1
                next_service = selected_services[current_service_index + 1]

                logger.info(f"🔄 LEGACY: Moving to next service")
                logger.info(f"   Next service: {next_service.name}")
                logger.info(f"   Remaining services: {len(selected_services) - current_service_index - 1}")

                from flows.nodes.booking import create_collect_datetime_node
                return {
                    "success": True,
                    "booking_id": slot_uuid,
                    "service_name": selected_services[current_service_index].name,
                    "has_more_services": True,
                    "next_service": next_service.name,
                    "remaining_services": len(selected_services) - current_service_index - 1
                }, create_collect_datetime_node(next_service.name, True)  # Ask for time for next service

            else:
                # All groups/services booked - show booking summary
                logger.info(f"🎯 All bookings completed, showing booking summary")
                logger.info("=" * 80)

                # Get required data for booking summary
                selected_services = flow_manager.state.get("selected_services", [])
                selected_center = flow_manager.state.get("selected_center")
                is_cerba_member = flow_manager.state.get("is_cerba_member", False)

                # Calculate total cost
                total_cost = 0
                selected_slots = flow_manager.state.get("booked_slots", [])

                for slot_data in selected_slots:
                    total_cost += slot_data.get("price", 0)

                from flows.nodes.booking import create_booking_summary_confirmation_node
                return {
                    "success": True,
                    "slot_id": slot_uuid,
                    "all_slots_created": True,
                    "total_slots": len(flow_manager.state["booked_slots"]),
                    "message": "Perfect! Your time slot has been reserved."
                }, create_booking_summary_confirmation_node(selected_services, selected_slots, selected_center, total_cost, is_cerba_member)
        else:
            # Handle specific error cases
            error_msg = f"Slot reservation failed: HTTP {status_code}"
            logger.error(error_msg)

            # For status code errors, assume slot is no longer available
            if status_code == 409:
                # Slot no longer available - refresh slots
                current_service_name = selected_services[current_service_index].name
                from flows.nodes.booking import create_slot_refresh_node
                return {
                    "success": False,
                    "message": f"Sorry, that time slot is no longer available for {current_service_name}. Let me show you updated available times.",
                    "error_type": "slot_unavailable"
                }, create_slot_refresh_node(current_service_name)
            else:
                # Other booking error
                from flows.nodes.completion import create_error_node
                return {
                    "success": False,
                    "message": "There was an issue creating your booking. Please try again.",
                    "error_type": "booking_failed"
                }, create_error_node("Booking failed. Please try again.")
            
    except Exception as e:
        logger.error(f"Booking creation error: {e}")
        from flows.nodes.completion import create_error_node
        return {"success": False, "message": "Failed to create booking"}, create_error_node("Booking creation failed. Please try again.")


async def perform_slot_booking_action(action: dict, flow_manager) -> None:
    """Custom action handler: speak TTS, run slot booking, and transition directly.

    Handles TTS internally via queue_frame instead of relying on tts_say action,
    so there's no ActionFinishedFrame dependency that can be dropped by interruptions.
    """
    from pipecat.frames.frames import TTSSpeakFrame

    try:
        tts_text = action.get("tts_text", "")
        if tts_text:
            await flow_manager.task.queue_frame(TTSSpeakFrame(text=tts_text))

        # Delegate to existing handler
        result, next_node = await perform_slot_booking_and_transition({}, flow_manager)
        await flow_manager.set_node_from_config(next_node)

    except Exception as e:
        logger.error(f"Slot booking action error: {e}")
        from flows.nodes.completion import create_error_node
        await flow_manager.set_node_from_config(
            create_error_node("Slot booking failed. Please try again.")
        )


async def handle_booking_modification(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle booking cancellation/modification"""
    action = args.get("action", "").lower()
    
    if action == "cancel":
        booked_slots = flow_manager.state.get("booked_slots", [])
        
        if not booked_slots:
            return {"success": False, "message": "No bookings to cancel"}, None
        
        try:
            # Cancel all booked slots
            cancelled_slots = []
            for slot in booked_slots:
                slot_uuid = slot["slot_uuid"]
                delete_response = delete_slot(slot_uuid)
                
                if delete_response.status_code == 200:
                    cancelled_slots.append(slot)
                    logger.info(f"🗑️ Cancelled booking: {slot_uuid}")
            
            # Clear booked slots from state
            flow_manager.state["booked_slots"] = []
            
            from flows.nodes.completion import create_restart_node
            return {
                "success": True,
                "cancelled_count": len(cancelled_slots),
                "message": f"Successfully cancelled {len(cancelled_slots)} booking(s)"
            }, create_restart_node()
            
        except Exception as e:
            logger.error(f"Cancellation error: {e}")
            return {"success": False, "message": "Failed to cancel bookings"}, None
    
    elif action == "change_time":
        # Cancel existing bookings first, then redirect to date/time collection
        booked_slots = flow_manager.state.get("booked_slots", [])
        if booked_slots:
            for slot in booked_slots:
                try:
                    delete_slot(slot["slot_uuid"])
                    logger.info(f"🗑️ Cancelled booking for rescheduling: {slot['slot_uuid']}")
                except:
                    pass
            flow_manager.state["booked_slots"] = []
        
        from flows.nodes.booking import create_collect_datetime_node
        return {
            "success": True,
            "message": "Let's reschedule your appointment"
        }, create_collect_datetime_node()
    
    else:
        return {"success": False, "message": "Please specify 'cancel' or 'change_time'"}, None


async def confirm_booking_summary_and_proceed(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle booking summary confirmation and proceed accordingly"""
    action = args.get("action", "")

    if action == "proceed":
        logger.info("✅ Booking summary confirmed, proceeding to patient lookup and personal information collection")

        # PHONE + DOB LOOKUP LOGIC
        # Get phone number from Talkdesk bridge
        caller_phone = flow_manager.state.get("caller_phone_from_talkdesk", "")
        # Get DOB from patient info collection (already collected earlier in flow)
        patient_dob = flow_manager.state.get("patient_dob", "")

        logger.info(f"🔍 Attempting patient lookup with phone and DOB")

        # Try to find existing patient
        if caller_phone and patient_dob:
            from services.patient_lookup import lookup_by_phone_and_dob, populate_patient_state

            # Perform lookup
            found_patient = lookup_by_phone_and_dob(caller_phone, patient_dob)

            if found_patient:
                # Patient found in database
                logger.success(f"✅ Patient found in database: {found_patient.get('first_name', '')} {found_patient.get('last_name', '')}")

                # Populate flow state with patient data
                populate_patient_state(flow_manager, found_patient)

                # Transition to patient summary confirmation
                from flows.nodes.patient_summary import create_patient_summary_node
                return {
                    "success": True,
                    "message": "Patient found in database, showing summary for confirmation",
                    "patient_found": True
                }, create_patient_summary_node(found_patient)
            else:
                # Patient not found, proceed with normal name collection
                logger.info("❌ Patient not found in database, proceeding with normal data collection")
        else:
            # Missing phone or DOB for lookup
            logger.warning(f"⚠️ Cannot perform patient lookup: missing phone ({bool(caller_phone)}) or DOB ({bool(patient_dob)})")

        # Fallback: Normal full name collection flow for new patients
        from flows.nodes.patient_details import create_collect_full_name_node
        return {
            "success": True,
            "message": "Booking confirmed, starting personal information collection",
            "patient_found": False
        }, create_collect_full_name_node()

    elif action == "cancel":
        logger.info("❌ Patient cancelled booking due to cost/preferences")

        # Go to restart/cancellation flow
        from flows.nodes.completion import create_restart_node
        return {
            "success": False,
            "message": "Booking cancelled as requested"
        }, create_restart_node()

    elif action == "change":
        logger.info("🔄 Patient wants to change booking details")

        # Instead of restarting completely, go back to slot selection with available slots
        # This preserves the service, center, and date but allows time change
        available_slots = flow_manager.state.get("available_slots", [])
        selected_services = flow_manager.state.get("selected_services", [])
        current_service_index = flow_manager.state.get("current_service_index", 0)

        if available_slots and selected_services:
            current_service = selected_services[current_service_index]
            user_preferred_date = flow_manager.state.get("preferred_date")
            time_preference = flow_manager.state.get("time_preference", "any time")

            logger.info(f"🔄 Returning to slot selection for {current_service.name}")

            from flows.nodes.booking import create_slot_selection_node
            return {
                "success": True,
                "message": "Let's find you a different time slot"
            }, create_slot_selection_node(
                slots=available_slots,
                service=current_service,
                is_cerba_member=flow_manager.state.get("is_cerba_member", False),
                user_preferred_date=user_preferred_date,
                time_preference=time_preference
            )
        else:
            # Fallback: go back to date/time selection
            logger.warning("⚠️ No available slots stored, going back to date selection")
            from flows.nodes.booking import create_collect_datetime_node
            return {
                "success": False,
                "message": "Let's choose a different date and time"
            }, create_collect_datetime_node()

    else:
        return {"success": False, "message": "Please let me know if you want to proceed, cancel, or change the booking"}, None


async def show_more_same_day_slots_handler(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Show additional slots on the same day as the earliest slot
    Uses cached slots from first available mode search
    """
    try:
        logger.info("🔍 SHOW MORE SAME DAY: User requested additional slots on same day")

        # Get cached data
        cached_slots = flow_manager.state.get("cached_all_slots", [])
        cached_params = flow_manager.state.get("cached_search_params", {})

        logger.info(f"🔍 DEBUG: Found {len(cached_slots)} cached slots")

        if not cached_slots:
            logger.error("❌ No cached slots found - first available mode may not have been used")
            return {
                "success": False,
                "message": "No cached slot data available. Please search for slots first."
            }, None

        # Parse all cached slots and find the earliest date
        from datetime import datetime
        from zoneinfo import ZoneInfo

        all_slots_with_dt = []
        for slot in cached_slots:
            try:
                # Parse slot start_time (API uses 'start_time' field, not 'datetime')
                slot_datetime_str = slot.get('start_time', '')
                if not slot_datetime_str:
                    logger.warning(f"⚠️ Slot missing 'start_time' field")
                    continue

                slot_dt = datetime.fromisoformat(slot_datetime_str.replace('Z', '+00:00'))
                slot_dt_local = slot_dt.astimezone(ZoneInfo("Europe/Rome"))

                all_slots_with_dt.append({
                    'slot_data': slot,
                    'datetime': slot_dt_local,
                    'date_key': slot_dt_local.strftime('%Y-%m-%d')
                })
            except Exception as e:
                logger.warning(f"⚠️ Failed to parse slot datetime: {e}")
                continue

        if not all_slots_with_dt:
            logger.error("❌ Could not parse any cached slots")
            return {
                "success": False,
                "message": "Error processing cached slots"
            }, None

        # Sort by datetime and get the earliest date
        all_slots_with_dt.sort(key=lambda x: x['datetime'])
        earliest_date = all_slots_with_dt[0]['date_key']

        # Filter to only slots on that earliest date
        same_day_slots = [s['slot_data'] for s in all_slots_with_dt if s['date_key'] == earliest_date]

        logger.info(f"📅 Found {len(same_day_slots)} total slots on earliest day ({earliest_date})")
        logger.info(f"🔍 DEBUG: all_slots_with_dt count: {len(all_slots_with_dt)}")
        logger.info(f"🔍 DEBUG: Parsed dates: {[s['date_key'] for s in all_slots_with_dt]}")

        if len(same_day_slots) <= 1:
            # Only 1 slot on that day (the one already shown)
            return {
                "success": False,
                "message": f"That is the only available slot on {earliest_date}. Would you like to search for a different date?"
            }, None

        # Turn off first available mode and show all slots on that day
        flow_manager.state["first_available_mode"] = False

        from flows.nodes.booking import create_slot_selection_node
        from models.requests import HealthService

        # Reconstruct service from cached params
        service_data = cached_params.get("service", {})
        current_service = HealthService(**service_data) if isinstance(service_data, dict) else service_data

        logger.success(f"✅ Showing all {len(same_day_slots)} slots on {earliest_date}")

        return {
            "success": True,
            "message": f"Showing all available slots on {earliest_date}",
            "slots_count": len(same_day_slots)
        }, create_slot_selection_node(
            slots=same_day_slots,
            service=current_service,
            is_cerba_member=cached_params.get("is_cerba_member", False),
            user_preferred_date=earliest_date,
            time_preference=cached_params.get("time_preference", "any time"),
            first_available_mode=False  # Show all slots now
        )

    except Exception as e:
        logger.error(f"❌ Error showing more same day slots: {e}")
        return {
            "success": False,
            "message": "An error occurred while retrieving additional slots"
        }, None


async def search_different_date_handler(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Search for slots on a specific different date requested by user
    Performs a new API call with the requested date
    """
    try:
        new_date = args.get("new_date")
        time_preference = args.get("time_preference", "any time")

        if not new_date:
            logger.error("❌ No date provided for search")
            return {
                "success": False,
                "message": "Please specify which date you'd like to search"
            }, None

        logger.info(f"🔍 SEARCH DIFFERENT DATE: User requested slots on {new_date} with preference '{time_preference}'")

        # Clear first available mode
        flow_manager.state["first_available_mode"] = False
        flow_manager.state["preferred_date"] = new_date
        flow_manager.state["time_preference"] = time_preference

        # Get current service info - ALWAYS use current_group_index
        current_group_index = flow_manager.state.get("current_group_index", 0)
        service_groups = flow_manager.state.get("service_groups", [])
        selected_services = flow_manager.state.get("selected_services", [])
        selected_center = flow_manager.state.get("selected_center")

        if not selected_center:
            logger.error("❌ No health center selected")
            return {
                "success": False,
                "message": "Health center information not found. Please start the booking process again."
            }, None

        # Determine which service(s) to search slots for
        if service_groups and current_group_index < len(service_groups):
            # Use service groups if available (bundle/combined/separate scenarios)
            current_group = service_groups[current_group_index]
            current_group_services = current_group["services"]
            uuid_exam = [svc.uuid for svc in current_group_services]
            current_service_name = " più ".join([svc.name for svc in current_group_services])
            # Use first service from group as display service
            current_service = current_group_services[0]
            logger.info(f"🔍 DIFFERENT DATE: Using service group {current_group_index} - {current_service_name}")
        else:
            # Fallback to legacy single-service logic
            current_service_index = flow_manager.state.get("current_service_index", 0)
            if not selected_services or current_service_index >= len(selected_services):
                logger.error("❌ No service information found")
                return {
                    "success": False,
                    "message": "Service information not found. Please start the booking process again."
                }, None
            current_service = selected_services[current_service_index]
            uuid_exam = [current_service.uuid]
            current_service_name = current_service.name
            logger.info(f"🔍 DIFFERENT DATE: Using legacy service {current_service_index} - {current_service_name}")

        # Perform new slot search with the requested date
        logger.info(f"🔎 Searching slots for {current_service_name} on {new_date}")

        slots_response = list_slot(
            health_center_uuid=selected_center.uuid,
            date_search=new_date,
            uuid_exam=uuid_exam,  # Use group-aware UUID list
            gender=flow_manager.state.get("patient_gender", "m"),
            date_of_birth=flow_manager.state.get("patient_dob", "1980-04-13")
        )

        if slots_response and len(slots_response) > 0:
            flow_manager.state["available_slots"] = slots_response
            logger.success(f"✅ Found {len(slots_response)} slots on {new_date}")

            from flows.nodes.booking import create_slot_selection_node

            return {
                "success": True,
                "message": f"Found {len(slots_response)} available slots on {new_date}",
                "slots_count": len(slots_response)
            }, create_slot_selection_node(
                slots=slots_response,
                service=current_service,
                is_cerba_member=flow_manager.state.get("is_cerba_member", False),
                user_preferred_date=new_date,
                time_preference=time_preference,
                first_available_mode=False
            )
        else:
            logger.warning(f"⚠️ No slots found on {new_date}")
            from flows.nodes.booking import create_no_slots_node
            return {
                "success": False,
                "message": f"No available slots found on {new_date}"
            }, create_no_slots_node(new_date, time_preference)

    except Exception as e:
        logger.error(f"❌ Error searching different date: {e}")
        return {
            "success": False,
            "message": "An error occurred while searching for slots"
        }, None