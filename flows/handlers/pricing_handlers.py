"""
Price inquiry flow handlers
"""

from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs


async def handle_proceed_to_booking(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Patient wants to book after seeing price — switch to booking flow.

    All patient data (address, gender, DOB, center) already in state.
    Sorting API skipped (legacy mode) — service UUIDs already known.
    Goes to datetime collection → slot search → cerba → summary → patient details → booking.
    """
    # Switch intent from price_inquiry to booking
    flow_manager.state["intent"] = "booking"
    flow_manager.state["booking_scenario"] = "legacy"

    logger.info("💰→📅 Price inquiry → booking: proceeding to datetime collection")

    # Get service/center names for the datetime node
    selected_services = flow_manager.state.get("selected_services", [])
    selected_center = flow_manager.state.get("selected_center")
    first_service_name = selected_services[0].name if selected_services else "your appointment"
    center_name = selected_center.name if selected_center else ""

    from flows.nodes.booking import create_collect_datetime_node
    return {
        "success": True,
        "message": "Proceeding to booking"
    }, create_collect_datetime_node(first_service_name, False, center_name)


async def handle_end_price_inquiry(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Patient doesn't want to book — clear booking state and return to router."""
    # Clear booking-related state
    flow_manager.state["booking_in_progress"] = False
    flow_manager.state.pop("intent", None)
    flow_manager.state.pop("selected_center", None)
    flow_manager.state.pop("available_slots", None)
    flow_manager.state.pop("pending_slot_search_params", None)

    logger.info("💰→🏠 Price inquiry ended, returning to router")

    from flows.nodes.router import create_router_node
    return {
        "success": True,
        "message": "Price inquiry ended"
    }, create_router_node()
