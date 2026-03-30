"""
Price information node — presents service prices and availability from slot API response
"""

from typing import List, Dict, Any
from datetime import datetime
from pipecat_flows import NodeConfig, FlowsFunctionSchema
from loguru import logger

from flows.handlers.pricing_handlers import handle_proceed_to_booking, handle_end_price_inquiry
from config.settings import settings


def _fmt_price(price: float) -> str:
    """Format price: show decimals only when needed. 134.0→'134', 120.6→'120,60', 76.5→'76,50'."""
    if price == int(price):
        return str(int(price))
    return f"{price:.2f}".replace(".", ",")


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


def create_price_info_node(slots: List[Dict[str, Any]], service_name: str, center_name: str, doctor_name: str = None, doctor_not_found: bool = False, requested_doctor: str = None) -> NodeConfig:
    """Create node that presents price and availability info extracted from slot response.

    Args:
        slots: Slot API response (list of slot dicts with health_services[].price/cerba_card_price)
        service_name: Display name of the service
        center_name: Display name of the health center
        doctor_name: Matched doctor name (if doctor was found)
        doctor_not_found: True if patient requested a doctor but not found at this center
        requested_doctor: Original doctor name requested by patient (for "not found" message)
    """
    # Extract price range across ALL slots (different doctors = different prices)
    all_prices = []
    all_cerba_prices = []
    total_slots = 0
    slots_with_cerba = 0
    slots_without_cerba = 0

    for slot in slots:
        for hs in slot.get("health_services", []):
            total_slots += 1
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
                    slots_with_cerba += 1
                except (ValueError, TypeError):
                    slots_without_cerba += 1
            else:
                slots_without_cerba += 1

    # Log detailed price breakdown
    logger.info(f"💰 Price extraction: {total_slots} service entries from {len(slots)} slots")
    logger.info(f"💰   Prices: {sorted(set(all_prices)) if all_prices else 'NONE'}")
    logger.info(f"💰   Cerba Card prices: {sorted(set(all_cerba_prices)) if all_cerba_prices else 'NONE'}")
    logger.info(f"💰   Slots with Cerba Card: {slots_with_cerba}, without: {slots_without_cerba}")

    # Build price range text
    if all_prices:
        min_price = min(all_prices)
        max_price = max(all_prices)
        if min_price == max_price:
            price_range_text = f"The price for {service_name} is {_fmt_price(min_price)} euro."
        else:
            price_range_text = f"The price range for {service_name} is from {_fmt_price(min_price)} euro to {_fmt_price(max_price)} euro, depending on the doctor."

        # Add Cerba Card range if available
        if all_cerba_prices:
            min_cerba = min(all_cerba_prices)
            max_cerba = max(all_cerba_prices)
            if min_cerba == max_cerba:
                price_range_text += f" With Cerba Card the price is {_fmt_price(min_cerba)} euro."
            else:
                price_range_text += f" With Cerba Card the price range is from {_fmt_price(min_cerba)} euro to {_fmt_price(max_cerba)} euro."
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
    post_price_prompt = """After presenting the prices, ask ONLY: "Vuoi prenotare? Oppure hai bisogno di altre informazioni?"
Do NOT mention transfer, operator, or "posso trasferirti". Just ask the question and wait for the patient's answer.
If patient says yes to booking → call proceed_to_booking.
If no or they want something else → call end_price_inquiry."""

    if settings.booking_enabled:
        proceed_description = "Patient wants to book this service after seeing the price"
    else:
        proceed_description = "Patient wants to book — transfer to operator"

    # Build doctor context for prompt
    doctor_context = ""
    if doctor_name:
        doctor_context = f"\nThis availability is specifically with {doctor_name}. Mention the doctor's name when presenting."
    elif doctor_not_found and requested_doctor:
        doctor_context = f"\nIMPORTANT: The patient asked about {requested_doctor}, but this doctor was NOT found at this center for this service. First say '{requested_doctor} non risulta disponibile presso questa sede per questa prestazione.' Then present the GENERAL availability below and ask if they want it."

    return NodeConfig(
        name="price_info",
        role_messages=[{
            "role": "system",
            "content": f"""Present the price and availability information to the patient for {service_name} at {center_name}.{doctor_context}

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
