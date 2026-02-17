"""
Second service handlers for multi-service booking.
Handles search, selection, and sorting for the second service
after the first booking completes.
"""

import re
import asyncio
from typing import Dict, Any, Tuple, List, Optional
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.fuzzy_search import fuzzy_search_service
from services.sorting_api import call_sorting_api
from services.llm_interpretation import interpret_sorting_scenario
from models.requests import HealthService
from config.settings import settings


def _normalize_service_name(name: str) -> str:
    """Normalize service name for comparison"""
    if not name:
        return ""
    return re.sub(r'\s+', ' ', name.lower().strip())


def _find_exact_match(search_term: str, services: List[HealthService]) -> Optional[HealthService]:
    """Find exact match between search term and service names"""
    normalized_search = _normalize_service_name(search_term)
    if not normalized_search:
        return None
    for service in services:
        if normalized_search == _normalize_service_name(service.name):
            logger.info(f"✅ Exact match found: '{search_term}' == '{service.name}'")
            return service
    return None


async def _transition_to_sorting(flow_manager: FlowManager, service: HealthService) -> Tuple[Dict[str, Any], NodeConfig]:
    """Set up selected service and transition to sorting node."""
    flow_manager.state["selected_services"] = [service]
    flow_manager.state["current_service_index"] = 0

    tts = f"Sto verificando la disponibilità per {service.name}. Attendi un momento."
    from flows.nodes.second_service import create_second_service_sorting_node
    return {
        "success": True,
        "service_name": service.name,
        "message": f"Proceeding to sort {service.name}"
    }, create_second_service_sorting_node(service.name, tts)


async def perform_second_service_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform fuzzy search for the second service."""
    try:
        search_term = flow_manager.state.get("pending_search_term", "").strip()

        # If no pending_search_term, use the original pending request text
        if not search_term:
            search_term = flow_manager.state.get("second_service_search_term", "").strip()

        if not search_term or len(search_term) < 2:
            from flows.nodes.service_selection import create_search_retry_node
            return {
                "success": False,
                "message": "Please provide the name of a service to search for.",
                "services": []
            }, create_search_retry_node("Please provide the name of a service to search for.")

        # Store for retry/refine
        flow_manager.state["pending_search_term"] = search_term

        loop = asyncio.get_event_loop()
        logger.info(f"🔍 Second service search: '{search_term}'")
        search_result = await loop.run_in_executor(
            None,
            fuzzy_search_service.search_services,
            search_term,
            3
        )
        logger.info(f"✅ Second service search completed: found={search_result.found}, count={search_result.count}")

        if search_result.found and search_result.services:
            flow_manager.state["services_found"] = search_result.services
            flow_manager.state["current_search_term"] = search_term

            exact_match = _find_exact_match(search_term, search_result.services)

            if exact_match:
                logger.info(f"🎯 Auto-selecting exact match for second service: {exact_match.name}")
                return await _transition_to_sorting(flow_manager, exact_match)

            # Multiple results — show selection
            services_data = [{"name": s.name, "uuid": s.uuid} for s in search_result.services]

            from flows.nodes.second_service import create_second_service_selection_node
            return {
                "success": True,
                "count": search_result.count,
                "services": services_data,
                "search_term": search_term,
                "message": f"Found {search_result.count} services for '{search_term}'"
            }, create_second_service_selection_node(search_result.services, search_term)
        else:
            error_message = search_result.message or f"No services found for '{search_term}'. Can you please provide the full service name."
            from flows.nodes.service_selection import create_search_retry_node
            return {
                "success": False,
                "message": error_message,
                "services": []
            }, create_search_retry_node(error_message)

    except Exception as e:
        logger.error(f"Second service search error: {e}")
        from flows.nodes.service_selection import create_search_retry_node
        return {
            "success": False,
            "message": "Service search failed. Please try again.",
            "services": []
        }, create_search_retry_node("Service search failed. Please try again.")


async def select_second_service_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle user selection of second service from search results."""
    service_uuid = args.get("service_uuid", "").strip()

    if not service_uuid:
        return {"success": False, "message": "Please select a service"}, None

    services_found = flow_manager.state.get("services_found", [])
    selected_service = None
    for service in services_found:
        if service.uuid == service_uuid:
            selected_service = service
            break

    if not selected_service:
        return {"success": False, "message": "Service not found"}, None

    logger.info(f"🎯 Second service selected: {selected_service.name}")
    return await _transition_to_sorting(flow_manager, selected_service)


async def refine_second_service_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle refined search for second service."""
    refined_term = args.get("refined_search_term", "").strip()

    if not refined_term or len(refined_term) < 3:
        return {"success": False, "message": "Please provide a more specific service name"}, None

    # Store and re-search
    flow_manager.state["pending_search_term"] = refined_term

    loop = asyncio.get_event_loop()
    logger.info(f"🔍 Refining second service search: '{refined_term}'")
    search_result = await loop.run_in_executor(
        None,
        fuzzy_search_service.search_services,
        refined_term,
        3
    )

    if search_result.found and search_result.services:
        flow_manager.state["services_found"] = search_result.services
        flow_manager.state["current_search_term"] = refined_term

        exact_match = _find_exact_match(refined_term, search_result.services)
        if exact_match:
            logger.info(f"🎯 Auto-selecting exact match from refined search: {exact_match.name}")
            return await _transition_to_sorting(flow_manager, exact_match)

        services_data = [{"name": s.name, "uuid": s.uuid} for s in search_result.services]
        from flows.nodes.second_service import create_second_service_selection_node
        return {
            "success": True,
            "count": search_result.count,
            "services": services_data,
            "search_term": refined_term
        }, create_second_service_selection_node(search_result.services, refined_term)
    else:
        error_message = f"No services found for '{refined_term}'. Try a different term."
        from flows.nodes.service_selection import create_search_retry_node
        return {
            "success": False,
            "message": error_message,
            "services": []
        }, create_search_retry_node(error_message)


async def perform_second_service_sorting_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Call sorting API for the second service at the existing selected center.
    If sorting fails (center doesn't have it), fall back to center search."""
    try:
        selected_center = flow_manager.state.get("selected_center")
        selected_services = flow_manager.state.get("selected_services", [])
        patient_gender = flow_manager.state.get("patient_gender", "m")
        patient_dob = flow_manager.state.get("patient_dob", "")
        dob_formatted = patient_dob.replace("-", "") if patient_dob else "19800413"

        if not selected_center or not selected_services:
            from flows.nodes.completion import create_error_node
            return {"success": False, "message": "Missing center or service"}, create_error_node("Missing data. Please restart booking.")

        service_name = selected_services[0].name
        logger.info(f"🔄 Second service sorting API: {service_name} at {selected_center.name}")

        sorting_result = await call_sorting_api(
            health_center_uuid=selected_center.uuid,
            gender=patient_gender,
            date_of_birth=dob_formatted,
            selected_services=selected_services
        )

        if sorting_result.get("success"):
            api_response_data = sorting_result["data"]
            flow_manager.state["sorting_api_response"] = api_response_data
            flow_manager.state["sorting_api_package_detected"] = sorting_result.get("package_detected", False)

            # Parse service groups (same logic as booking_handlers.py select_center_and_book)
            service_groups = []
            if api_response_data and isinstance(api_response_data, list):
                for group_idx, group_data in enumerate(api_response_data):
                    if not isinstance(group_data, dict):
                        continue
                    health_services = group_data.get("health_services", [])
                    is_group = group_data.get("group", False)
                    if not health_services:
                        continue

                    services = []
                    for svc in health_services:
                        if not isinstance(svc, dict):
                            continue
                        svc_uuid = svc.get("uuid")
                        svc_name = svc.get("name")
                        svc_code = svc.get("health_service_code", "")
                        if not svc_uuid or not svc_name:
                            continue
                        try:
                            services.append(HealthService(
                                uuid=svc_uuid, name=svc_name, code=svc_code,
                                synonyms=[], sector="health_services"
                            ))
                        except Exception as e:
                            logger.error(f"❌ Failed to create HealthService for {svc_name}: {e}")
                    if services:
                        service_groups.append({"services": services, "is_group": is_group})

            if not service_groups:
                logger.warning("⚠️ No valid groups from sorting API for second service, falling back to center search")
                from flows.nodes.booking import create_final_center_search_node
                return {"success": False, "message": "Sorting failed"}, create_final_center_search_node()

            flow_manager.state["service_groups"] = service_groups

            # LLM interpretation
            openai_api_key = settings.api_keys["openai"]
            llm_interpretation = await interpret_sorting_scenario(
                api_response_data=api_response_data,
                service_groups=service_groups,
                openai_api_key=openai_api_key
            )

            flow_manager.state["booking_scenario"] = llm_interpretation["booking_scenario"]
            flow_manager.state["current_group_index"] = 0
            flow_manager.state["llm_interpretation_reasoning"] = llm_interpretation["reasoning"]

            logger.success(f"✅ Second service sorting OK: scenario={llm_interpretation['booking_scenario']}")

            # Get display name
            first_group_services = service_groups[0]["services"]
            display_name = " più ".join([svc.name for svc in first_group_services])
            center_name = selected_center.name

            from flows.nodes.booking import create_collect_datetime_node
            return {
                "success": True,
                "center_name": center_name,
                "sorting_api_called": True
            }, create_collect_datetime_node(display_name, False, center_name)

        else:
            # Sorting API failed — center doesn't have this service, search new center
            logger.warning(f"⚠️ Sorting API failed for second service at {selected_center.name}, searching new center")
            from flows.nodes.booking import create_final_center_search_node
            return {
                "success": False,
                "message": f"Service not available at {selected_center.name}"
            }, create_final_center_search_node()

    except Exception as e:
        logger.error(f"❌ Second service sorting error: {e}")
        # Fall back to center search
        from flows.nodes.booking import create_final_center_search_node
        return {"success": False, "message": str(e)}, create_final_center_search_node()
