"""
Final booking completion nodes
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict
from pipecat_flows import NodeConfig, FlowsFunctionSchema

from flows.handlers.service_handlers import search_health_services_and_transition
from config.settings import settings


def create_booking_success_final_node(booking_info: Dict, selected_services: List, booked_slots: List[Dict]) -> NodeConfig:
    """Create final booking success node with complete booking details"""

    # Format booking details
    services_text = ", ".join([service.name for service in selected_services])
    booking_code = booking_info.get("code", "N/A")
    booking_uuid = booking_info.get("uuid", "N/A")
    creation_date = booking_info.get("created_at", "")

    # Format slot details (use service_name from booked_slots, not selected_services index)
    slots_details = []
    total_price = 0

    for slot in booked_slots:
        # Convert UTC times to Italian local time for user display
        from services.timezone_utils import utc_to_italian_display

        italian_start = utc_to_italian_display(slot['start_time'])
        italian_end = utc_to_italian_display(slot['end_time'])

        # Fallback to original if conversion fails
        if not italian_start or not italian_end:
            from loguru import logger
            logger.warning(f"⚠️ Timezone conversion failed for booking completion display, using original times")
            start_time_str = slot['start_time'].replace("T", " ").replace("+00:00", "")
            end_time_str = slot['end_time'].replace("T", " ").replace("+00:00", "")
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        else:
            # Use converted Italian times
            start_dt = datetime.strptime(italian_start, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(italian_end, "%Y-%m-%d %H:%M:%S")

        formatted_date = start_dt.strftime("%d %B %Y")
        start_time = start_dt.strftime("%-H:%M")
        end_time = end_dt.strftime("%-H:%M")

        # Use service_name from slot (which already has bundled names like "RX A + RX B")
        service_name = slot.get('service_name', 'Service')
        price = slot.get('price', 0)

        # If price is 0, try to get it from health_services within the slot (like in booking summary)
        if price == 0 and 'health_services' in slot and len(slot['health_services']) > 0:
            health_service = slot['health_services'][0]
            price = health_service.get('price', 0)

        total_price += price

        slots_details.append(f"• {service_name} il {formatted_date} dalle {start_time} alle {end_time} - {float(price):.2f} euro")

    # Create confirmation message
    if creation_date:
        try:
            created_dt = datetime.fromisoformat(creation_date.replace('Z', '+00:00'))
            created_date = created_dt.strftime("%d %B %Y at %-H:%M")
        except:
            created_date = creation_date
    else:
        created_date = datetime.now().strftime("%d %B %Y at %-H:%M")

    task_content = f"""🎉 Excellent! Your booking has been created successfully!

**Your Appointments:**
{chr(10).join(slots_details)}

**Total Cost: {float(total_price):.2f} euro**

You will receive a confirmation SMS on your phone number with all the details. Thank you for choosing Cerba Healthcare!

Is there anything else I can help you with today?"""

    return NodeConfig(
        name="booking_success_final",
        role_messages=[{
            "role": "system",
            "content": f"""Celebrate the completion of the booking with warmth and professionalism. Always say 'euro' instead of using the € symbol. Speak naturally like a friendly assistant.

📅 DATE AND TIME FORMATTING RULES (MUST FOLLOW):
- ALWAYS remove leading zeros from BOTH hours AND minutes
- "07:30" → "7:30", "03:15" → "3:15", "09:45" → "9:45"
- "11:05" → "11:5" (remove the 0!), "14:05" → "14:5", "08:07" → "8:7"
- For times ending in :00, say "in punto": "07:00" → "7 in punto"
- Remove leading zeros from dates: "01 November" → "1 November"

CRITICAL ITALIAN RULES: ALWAYS say "più" (NOT "Plus"), "in punto" (NOT "o'clock"), "euro" (NOT "euros").

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="start_new_booking",
                handler=search_health_services_and_transition,
                description="Start a new booking process",
                properties={
                    "search_term": {
                        "type": "string",
                        "description": "Name of the service to search for a new booking"
                    }
                },
                required=["search_term"]
            )
        ]
    )