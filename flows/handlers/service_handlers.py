"""
Service search and selection flow handlers
"""

import json
import re
from typing import Dict, Any, Tuple, List, Optional
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.fuzzy_search import fuzzy_search_service
from models.requests import HealthService


def _normalize_service_name(name: str) -> str:
    """Normalize service name for comparison - lowercase, strip, remove extra spaces"""
    if not name:
        return ""
    # Lowercase, strip whitespace, collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', name.lower().strip())
    return normalized


def _find_exact_match(search_term: str, services: List[HealthService]) -> Optional[HealthService]:
    """Find exact match between search term and service names (normalized comparison)"""
    normalized_search = _normalize_service_name(search_term)

    if not normalized_search:
        return None

    for service in services:
        normalized_name = _normalize_service_name(service.name)
        if normalized_search == normalized_name:
            logger.info(f"✅ Exact match found: '{search_term}' == '{service.name}'")
            return service

    logger.info(f"ℹ️ No exact match for '{search_term}' in {len(services)} services")
    return None


async def search_health_services_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Search for health services and dynamically create next node based on results"""
    try:
        search_term = args.get("search_term", "").strip()
        limit = min(args.get("limit", 3), 5)

        logger.info(f"🔍 Flow searching health services: '{search_term}' (limit: {limit})")

        # Store search parameters in flow state for the search node
        flow_manager.state["pending_search_term"] = search_term
        flow_manager.state["pending_search_limit"] = limit

        # Create intermediate node with pre_actions for immediate TTS
        search_status_text = f"Sto cercando servizi correlati a {search_term}. Attendi..."

        from flows.nodes.service_selection import create_search_processing_node
        return {
            "success": True,
            "message": f"Starting search for '{search_term}'"
        }, create_search_processing_node(search_term, limit, search_status_text)

    except Exception as e:
        logger.error(f"Flow service search initialization error: {e}")
        from flows.nodes.service_selection import create_search_retry_node
        return {
            "success": False,
            "message": "Service search failed. Please try again.",
            "services": []
        }, create_search_retry_node("Service search failed. Please try again.")


async def perform_health_services_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual health services search after TTS message"""
    try:
        # Get stored search parameters
        search_term = flow_manager.state.get("pending_search_term", "").strip()
        limit = flow_manager.state.get("pending_search_limit", 3)

        if not search_term or len(search_term) < 2:
            # Import node creator function
            from flows.nodes.service_selection import create_search_retry_node
            return {
                "success": False,
                "message": "Please provide the name of a service to search for.",
                "services": []
            }, create_search_retry_node("Please provide the name of a service to search for.")

        # Use fuzzy search service - run in executor to avoid blocking event loop
        import asyncio
        loop = asyncio.get_event_loop()
        logger.info(f"🔍 Starting non-blocking fuzzy search for: '{search_term}' (limit: {limit})")
        search_result = await loop.run_in_executor(
            None,  # Use default thread pool executor
            fuzzy_search_service.search_services,
            search_term,
            limit
        )
        logger.info(f"✅ Search completed: found={search_result.found}, count={search_result.count}")
        print(search_result)

        if search_result.found and search_result.services:
            # Store services in flow state
            flow_manager.state["services_found"] = search_result.services
            flow_manager.state["current_search_term"] = search_term

            # Check for exact match - auto-select if patient's request matches a service name
            exact_match = _find_exact_match(search_term, search_result.services)

            if exact_match:
                # Auto-select the matching service - skip selection node
                logger.info(f"🎯 Auto-selecting exact match: {exact_match.name}")

                # Initialize selected services list in state
                if "selected_services" not in flow_manager.state:
                    flow_manager.state["selected_services"] = []

                # Add selected service (avoid duplicates)
                if exact_match not in flow_manager.state["selected_services"]:
                    flow_manager.state["selected_services"].append(exact_match)

                # Transition directly to address collection
                from flows.nodes.patient_info import create_collect_address_node
                return {
                    "success": True,
                    "auto_selected": True,
                    "service_name": exact_match.name,
                    "service_uuid": exact_match.uuid,
                    "message": f"Found exact match: {exact_match.name}"
                }, create_collect_address_node()

            # No exact match - show options to user
            services_data = []
            for service in search_result.services:
                services_data.append({
                    "name": service.name,
                    "uuid": service.uuid
                })

            result = {
                "success": True,
                "count": search_result.count,
                "services": services_data,
                "search_term": search_term,
                "message": f"Found {search_result.count} services for '{search_term}'"
            }

            # Dynamically create service selection node with found services
            from flows.nodes.service_selection import create_service_selection_node
            return result, create_service_selection_node(search_result.services, search_term)
        else:
            # Dynamically create no results node
            error_message = search_result.message or f"No services found for '{search_term}'. Can you please provide the full service name."
            from flows.nodes.service_selection import create_search_retry_node
            return {
                "success": False,
                "message": error_message,
                "services": []
            }, create_search_retry_node(error_message)
    
    except Exception as e:
        logger.error(f"Flow service search error: {e}")
        from flows.nodes.service_selection import create_search_retry_node
        return {
            "success": False,
            "message": "Service search failed. Please try again.",
            "services": []
        }, create_search_retry_node("Service search failed. Please try again.")


async def perform_search_action(action: dict, flow_manager) -> None:
    """Custom action handler: speak TTS, run service search, and transition directly.

    Handles TTS internally via queue_frame instead of relying on tts_say action,
    so there's no ActionFinishedFrame dependency that can be dropped by interruptions.
    """
    from pipecat.frames.frames import TTSSpeakFrame

    try:
        tts_text = action.get("tts_text", "")
        if tts_text:
            await flow_manager.task.queue_frame(TTSSpeakFrame(text=tts_text))

        # Delegate to existing handler
        result, next_node = await perform_health_services_search_and_transition({}, flow_manager)
        await flow_manager.set_node_from_config(next_node)

    except Exception as e:
        logger.error(f"Search action error: {e}")
        from flows.nodes.service_selection import create_search_retry_node
        await flow_manager.set_node_from_config(
            create_search_retry_node("Service search failed. Please try again.")
        )


async def select_service_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle service selection and transition to address collection"""
    service_uuid = args.get("service_uuid", "").strip()
    
    if not service_uuid:
        return {"success": False, "message": "Please select a service"}, None
    
    # Find the selected service from stored services
    services_found = flow_manager.state.get("services_found", [])
    selected_service = None
    
    for service in services_found:
        if service.uuid == service_uuid:
            selected_service = service
            break

    # Fallback: match by name if LLM passed name instead of UUID
    if not selected_service:
        for service in services_found:
            if service.name.strip().lower() == service_uuid.strip().lower():
                selected_service = service
                logger.warning(f"⚠️ select_service matched by name fallback: {service.name}")
                break

    if not selected_service:
        return {"success": False, "message": "Service not found"}, None
    
    # Initialize selected services list in state
    if "selected_services" not in flow_manager.state:
        flow_manager.state["selected_services"] = []
    
    # Add selected service (avoid duplicates)
    if selected_service not in flow_manager.state["selected_services"]:
        flow_manager.state["selected_services"].append(selected_service)
    
    logger.info(f"🎯 Service selected: {selected_service.name}")
    
    from flows.nodes.patient_info import create_collect_address_node
    return {
        "success": True, 
        "service_name": selected_service.name,
        "service_uuid": selected_service.uuid
    }, create_collect_address_node()


async def refine_search_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Handle refined search when user wants to speak full service name"""
    refined_term = args.get("refined_search_term", "").strip()
    
    if not refined_term or len(refined_term) < 3:
        return {
            "success": False,
            "message": "Please provide a more specific service name"
        }, None
    
    # Perform new search with refined term - run in executor to avoid blocking
    import asyncio
    loop = asyncio.get_event_loop()
    logger.info(f"🔍 Starting non-blocking refined search for: '{refined_term}'")
    search_result = await loop.run_in_executor(
        None,
        fuzzy_search_service.search_services,
        refined_term,
        3
    )
    logger.info(f"✅ Refined search completed: found={search_result.found}, count={search_result.count}")
    
    if search_result.found and search_result.services:
        # Store new search results
        flow_manager.state["services_found"] = search_result.services
        flow_manager.state["current_search_term"] = refined_term

        # Check for exact match - auto-select if refined term matches a service name
        exact_match = _find_exact_match(refined_term, search_result.services)

        if exact_match:
            # Auto-select the matching service - skip selection node
            logger.info(f"🎯 Auto-selecting exact match from refined search: {exact_match.name}")

            # Initialize selected services list in state
            if "selected_services" not in flow_manager.state:
                flow_manager.state["selected_services"] = []

            # Add selected service (avoid duplicates)
            if exact_match not in flow_manager.state["selected_services"]:
                flow_manager.state["selected_services"].append(exact_match)

            # Transition directly to address collection
            from flows.nodes.patient_info import create_collect_address_node
            return {
                "success": True,
                "auto_selected": True,
                "service_name": exact_match.name,
                "service_uuid": exact_match.uuid,
                "message": f"Found exact match: {exact_match.name}"
            }, create_collect_address_node()

        # No exact match - show options
        services_data = []
        for service in search_result.services:
            services_data.append({
                "name": service.name,
                "uuid": service.uuid
            })

        result = {
            "success": True,
            "count": search_result.count,
            "services": services_data,
            "search_term": refined_term,
            "message": f"Found {search_result.count} services for '{refined_term}'"
        }

        from flows.nodes.service_selection import create_service_selection_node
        return result, create_service_selection_node(search_result.services, refined_term)
    else:
        error_message = f"No services found for '{refined_term}'. Try a different term."
        from flows.nodes.service_selection import create_search_retry_node
        return {
            "success": False,
            "message": error_message,
            "services": []
        }, create_search_retry_node(error_message)