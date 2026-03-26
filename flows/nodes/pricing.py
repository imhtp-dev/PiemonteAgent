"""
Price information node — presents service prices and availability from slot API response
"""

from typing import List, Dict, Any
from datetime import datetime
from pipecat_flows import NodeConfig, FlowsFunctionSchema
from loguru import logger

from flows.handlers.pricing_handlers import handle_proceed_to_booking, handle_end_price_inquiry
from config.settings import settings


def _extract_first_available_date(slots: List[Dict[str, Any]]) -> str:
    """Extract the earliest available slot date from the slots list."""
    earliest = None
    for slot in slots:
        start_time = slot.get("start_time", "")
        if not start_time:
            continue
        try:
            # Parse ISO format: "2026-03-28T09:00:00+00:00"
            dt = datetime.fromisoformat(start_time)
            if earliest is None or dt < earliest:
                earliest = dt
        except (ValueError, TypeError):
            pass

    if earliest:
        # Format as readable date: "28 March 2026"
        return earliest.strftime("%-d %B %Y")
    return ""


def create_price_info_node(slots: List[Dict[str, Any]], service_name: str, center_name: str) -> NodeConfig:
    """Create node that presents price and availability info extracted from slot response.

    Args:
        slots: Slot API response (list of slot dicts with health_services[].price/cerba_card_price)
        service_name: Display name of the service
        center_name: Display name of the health center
    """
    # Extract price range across ALL slots (different doctors = different prices)
    all_prices = []
    all_cerba_prices = []

    for slot in slots:
        for hs in slot.get("health_services", []):
            price = hs.get("price")
            cerba_price = hs.get("cerba_card_price")
            if price is not None:
                try:
                    all_prices.append(float(price))
                except (ValueError, TypeError):
                    pass
            if cerba_price is not None:
                try:
                    all_cerba_prices.append(float(cerba_price))
                except (ValueError, TypeError):
                    pass

    # Build price range text
    if all_prices:
        min_price = min(all_prices)
        max_price = max(all_prices)
        if min_price == max_price:
            price_range_text = f"The price for {service_name} is {min_price:.0f} euro."
        else:
            price_range_text = f"The price range for {service_name} is from {min_price:.0f} euro to {max_price:.0f} euro, depending on the doctor."

        # Add Cerba Card range if available
        if all_cerba_prices:
            min_cerba = min(all_cerba_prices)
            max_cerba = max(all_cerba_prices)
            if min_cerba == max_cerba:
                price_range_text += f" With Cerba Card the price is {min_cerba:.0f} euro."
            else:
                price_range_text += f" With Cerba Card the price range is from {min_cerba:.0f} euro to {max_cerba:.0f} euro."
    else:
        price_range_text = "Price information is not available at this time."

    # Extract first available date
    first_available = _extract_first_available_date(slots)
    if first_available:
        availability_text = f"The first available slot is on {first_available}."
    else:
        availability_text = ""

    logger.info(f"💰 Price info node: {service_name} @ {center_name}")
    logger.info(f"   Range: {min(all_prices) if all_prices else 'N/A'}-{max(all_prices) if all_prices else 'N/A'}€ ({len(slots)} slots)")
    if first_available:
        logger.info(f"   First available: {first_available}")

    # Build post-pricing prompt based on booking availability
    if settings.booking_enabled:
        post_price_prompt = """After presenting the prices, ask the patient if they would like to book this service.
If yes → call proceed_to_booking.
If no or they want something else → call end_price_inquiry."""
        proceed_description = "Patient wants to book this service after seeing the price"
    else:
        post_price_prompt = """After presenting the prices, ask the patient if they would like to book this service.
If yes → call proceed_to_booking. The patient will be transferred to a human operator who will complete the booking.
If no or they want something else → call end_price_inquiry."""
        proceed_description = "Patient wants to book this service — transfer to human operator for booking"

    return NodeConfig(
        name="price_info",
        role_messages=[{
            "role": "system",
            "content": f"""Present the price and availability information to the patient for {service_name} at {center_name}.

{price_range_text}
{availability_text}

Speak naturally and conversationally. Do not use numbered lists.
Present both the price and the first available date (if available) in one natural response.
{post_price_prompt}
{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": "Present the price information to the patient now."
        }],
        functions=[
            FlowsFunctionSchema(
                name="proceed_to_booking",
                handler=handle_proceed_to_booking,
                description=proceed_description,
                properties={},
                required=[]
            ),
            FlowsFunctionSchema(
                name="end_price_inquiry",
                handler=handle_end_price_inquiry,
                description="Patient doesn't want to book or has another question",
                properties={},
                required=[]
            )
        ]
    )
