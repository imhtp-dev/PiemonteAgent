"""
Price information node — presents service prices from slot API response
"""

from typing import List, Dict, Any
from pipecat_flows import NodeConfig, FlowsFunctionSchema
from loguru import logger

from flows.handlers.pricing_handlers import handle_proceed_to_booking, handle_end_price_inquiry
from config.settings import settings


def create_price_info_node(slots: List[Dict[str, Any]], service_name: str, center_name: str) -> NodeConfig:
    """Create node that presents price info extracted from slot response.

    Args:
        slots: Slot API response (list of slot dicts with health_services[].price/cerba_card_price)
        service_name: Display name of the service
        center_name: Display name of the health center
    """
    # Extract prices from first slot's health_services
    price_lines = []
    total_price = 0.0
    total_cerba = 0.0

    if slots and slots[0].get("health_services"):
        for hs in slots[0]["health_services"]:
            name = hs.get("name", "")
            price = hs.get("price")
            cerba_price = hs.get("cerba_card_price")

            price_str = f"€{price}" if price is not None else "N/A"
            cerba_str = f"€{cerba_price}" if cerba_price is not None else "N/A"
            price_lines.append(f"- {name}: {price_str} (con Cerba Card: {cerba_str})")

            if price is not None:
                try:
                    total_price += float(price)
                except (ValueError, TypeError):
                    pass
            if cerba_price is not None:
                try:
                    total_cerba += float(cerba_price)
                except (ValueError, TypeError):
                    pass

    prices_text = "\n".join(price_lines) if price_lines else "- Price information not available"

    # Build total line only if multiple services
    total_line = ""
    if len(price_lines) > 1:
        total_line = f"\nTotale: €{total_price:.2f} (con Cerba Card: €{total_cerba:.2f})"

    logger.info(f"💰 Price info node: {service_name} @ {center_name}")
    for line in price_lines:
        logger.info(f"   {line}")

    return NodeConfig(
        name="price_info",
        role_messages=[{
            "role": "system",
            "content": f"""Present the price information to the patient for {service_name} at {center_name}.

Prices:
{prices_text}{total_line}

After presenting the prices, ask the patient if they would like to book this service.
If yes → call proceed_to_booking.
If no or they want something else → call end_price_inquiry.
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
                description="Patient wants to book this service after seeing the price",
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
