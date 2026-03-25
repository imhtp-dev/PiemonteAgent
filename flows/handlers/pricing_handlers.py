"""
Price inquiry flow handlers
"""

from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from config.settings import settings


async def handle_proceed_to_booking(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Patient wants to book after seeing price — switch to booking flow.

    When booking is disabled (initial release), escalates to operator instead.
    Operator receives full transcript summary (service, price, patient info).

    All patient data (address, gender, DOB, center) already in state.
    Sorting API skipped (legacy mode) — service UUIDs already known.
    Goes to datetime collection → slot search → cerba → summary → patient details → booking.
    """
    # CHECK: Is booking disabled? (initial release — info + pricing only)
    if not settings.booking_enabled:
        business_status = flow_manager.state.get("business_status", "open")

        # If call center is closed, don't escalate — tell patient to call back
        if business_status in ("close", "after_hours"):
            logger.info("🚫 Booking disabled + call center closed — cannot escalate after price inquiry")
            from flows.nodes.router import create_router_node
            return {
                "success": True,
                "message": "Mi dispiace, la prenotazione per questo servizio richiede un operatore, ma il call center è attualmente chiuso. La invito a richiamare durante gli orari di apertura."
            }, create_router_node(business_status=business_status)

        logger.info("🚫 Booking disabled — escalating to operator after price inquiry")

        flow_manager.state["transfer_reason"] = "Prenotazione richiesta dopo verifica prezzo (booking disabilitato)"
        flow_manager.state["transfer_requested"] = True
        flow_manager.state["transfer_type"] = "booking_disabled"

        from flows.handlers.global_handlers import _handle_transfer_escalation
        await _handle_transfer_escalation(flow_manager)

        from flows.nodes.transfer import create_transfer_node
        return {
            "success": True,
            "redirected_to_transfer": True,
            "message": "Per la prenotazione la trasferisco a un operatore che potrà aiutarti."
        }, create_transfer_node()

    # Switch intent from price_inquiry to booking
    flow_manager.state["intent"] = "booking"
    flow_manager.state["booking_scenario"] = "legacy"

    logger.info("💰→📅 Price inquiry → booking: proceeding to datetime collection")

    # Get service/center names for the datetime node
    selected_services = flow_manager.state.get("selected_services", [])
    selected_center = flow_manager.state.get("selected_center")
    first_service_name = selected_services[0].name if selected_services else "your appointment"
    center_name = selected_center.name if selected_center else ""

    from flows.handlers.booking_handlers import auto_search_first_available
    return await auto_search_first_available(flow_manager)


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
    }, create_router_node(business_status=flow_manager.state.get("business_status", "open"))
