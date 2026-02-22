"""
Agent Routing Handlers
Handles routing between agents (kept for future extensibility)

NOTE: Info agent routing removed - now handled by global functions.
Booking routing kept for future agent integration.
"""

from typing import Dict, Any, Tuple
from pipecat_flows import FlowManager, NodeConfig
from loguru import logger


async def route_to_booking_handler(
    args: Dict[str, Any],
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Route to booking agent flow.
    NOTE: This is kept for future use when integrating other agents.
    Currently, global function `start_booking` handles booking routing.

    Args:
        args: Contains user_request (what they want to book)
        flow_manager: Flow manager instance

    Returns:
        Tuple of (result dict, booking greeting node)
    """
    user_request = args.get("user_request", "")

    logger.info(f"🟢 Routing to BOOKING agent | User request: {user_request}")

    # Update state to track current agent
    flow_manager.state["current_agent"] = "booking"
    flow_manager.state["booking_in_progress"] = False  # Will be set to True once booking starts
    flow_manager.state["can_transfer_to_info"] = False  # Block info transfers during booking
    flow_manager.state["came_from_agent"] = flow_manager.state.get("current_agent", "router")

    # Store the user's initial request for the booking flow
    if user_request:
        flow_manager.state["initial_booking_request"] = user_request

    logger.info(f"📊 State updated: current_agent=booking, booking_in_progress=False")

    # Import and return booking greeting node
    from flows.nodes.greeting import create_greeting_node

    return {
        "routed_to": "booking_agent",
        "user_request": user_request,
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }, create_greeting_node()


async def transfer_from_booking_to_info_handler(
    args: Dict[str, Any],
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Transfer from booking agent back to router (which has global info functions).
    This can ONLY be called AFTER booking completion.

    Args:
        args: Contains user_question (what they want to know)
        flow_manager: Flow manager instance

    Returns:
        Tuple of (result dict, router node with global functions)
    """
    user_question = args.get("user_question", "")

    # Safety check: only allow if booking is completed
    booking_completed = flow_manager.state.get("booking_completed", False)
    booking_in_progress = flow_manager.state.get("booking_in_progress", False)

    if booking_in_progress and not booking_completed:
        logger.error(f"❌ BLOCKED: Cannot transfer during active booking")
        from flows.nodes.booking import create_collect_datetime_node
        return {
            "error": "Cannot transfer during booking",
            "message": "Please complete the booking first"
        }, create_collect_datetime_node()

    logger.info(f"🟢➜🟠 Transferring from BOOKING to ROUTER | Question: {user_question}")

    # Update state
    flow_manager.state["previous_agent"] = "booking"
    flow_manager.state["current_agent"] = "router"
    flow_manager.state["transfer_reason"] = "Post-booking question"
    flow_manager.state["can_transfer_to_booking"] = True

    if user_question:
        flow_manager.state["post_booking_question"] = user_question

    logger.success(f"✅ Transfer complete: BOOKING → ROUTER (global functions available)")

    # Return router node (has global info functions)
    from flows.nodes.router import create_router_node

    return {
        "transfer": "booking_to_router",
        "user_question": user_question,
        "post_booking": True,
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }, create_router_node(business_status=flow_manager.state.get("business_status", "open"))
