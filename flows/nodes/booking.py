"""
Booking and appointment management nodes
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, List
from pipecat_flows import NodeConfig, FlowsFunctionSchema, ContextStrategyConfig, ContextStrategy
from loguru import logger

from models.requests import HealthService, HealthCenter

# Global variable to store current session's filtered slots for UUID lookup
_current_session_slots = {}
from flows.handlers.flow_handlers import generate_flow_and_transition, finalize_services_and_search_centers
from flows.handlers.booking_handlers import (
    search_final_centers_and_transition,
    select_center_and_book,
    check_cerba_membership_and_transition,
    collect_datetime_and_transition,
    search_slots_and_transition,
    select_slot_and_book,
    create_booking_and_transition,
    confirm_booking_summary_and_proceed,
    update_date_and_search_slots,
    show_more_same_day_slots_handler,
    search_different_date_handler,
    handle_radius_expansion_response
)
from config.settings import settings
from utils.italian_time import time_to_italian_words


def create_orange_box_node() -> NodeConfig:
    """Create the Orange Box node that generates decision flows"""
    return NodeConfig(
        name="orange_box_flow_generation",
        role_messages=[{
            "role": "system",
            "content": f"Call generate_flow function to analyze service requirements. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Call generate_flow now to check for special requirements."
        }],
        functions=[
            FlowsFunctionSchema(
                name="generate_flow",
                handler=generate_flow_and_transition,
                description="Generate decision flow for the selected service. Call this immediately.",
                properties={},
                required=[]
            )
        ]
        # respond_immediately=True  # DISABLED FOR TESTING - enable in production
    )


def create_flow_navigation_node(generated_flow: dict, service_name: str) -> NodeConfig:
    """Create LLM-driven flow navigation node"""
    return NodeConfig(
        name="flow_navigation",
        role_messages=[{
            "role": "system",
            "content": f"""You are navigating a decision flow for the health service: {service_name}

🔥 MANDATORY: FOLLOW THIS EXACT JSON FLOW STRUCTURE STEP-BY-STEP 🔥

{json.dumps(generated_flow, indent=2)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 STEP-BY-STEP NAVIGATION PROTOCOL (MUST FOLLOW STRICTLY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**STEP 1: START AT ROOT LEVEL**
- Begin by presenting the "message" field from the ROOT level of the JSON
- This is your first question to the user
- DO NOT skip this step - always start here

**STEP 2: WAIT FOR USER RESPONSE**
- After presenting the message, WAIT for user to answer
- User will answer either YES or NO (or select from service options)
- DO NOT proceed to next step until user responds

**STEP 3: NAVIGATE TO CORRECT BRANCH**
- If user says YES → navigate into the "yes" branch object
- If user says NO → navigate into the "no" branch object
- If user selects a service from list_health_services → track that service (uuid, code, sector) AND continue with YES branch

**STEP 4: CHECK CURRENT POSITION IN TREE**
At your new position in the tree, check what exists:
- Does this level have a "message" field? → Present it and wait for response (go back to STEP 2)
- Does this level have "list_health_services"? → Present the service options
- Does this level have "action": "save_cart"? → Call finalize_services function (FINAL STEP)
- Does this level ONLY have text without yes/no branches? → This is an END point, call finalize_services

**STEP 5: TRACK ALL SELECTED SERVICES**
Throughout navigation, maintain a list of ALL services user selected:
- Main service (from root "list_health_services" or "main_exam")
- Additional services from any "list_health_services" arrays
- Specialist visits if user answered YES to specialist questions

For EACH service, extract from parallel arrays at the SAME index:
- uuid from list_health_servicesUUID[index]
- name from list_health_services[index]
- code from health_service_code[index]
- sector from sector[index]

**STEP 6: RECOGNIZE END CONDITIONS**
You've reached the end when you encounter ANY of these:
- "action": "save_cart" → Call finalize_services immediately
- A message with NO "yes" or "no" branches below it → Call finalize_services
- A terminal node (like "It is not possible to proceed...") → Call finalize_services with empty additional_services

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 EXAMPLE NAVIGATION FLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Using your JSON structure:

1️⃣ START: Present root "message"
   → "This exam requires a prescription from a general practitioner or specialist. Do you have a medical prescription?"

2️⃣ USER SAYS: "Yes"
   → Navigate to json["yes"] branch

3️⃣ PRESENT: json["yes"]["message"]
   → "Does your prescription include any of the following additional services you want to book?"
   → Show services: "RX del Piede Destro, RX del Piede Destro Sotto carico, ..." (NO NUMBERS!)

4️⃣ USER SAYS: "Yes, I want RX del Piede Destro"
   → Track service: uuid="ea65a7bf-58e4-4ac0-9041-61a5088cefb6", code="RRAD0049", sector="optionals"
   → Navigate to json["yes"]["yes"] branch

5️⃣ PRESENT: json["yes"]["yes"]["message"]
   → "Performing a diagnostic exam does not include the visit. Do you want to book a specialist visit to review the booked exams?"
   → Show service: "Visita Ortopedica (Prima Visita)"

6️⃣ USER SAYS: "Yes"
   → Track specialist service: uuid="1cc793b7-4a8b-4c54-ac09-3c7ca7e5a168", code="PORT0001", sector="opinions"
   → Navigate to json["yes"]["yes"]["yes"] branch

7️⃣ FOUND: "action": "save_cart"
   → END REACHED! Call finalize_services with ALL tracked services

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 CRITICAL RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **NEVER SKIP LEVELS**: You must present EVERY "message" field you encounter while navigating the tree
2. **NEVER MENTION UUIDs**: Users see only service names, UUIDs are internal tracking only
3. **NEVER USE NUMBERS**: Don't say "1. Service A, 2. Service B" - just say "Service A, Service B"
4. **ALWAYS EXTRACT PARALLEL ARRAYS**: When user picks a service, get uuid, code, AND sector at same index
5. **TRACK EVERYTHING**: Keep a mental list of ALL services selected throughout the conversation (including optional services, specialist visits, prescriptions, etc.)
6. **FOLLOW YES/NO STRICTLY**: If user says YES, go to "yes" branch. If NO, go to "no" branch. Never mix them up.
7. **CALL finalize_services ONLY AT THE END**: When you reach "action": "save_cart" or a terminal node, include ALL tracked services in additional_services array

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎭 PRESENTATION STYLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Present messages EXACTLY as written in the "message" fields
- Speak naturally and conversationally like a human healthcare assistant
- When listing services, use natural language: "We have X, Y, and Z available"
- Never sound robotic or mention technical terms like "UUID", "sector", "branch"

Be conversational but follow the flow structure carefully. Always speak like a human, not a robot. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": f"Start the decision flow for {service_name}. Begin with the main message from the generated flow."
        }],
        functions=[
            FlowsFunctionSchema(
                name="finalize_services",
                handler=finalize_services_and_search_centers,
                description="Finalize ALL service selections and proceed to center search",
                properties={
                    "additional_services": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "uuid": {"type": "string", "description": "Service UUID from list_health_servicesUUID array"},
                                "name": {"type": "string", "description": "Service name from list_health_services array"},
                                "code": {"type": "string", "description": "Service code from health_service_code array"},
                                "sector": {"type": "string", "description": "Service sector from sector array (health_services, prescriptions, preliminary_visits, optionals, opinions)"}
                            },
                            "required": ["uuid", "name", "code", "sector"]
                        },
                        "description": "ALL services selected during flow navigation. Extract uuid, name, code, and sector from the parallel arrays at the SAME index in the flow JSON. Include ALL services user selected: optionals, prescriptions, preliminary visits, specialist visits (opinions sector), etc."
                    },
                    "flow_path": {
                        "type": "string",
                        "description": "The path through the decision tree (e.g., 'yes->yes', 'yes->no')"
                    }
                },
                required=[]
            )
        ]
    )


def create_final_center_search_node() -> NodeConfig:
    """Create final center search node with all services"""
    return NodeConfig(
        name="final_center_search",
        role_messages=[{
            "role": "system",
            "content": f"Call search_final_centers function immediately. Do not speak. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Call search_final_centers now."
        }],
        functions=[
            FlowsFunctionSchema(
                name="search_final_centers",
                handler=search_final_centers_and_transition,
                description="Search health centers. Call this immediately.",
                properties={},
                required=[]
            )
        ]
        # respond_immediately=True  # DISABLED FOR TESTING - enable in production
    )


def create_final_center_selection_node(centers: List[HealthCenter], services: List[HealthService], expanded_search: bool = False) -> NodeConfig:
    """Create final center selection node with top 3 centers

    Args:
        centers: List of health centers to choose from
        services: List of health services being booked
        expanded_search: True if search was expanded beyond initial 20km radius
    """
    top_centers = centers[:3]
    service_names = ", ".join([s.name for s in services])

    # Create Italian ordinals for natural speech
    italian_ordinals = ["Il primo", "il secondo", "il terzo", "il quarto", "il quinto"]

    # Create TTS-friendly sentence listing of health centers (Italian)
    if len(top_centers) == 1:
        centers_text = f"Il centro disponibile è {top_centers[0].name}."
    elif len(top_centers) == 2:
        centers_text = f"Il primo centro è {top_centers[0].name}, e il secondo è {top_centers[1].name}."
    elif len(top_centers) == 3:
        centers_text = f"Il primo centro è {top_centers[0].name}, il secondo è {top_centers[1].name}, e il terzo è {top_centers[2].name}."
    else:
        # Fallback for more than 3 centers
        centers_parts = [f"{italian_ordinals[i]} è {center.name}" for i, center in enumerate(top_centers[:5])]
        centers_text = ", ".join(centers_parts[:-1]) + f", e {centers_parts[-1]}."

    # Build UUID mapping for function calls (internal use only)
    uuid_mapping = {center.name: center.uuid for center in top_centers}

    # Different intro based on whether search was expanded
    if expanded_search:
        intro_text = f"Ho cercato in un'area più ampia e ho trovato alcuni centri che offrono {service_names}."
    else:
        intro_text = f"Ho trovato alcuni centri che offrono {service_names}."

    task_content = f"""Say EXACTLY this (one natural flowing sentence, NO lists or bullet points):
"{intro_text} {centers_text} Quale preferisci?" """

    return NodeConfig(
        name="final_center_selection",
        role_messages=[{
            "role": "system",
            "content": f"""Help patient choose a health center for: {service_names}.

🚨 TTS-FRIENDLY OUTPUT RULES (CRITICAL):
- NEVER use bullet points, numbered lists, or line breaks
- NEVER format as "1. Centro X\\n2. Centro Y" - TTS reads this without pauses
- Speak in ONE natural Italian sentence that flows smoothly
- Example GOOD: "Il primo centro è Milano Biochimico, il secondo è Cologno Curie, e il terzo è Rozzano Delta Medica. Quale preferisci?"
- Example BAD: "1. Milano Biochimico\\n2. Cologno Curie\\n3. Rozzano Delta Medica"

Center name → UUID mapping (for function calls only, NEVER speak UUIDs):
{uuid_mapping}

When patient selects a center, call select_center with the correct UUID. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_center",
                handler=select_center_and_book,
                description="Select a health center for booking",
                properties={
                    "center_uuid": {
                        "type": "string",
                        "description": "UUID of the selected health center"
                    }
                },
                required=["center_uuid"]
            )
        ]
    )


def create_ask_expand_radius_node(address: str, service_name: str, current_radius: int, next_radius: int) -> NodeConfig:
    """Create node to ask user if they want to expand search radius

    Args:
        address: Patient's address/location
        service_name: Name of the service being searched
        current_radius: Current search radius that found no results (22 or 42)
        next_radius: Proposed expanded radius (42 or 62)
    """
    return NodeConfig(
        name="ask_expand_radius",
        role_messages=[{
            "role": "system",
            "content": f"""No health centers found within {current_radius}km of {address}.
Ask the user if they want to expand the search to {next_radius}km.

Be conversational and helpful. Explain that expanding the search might find centers further away.
When user responds yes/no, call expand_search_radius with expand=true or expand=false.

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": f"""Say in Italian: "Mi dispiace, non ho trovato centri sanitari entro {current_radius} chilometri da {address} per {service_name}. Vuoi che cerchi in un'area più ampia, fino a {next_radius} chilometri?" """
        }],
        functions=[
            FlowsFunctionSchema(
                name="expand_search_radius",
                handler=handle_radius_expansion_response,
                description="Handle user's decision about expanding the search radius",
                properties={
                    "expand": {
                        "type": "boolean",
                        "description": "True if user wants to expand search, False if not"
                    },
                    "next_radius": {
                        "type": "integer",
                        "description": f"The next radius to search ({next_radius}km)",
                        "default": next_radius
                    }
                },
                required=["expand"]
            )
        ]
    )


def create_no_centers_node(address: str, service_name: str) -> NodeConfig:
    """Dynamically create node when no centers are found"""
    return NodeConfig(
        name="no_centers_found",
        role_messages=[{
            "role": "system",
            "content": "Apologetically explain that no health centers were found and offer alternatives."
        }],
        task_messages=[{
            "role": "system",
            "content": f"No health center found at {address} for {service_name}. Apologize and ask if they'd like to try a different location or service. Offer to start over. {settings.language_config}"
        }],
        functions=[
            FlowsFunctionSchema(
                name="search_health_services",
                handler=lambda args, flow_manager: None,  # Import this properly in implementation
                description="Search for different health services",
                properties={
                    "search_term": {
                        "type": "string",
                        "description": "New service name to search for"
                    }
                },
                required=["search_term"]
            )
        ]
    )


def create_cerba_membership_node() -> NodeConfig:
    """Create Cerba membership check node"""
    return NodeConfig(
        name="cerba_membership_check",
        role_messages=[{
            "role": "system",
            "content": f"""Ask if patient has a Cerba Card for pricing discount.

CRITICAL: When user responds YES or NO, you MUST call check_cerba_membership function with is_cerba_member=true or is_cerba_member=false.

Do NOT say "finalizing booking" or proceed without calling the function. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": "Do you have a Cerba Card?"
        }],
        functions=[
            FlowsFunctionSchema(
                name="check_cerba_membership",
                handler=check_cerba_membership_and_transition,
                description="REQUIRED: Call this when user answers about Cerba membership. Pass is_cerba_member=true if they have a card, false if not. This is needed to proceed to appointment booking.",
                properties={
                    "is_cerba_member": {
                        "type": "boolean",
                        "description": "Whether the user is a Cerba member (true/false)"
                    }
                },
                required=["is_cerba_member"]
            )
        ]
    )


def create_collect_datetime_node(service_name: str = None, is_multi_service: bool = False, center_name: str = None) -> NodeConfig:
    """Create date and time collection node with LLM-driven natural language support

    Args:
        service_name: Optional service name to show in prompt (e.g., "Visita Ortopedica")
        is_multi_service: If True, says "next appointment", if False says "first appointment"
        center_name: Optional center name for context after RESET
    """
    # Get today's complete date information for LLM context
    today = datetime.now()
    today_date = today.strftime("%Y-%m-%d")
    today_day = today.strftime("%A")  # Full day name (e.g., "Thursday")
    today_formatted = today.strftime("%B %d, %Y")  # e.g., "October 16, 2025"

    # Determine node name and task content based on service name
    if service_name:
        if is_multi_service:
            task_content = f"What date and time would you prefer for your next appointment: {service_name}?"
        else:
            task_content = f"What date and time would you prefer for your first appointment: {service_name}?"
        node_name = "collect_datetime_service"
    else:
        task_content = "What date and time would you prefer for your appointment?"
        node_name = "collect_datetime"

    # Build booking context summary for RESET
    booking_context = ""
    if service_name:
        booking_context += f"Booking service: {service_name}."
    if center_name:
        booking_context += f" Health center: {center_name}."

    return NodeConfig(
        name=node_name,
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        pre_actions=[
            {
                "type": "tts_say",
                "text": "Ottimo! Ora fissiamo il tuo appuntamento."
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"""{booking_context}

Today is {today_day}, {today_formatted} (date: {today_date}). The current year is 2025.

You can understand natural language date expressions and calculate the correct dates automatically. When a patient mentions expressions like:
- "tomorrow" → calculate the next day
- "next Friday" → calculate the next Friday from today
- "next week" → calculate 7 days from today
- "next month" → calculate approximately 30 days from today
- "next Thursday" → calculate the next Thursday (if today is Thursday, it means the following Thursday)

🚀 SPECIAL: "FIRST AVAILABLE" / "MOST RECENT" REQUESTS:
If the patient says ANY of these phrases:
- "most recent availability" / "disponibilità più recente"
- "first available" / "prima disponibilità"
- "earliest possible" / "il prima possibile"
- "soonest slot" / "prima data libera"
- "any time, just the earliest" / "qualsiasi ora, solo la prima"
- "give me the first one" / "dammi il primo"

Then respond with:
- preferred_date: "{today_date}" (TODAY'S DATE)
- time_preference: "any"
- first_available_mode: true

IMPORTANT:
- If the user says 'morning' or mentions morning time, set time_preference to "morning" (8:00-12:00)
- If they say 'afternoon' or mention afternoon time, set time_preference to "afternoon" (12:00-19:00)
- If they mention a specific time, set time_preference to "specific"
- If no time preference mentioned, set time_preference to "any"

Calculate the exact date in YYYY-MM-DD format and call the collect_datetime function directly.

Always use 24-hour time format. Be flexible with user input formats. Speak naturally like a human. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_datetime",
                handler=collect_datetime_and_transition,
                description="Collect preferred appointment date and optional time preference",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "Preferred appointment date in YYYY-MM-DD format. Calculate from natural language expressions using today's date context. Examples: if today is 2025-10-16 (Thursday) and user says 'next Friday' → '2025-10-24', 'tomorrow' → '2025-10-17', 'next Thursday' → '2025-10-23'"
                    },
                    "preferred_time": {
                        "type": "string",
                        "description": "Preferred appointment time (specific time like '9:00', '14:30' or time range like 'morning', 'afternoon')"
                    },
                    "time_preference": {
                        "type": "string",
                        "description": "Time preference: 'morning' (8:00-12:00), 'afternoon' (12:00-19:00), 'specific' (exact time), or 'any' (no preference)"
                    },
                    "first_available_mode": {
                        "type": "boolean",
                        "description": "Set to true if patient wants the earliest/first available/most recent slot regardless of specific date/time"
                    }
                },
                required=["preferred_date"]
            )
        ]
    )


def create_slot_search_node() -> NodeConfig:
    """Create automatic slot search node"""
    return NodeConfig(
        name="slot_search",
        role_messages=[{
            "role": "system",
            "content": f"Search for available appointment slots for the selected service and time. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Let me search for available appointment slots for your preferred date and time. Please wait a moment..."
        }],
        functions=[
            FlowsFunctionSchema(
                name="search_slots",
                handler=search_slots_and_transition,
                description="Search for available appointment slots",
                properties={},
                required=[]
            )
        ]
        # respond_immediately=True  # DISABLED FOR TESTING - enable in production
    )


def create_slot_selection_node(slots: List[Dict], service: HealthService, is_cerba_member: bool = False, user_preferred_date: str = None, time_preference: str = "any time", first_available_mode: bool = False, is_automatic_search: bool = False, first_appointment_date: str = None) -> NodeConfig:
    """Create slot selection node with progressive filtering and minimal LLM data

    Args:
        is_automatic_search: True if this is automatic search for 2nd+ service
        first_appointment_date: Date of first appointment (for 2nd+ services context)
    """

    from loguru import logger

    # Parse and group slots by date
    slots_by_date = {}
    parsed_slots = []

    logger.info(f"🔧 SMART FILTERING: Processing {len(slots)} raw slots for {service.name}")
    logger.info(f"🔧 User preferred date: {user_preferred_date}")
    logger.info(f"🔧 Time preference: {time_preference}")
    logger.info(f"🔧 First available mode: {first_available_mode}")

    for slot in slots:
        # Convert UTC slot times to Italian local time for user display
        from services.timezone_utils import utc_to_italian_display, format_time_for_display

        italian_start = utc_to_italian_display(slot["start_time"])
        italian_end = utc_to_italian_display(slot["end_time"])

        # Fallback to original if conversion fails
        if not italian_start or not italian_end:
            logger.warning(f"⚠️ Timezone conversion failed, using original times")
            start_time_str = slot["start_time"].replace("T", " ").replace("+00:00", "")
            end_time_str = slot["end_time"].replace("T", " ").replace("+00:00", "")
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        else:
            # Use converted Italian times
            start_dt = datetime.strptime(italian_start, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(italian_end, "%Y-%m-%d %H:%M:%S")

        date_key = start_dt.strftime("%Y-%m-%d")
        formatted_date = start_dt.strftime("%d %B")

        # Format times in 24-hour format without leading zeros (Italian local time)
        start_time_24h = start_dt.strftime("%-H:%M")
        end_time_24h = end_dt.strftime("%-H:%M")

        parsed_slot = {
            'original': slot,
            'date_key': date_key,
            'formatted_date': formatted_date,
            'start_time_24h': start_time_24h,
            'end_time_24h': end_time_24h,
            'start_dt': start_dt,
            'end_dt': end_dt,
            'providing_entity_availability_uuid': slot.get('providing_entity_availability_uuid', ''),
            'health_center_name': slot.get('health_center', {}).get('name', ''),
            'service_name': slot.get('health_services', [{}])[0].get('name', service.name)
        }

        parsed_slots.append(parsed_slot)

        if date_key not in slots_by_date:
            slots_by_date[date_key] = []
        slots_by_date[date_key].append(parsed_slot)

    logger.info(f"🔧 PARSED: Found slots for {len(slots_by_date)} different dates: {list(slots_by_date.keys())}")

    # FIRST AVAILABLE MODE HANDLING (SEND ALL SLOTS FROM EARLIEST DATE)
    if first_available_mode:
        logger.info("🎯 FIRST AVAILABLE MODE: Sending ALL slots from earliest available date (TOMORROW)")

        # Get tomorrow's date in Italian timezone (API already searched from tomorrow)
        italian_tz = ZoneInfo("Europe/Rome")
        today_dt = datetime.now(italian_tz)
        tomorrow_dt = today_dt + timedelta(days=1)
        tomorrow_date_key = tomorrow_dt.strftime('%Y-%m-%d')

        logger.info(f"📅 Tomorrow's date: {tomorrow_date_key}")

        # Collect ALL slots across all dates
        all_slots_sorted = []
        for date_key in sorted(slots_by_date.keys()):
            all_slots_sorted.extend(slots_by_date[date_key])

        # Sort by datetime (earliest first)
        all_slots_sorted.sort(key=lambda s: s['start_dt'])

        # Filter to get TOMORROW's slots (or earliest date if tomorrow has no slots)
        tomorrow_slots = [s for s in all_slots_sorted if s['date_key'] == tomorrow_date_key]

        if tomorrow_slots:
            # Send ALL slots from TOMORROW (no limit!)
            selected_slots_for_llm = tomorrow_slots

            # Get the earliest slot for highlighting
            earliest_slot = tomorrow_slots[0]
            formatted_date = earliest_slot['formatted_date']
            earliest_time = earliest_slot['start_time_24h']

            # Check if there are morning and afternoon slots
            morning_slots = [s for s in tomorrow_slots if 8 <= s['start_dt'].hour < 12]
            afternoon_slots = [s for s in tomorrow_slots if 12 <= s['start_dt'].hour < 19]

            # Count other dates available
            unique_dates = sorted(set(s['date_key'] for s in all_slots_sorted))
            other_dates = [d for d in unique_dates if d != tomorrow_date_key]
            other_dates_count = len(other_dates)

            # Build task content with LLM instructions
            task_content = f"""The available appointments for {service.name} are {len(tomorrow_slots)} slots for TOMORROW ({formatted_date}).

🕐 The most recent (earliest) appointment available is at {earliest_time}.

IMPORTANT INSTRUCTIONS:
1. Present only the FIRST 4-6 time slots to the user initially
2. Mention the earliest time ({earliest_time}) as the most recent available
3. After showing the first 4-6 slots, tell the user:"""

            if len(tomorrow_slots) > 6:
                if len(morning_slots) > 0 and len(afternoon_slots) > 0:
                    task_content += f"""
   - "We have a total of {len(tomorrow_slots)} slots available tomorrow"
   - "We have {len(morning_slots)} slots in the morning and {len(afternoon_slots)} slots in the afternoon"
   - Ask if they want to see more options"""
                elif len(morning_slots) > 6:
                    task_content += f"""
   - "We have more morning slots available (total {len(morning_slots)} morning slots)"
   - Ask if they want to see the additional morning times"""
                elif len(afternoon_slots) > 6:
                    task_content += f"""
   - "We have more afternoon slots available (total {len(afternoon_slots)} afternoon slots)"
   - Ask if they want to see the additional afternoon times"""

            if other_dates_count > 0:
                task_content += f"""
4. Mention that appointments are also available on {other_dates_count} other date(s) if they prefer a different day

Ask the user if any of the shown times work for them, or if they'd like to see more options."""

            logger.info(f"✅ FIRST AVAILABLE: Sending ALL {len(tomorrow_slots)} slots from TOMORROW ({tomorrow_date_key})")
            logger.info(f"🕐 Earliest time tomorrow: {earliest_time}")
            logger.info(f"📊 Morning slots: {len(morning_slots)}, Afternoon slots: {len(afternoon_slots)}")
            logger.info(f"📊 Other dates available: {other_dates_count}")

        elif all_slots_sorted:
            # No slots today, show earliest available date with ALL its slots
            earliest_slot = all_slots_sorted[0]
            earliest_date_key = earliest_slot['date_key']
            earliest_date_slots = [s for s in all_slots_sorted if s['date_key'] == earliest_date_key]

            selected_slots_for_llm = earliest_date_slots
            formatted_date = earliest_slot['formatted_date']
            earliest_time = earliest_slot['start_time_24h']

            # Check morning/afternoon distribution
            morning_slots = [s for s in earliest_date_slots if 8 <= s['start_dt'].hour < 12]
            afternoon_slots = [s for s in earliest_date_slots if 12 <= s['start_dt'].hour < 19]

            task_content = f"""No appointments available today. The earliest available date is {formatted_date} with {len(earliest_date_slots)} appointment slots.

🕐 The most recent appointment available is at {earliest_time}.

IMPORTANT INSTRUCTIONS:
1. Present only the FIRST 4-6 time slots to the user initially
2. After showing the first 4-6 slots, tell the user:"""

            if len(earliest_date_slots) > 6:
                if len(morning_slots) > 0 and len(afternoon_slots) > 0:
                    task_content += f"""
   - "We have a total of {len(earliest_date_slots)} slots on {formatted_date}"
   - "We have {len(morning_slots)} morning slots and {len(afternoon_slots)} afternoon slots"
   - Ask if they want to see more options"""

            task_content += """

Ask the user if any of these times work for them, or if they'd like to see more options or search for a different date."""

            logger.info(f"⚠️ FIRST AVAILABLE: No slots today, sending ALL {len(earliest_date_slots)} slots from earliest date ({earliest_date_key})")
            logger.info(f"🕐 Earliest time on {earliest_date_key}: {earliest_time}")
        else:
            task_content = "I'm sorry, but there are currently no available appointments. Please try a different date or service."
            selected_slots_for_llm = []
            logger.error("❌ NO SLOTS AVAILABLE in first available mode")

    # PROGRESSIVE FILTERING LOGIC
    else:
        def filter_slots_by_time_preference(day_slots, preference):
            """Filter slots by morning (8-12) or afternoon (12-19)"""
            if preference == "morning":
                return [slot for slot in day_slots if 8 <= slot['start_dt'].hour < 12]
            elif preference == "afternoon":
                return [slot for slot in day_slots if 12 <= slot['start_dt'].hour < 19]
            else:
                return day_slots  # No time preference

        # Step 1: Check if user's preferred date has slots
        selected_slots_for_llm = []
        task_content = ""

    if not first_available_mode and user_preferred_date and user_preferred_date in slots_by_date:
        logger.info(f"✅ SMART FILTERING: User's preferred date {user_preferred_date} has slots!")
        day_slots = slots_by_date[user_preferred_date]

        # Step 2: Apply time preference filtering
        if time_preference in ["morning", "afternoon"]:
            filtered_slots = filter_slots_by_time_preference(day_slots, time_preference)

            if filtered_slots:
                # User's date + time preference has slots - SEND ALL (no limit)
                selected_slots_for_llm = filtered_slots
                time_period = "morning" if time_preference == "morning" else "afternoon"
                formatted_date = day_slots[0]['formatted_date']

                # Build task content with LLM instructions for progressive display
                task_content = f"""For {service.name} on {formatted_date} in the {time_period}, I found {len(filtered_slots)} available time slots.

IMPORTANT INSTRUCTIONS:
1. Present only the FIRST 4-6 time slots to the user initially
2. After showing the first 4-6 slots, tell the user:"""

                if len(filtered_slots) > 6:
                    task_content += f"""
   - "We have a total of {len(filtered_slots)} {time_period} slots available on {formatted_date}"
   - Ask if they want to see the additional {time_period} times

Ask the user if any of the shown times work for them, or if they'd like to see more options."""
                else:
                    task_content += """

Ask the user which time works best for them."""

                #logger.info(f"✅ PERFECT MATCH: Sending ALL {len(selected_slots_for_llm)} {time_period} slots on {user_preferred_date}")
            else:
                # No slots for preferred time, offer alternatives
                other_time_slots = filter_slots_by_time_preference(day_slots, "afternoon" if time_preference == "morning" else "morning")
                alternative_time = "afternoon" if time_preference == "morning" else "morning"
                formatted_date = day_slots[0]['formatted_date']

                if other_time_slots:
                    # SEND ALL alternative time slots (no limit)
                    selected_slots_for_llm = other_time_slots

                    task_content = f"""Sorry, no {time_preference} slots available for {service.name} on {formatted_date}. However, we have {len(other_time_slots)} {alternative_time} appointments available.

IMPORTANT INSTRUCTIONS:
1. Present only the FIRST 4-6 time slots to the user initially
2. After showing the first 4-6 slots, tell the user:"""

                    if len(other_time_slots) > 6:
                        task_content += f"""
   - "We have a total of {len(other_time_slots)} {alternative_time} slots available"
   - Ask if they want to see the additional {alternative_time} times

Ask the user if any of the shown times work for them, or if they'd like to see more options."""
                    else:
                        task_content += """

Ask the user which time works best for them."""

                    logger.info(f"⚠️ FALLBACK: No {time_preference} slots, sending ALL {len(selected_slots_for_llm)} {alternative_time} slots")
                else:
                    # No slots for this date at all in any time, show other dates
                    available_dates = [f"{slots_by_date[date][0]['formatted_date']} ({len(slots_by_date[date])} slots)"
                                     for date in sorted(slots_by_date.keys()) if date != user_preferred_date]
                    dates_text = "\n- ".join(available_dates)
                    task_content = f"Sorry, no appointments available for {service.name} on {formatted_date}. We have appointments on these dates:\n\n- {dates_text}\n\nWhich date would you prefer?"
                    logger.info(f"❌ NO SLOTS: User's preferred date has no slots in any time period")
        else:
            # No time preference, show all slots for the day - SEND ALL (no limit)
            selected_slots_for_llm = day_slots
            formatted_date = day_slots[0]['formatted_date']

            # Check morning/afternoon distribution for better messaging
            morning_slots = [s for s in day_slots if 8 <= s['start_dt'].hour < 12]
            afternoon_slots = [s for s in day_slots if 12 <= s['start_dt'].hour < 19]

            task_content = f"""For {service.name} on {formatted_date}, I found {len(day_slots)} available time slots.

IMPORTANT INSTRUCTIONS:
1. Present only the FIRST 4-6 time slots to the user initially
2. After showing the first 4-6 slots, tell the user:"""

            if len(day_slots) > 6:
                if len(morning_slots) > 0 and len(afternoon_slots) > 0:
                    task_content += f"""
   - "We have a total of {len(day_slots)} slots on {formatted_date}"
   - "We have {len(morning_slots)} morning slots and {len(afternoon_slots)} afternoon slots"
   - Ask if they want to see more options or prefer a specific time period (morning/afternoon)"""
                else:
                    task_content += f"""
   - "We have a total of {len(day_slots)} slots available on {formatted_date}"
   - Ask if they want to see the additional time options"""

                task_content += """

Ask the user if any of the shown times work for them, or if they'd like to see more options."""
            else:
                task_content += """

Ask the user which time works best for them."""

            logger.info(f"✅ WHOLE DAY: Sending ALL {len(selected_slots_for_llm)} slots for {user_preferred_date}")

    elif not first_available_mode:
        # User's preferred date not available, show available dates
        available_dates = []
        for date_key in sorted(slots_by_date.keys()):
            day_slots = slots_by_date[date_key]
            formatted_date = day_slots[0]['formatted_date']
            slots_count = len(day_slots)
            available_dates.append(f"{formatted_date} ({slots_count} slots available)")

        dates_text = "\n- ".join(available_dates)
        if user_preferred_date:
            # Different message for automatic search (2nd+ service) vs user-chosen date (1st service)
            if is_automatic_search and first_appointment_date:
                # For 2nd+ services: Mention first appointment context, don't apologize
                task_content = f"Dal momento che il tuo primo appuntamento è il {first_appointment_date}, abbiamo disponibilità per {service.name} in queste date:\n\n- {dates_text}\n\nQuale data preferisci? Una volta scelta la data, ti mostrerò gli orari disponibili."
                logger.info(f"🤖 AUTOMATIC DATE SELECTION: First appointment {first_appointment_date}, showing alternatives for 2nd service")
            else:
                # For 1st service: Keep original apologetic message
                task_content = f"Sorry, no appointments available for {service.name} on your preferred date. We have appointments on these dates:\n\n- {dates_text}\n\nWhich date would you prefer? Once you choose a date, I'll show you the available times for that date."
                logger.info(f"❌ DATE UNAVAILABLE: User's preferred date {user_preferred_date} not in available dates")
        else:
            task_content = f"We have appointments available on these dates:\n\n- {dates_text}\n\nWhich date would you prefer? Then I'll show you the available times."
            logger.info(f"ℹ️ DATE SELECTION: Showing {len(slots_by_date)} available dates")

    # Step 3: Create MINIMAL slot data for LLM AND store full slot data globally
    if selected_slots_for_llm:
        # Store the selected slots for UUID lookup later
        global _current_session_slots
        _current_session_slots = {}

        minimal_slots_for_llm = []
        for slot in selected_slots_for_llm:
            time_key = slot['start_time_24h']
            _current_session_slots[time_key] = slot  # Store full slot data by time

            # Convert time to Italian words for natural speech (prevents TTS double-reading)
            time_italian = time_to_italian_words(time_key)

            minimal_slots_for_llm.append({
                'time': time_key,  # Keep numeric for UUID mapping
                'time_italian': time_italian,  # Italian words for speech
                'uuid': slot['providing_entity_availability_uuid'],
                'date': slot['date_key']
            })

        slot_context = {
            # Send Italian word times to LLM for natural speech
            'available_times': [slot['time_italian'] for slot in minimal_slots_for_llm],
            'service_name': service.name,
            'slot_count': len(minimal_slots_for_llm),
            # Keep numeric time→UUID mapping for function calls
            'time_to_uuid_map': {slot['time']: slot['uuid'] for slot in minimal_slots_for_llm},
            # Add Italian→UUID mapping for LLM convenience
            'italian_to_uuid_map': {slot['time_italian']: slot['uuid'] for slot in minimal_slots_for_llm}
        }

        logger.success(f"🚀 OPTIMIZED: Sending only {len(minimal_slots_for_llm)} slots to LLM instead of {len(slots)}")
        logger.info(f"🚀 Times (Italian): {[slot['time_italian'] for slot in minimal_slots_for_llm]}")
        logger.info(f"🚀 Italian→UUID mapping: {slot_context['italian_to_uuid_map']}")
    else:
        # No specific slots selected, just dates
        slot_context = {
            'available_dates': list(slots_by_date.keys()),
            'service_name': service.name,
            'total_slots': len(slots)
        }
        logger.info(f"🚀 DATE SELECTION: Sending date options to LLM")
    
    # NOTE: task_content and slot_context are already set by the smart filtering logic above
    
    return NodeConfig(
        name="slot_selection",
        role_messages=[{
            "role": "system",
            "content": f"""Help the patient select from available appointment slots for {service.name}.

🎯 SLOT PRESENTATION: {slot_context.get('slot_count', 'Unknown')} slots available.

📢 AVAILABLE TIMES (already in Italian - speak these EXACTLY as written):
{slot_context.get('available_times', [])}

🗣️ SPEECH RULES - CRITICAL:
- Times are PRE-CONVERTED to Italian words - speak them EXACTLY as shown
- Do NOT add numeric times - just say the Italian words directly
- Example: If list shows "otto e trenta", say "otto e trenta" (NOT "8:30, otto e trenta")

⚡ UUID MAPPING for function calls (Italian time → UUID):
{slot_context.get('italian_to_uuid_map', {})}

🚨 WHEN USER SELECTS A TIME:
1. User says Italian time (e.g., "otto e trenta" or "le otto e mezza")
2. Match to closest time in the mapping above
3. Use the UUID as providing_entity_availability_uuid

- Never mention prices, UUIDs, or technical details
- Be conversational and human

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_slot",
                handler=select_slot_and_book,
                description="Select a specific appointment slot by providing the slot UUID",
                properties={
                    "providing_entity_availability_uuid": {
                        "type": "string",
                        "description": "CRITICAL: Must be the UUID value from the time→UUID mapping (e.g., '05ee29df-7257-4beb-9b46-0efb0625d686'), NOT the time itself. Look up the time in the mapping and use the corresponding UUID value."
                    },
                    "selected_time": {
                        "type": "string",
                        "description": "Readable time of the selected slot (e.g., '09:30 - 10:00')"
                    },
                    "selected_date": {
                        "type": "string",
                        "description": "Readable date of the selected slot (e.g., '21 November 2025')"
                    }
                },
                required=["providing_entity_availability_uuid"]
            ),
            FlowsFunctionSchema(
                name="show_more_same_day_slots",
                handler=show_more_same_day_slots_handler,
                description="Show additional available slots on the same day as the earliest slot (only available after first available search)",
                properties={},
                required=[]
            ),
            FlowsFunctionSchema(
                name="search_different_date",
                handler=search_different_date_handler,
                description="Search for slots on a specific different date requested by the user",
                properties={
                    "new_date": {
                        "type": "string",
                        "description": "The new date to search for in YYYY-MM-DD format (e.g., '2025-11-08')"
                    },
                    "time_preference": {
                        "type": "string",
                        "description": "Time preference: 'morning', 'afternoon', or 'any time'",
                        "default": "any time"
                    }
                },
                required=["new_date"]
            ),
            FlowsFunctionSchema(
                name="update_date_preference",
                handler=update_date_and_search_slots,
                description="Update the preferred date when user chooses a different date from the available options and immediately search for slots",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "New preferred appointment date in YYYY-MM-DD format (e.g., '2025-11-26'). Must be one of the available dates shown above."
                    },
                    "time_preference": {
                        "type": "string",
                        "description": "Time preference: 'morning' (8:00-12:00), 'afternoon' (12:00-19:00), or 'any' (no preference). Default is to preserve existing time preference.",
                        "default": "preserve_existing"
                    }
                },
                required=["preferred_date"]
            )
        ]
    )


def create_booking_creation_node() -> NodeConfig:
    """Create booking confirmation and creation node"""
    return NodeConfig(
        name="booking_creation",
        role_messages=[{
            "role": "system",
            "content": f"Confirm booking details and create the appointment. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Perfect! I'm ready to book your appointment. Please confirm if you want to proceed with this booking."
        }],
        functions=[
            FlowsFunctionSchema(
                name="create_booking",
                handler=create_booking_and_transition,
                description="Create the appointment booking",
                properties={
                    "confirm_booking": {
                        "type": "boolean",
                        "description": "Confirmation to proceed with booking (true/false)"
                    }
                },
                required=["confirm_booking"]
            )
        ]
    )


def create_slot_refresh_node(service_name: str) -> NodeConfig:
    """Create slot refresh node when booking fails due to unavailability"""
    return NodeConfig(
        name="slot_refresh",
        role_messages=[{
            "role": "system",
            "content": f"The selected slot for {service_name} is no longer available. Search for new available slots and present them to the patient. Be apologetic and helpful. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"I apologize, but that time slot for {service_name} was just booked by someone else. Let me search for other available times for you."
        }],
        functions=[
            FlowsFunctionSchema(
                name="search_slots",
                handler=search_slots_and_transition,
                description="Search for updated available slots",
                properties={},
                required=[]
            )
        ],
        respond_immediately=True  # Automatically trigger slot search
    )


def create_no_slots_node(date: str, time_preference: str = "any time", first_appointment_date: str = None, is_automatic_search: bool = False) -> NodeConfig:
    """Create node when no slots are available - with human-like alternative suggestions

    Args:
        date: The date that was searched
        time_preference: Time preference used in search
        first_appointment_date: Date of first appointment (for 2nd+ services)
        is_automatic_search: True if this is automatic search for 2nd+ service (user didn't choose the date)
    """

    # Build constraint message for multi-service bookings
    date_constraint_msg = ""
    system_constraint_msg = ""

    if first_appointment_date:
        date_constraint_msg = f" IMPORTANT: Since this is your second appointment, it must be scheduled on or after your first appointment date ({first_appointment_date}). Please do not suggest any dates before {first_appointment_date}."
        system_constraint_msg = f"CRITICAL CONSTRAINT: This is a multi-service booking. The first appointment is on {first_appointment_date}. You MUST NOT suggest any dates before {first_appointment_date}. Only suggest dates on {first_appointment_date} or later dates. "

    # Different message tone for automatic search (2nd+ services) vs user-chosen date (1st service)
    if is_automatic_search and first_appointment_date:
        # For 2nd+ services: Don't apologize about the date since user didn't choose it
        # Mention the first appointment context and present alternatives naturally
        no_slots_message = f"Dal momento che il tuo primo appuntamento è il {first_appointment_date}, ho cercato disponibilità per il secondo servizio. Vorresti che controllassi altre date disponibili? Posso verificare gli orari disponibili nei giorni successivi."
    else:
        # For 1st service or user-chosen dates: Keep original apologetic tone
        if time_preference == "any time":
            no_slots_message = f"I'm sorry, there are no available slots for {date}.{date_constraint_msg} I'd like to suggest some alternatives: would you like to try a different date? I can check if there are available slots on nearby dates."
        else:
            no_slots_message = f"I'm sorry, there are no available slots for {date} for {time_preference}.{date_constraint_msg} I'd like to suggest some alternatives: would you like to try a different date or time? For example, we might have available slots for {date} at a different time or on another date."

    return NodeConfig(
        name="no_slots_available",
        role_messages=[{
            "role": "system",
            "content": f"{system_constraint_msg}We are in 2025. When there are no available slots, be helpful and suggest alternatives in a human way. Offer to search for different dates or times. Never mention technical details or UUIDs. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": no_slots_message
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_datetime",
                handler=collect_datetime_and_transition,
                description="Collect new preferred date and optional time preference",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "New preferred appointment date in YYYY-MM-DD format"
                    },
                    "preferred_time": {
                        "type": "string",
                        "description": "New preferred appointment time (specific time like '09:00', '14:30' or time range like 'morning', 'afternoon')"
                    },
                    "time_preference": {
                        "type": "string",
                        "description": "Time preference: 'morning' (8:00-12:00), 'afternoon' (12:00-19:00), 'specific' (exact time) or 'any' (no preference)"
                    }
                },
                required=["preferred_date"]
            )
        ]
    )



def create_booking_summary_confirmation_node(selected_services: List[HealthService], selected_slots: List[Dict], selected_center: HealthCenter, total_cost: float, is_cerba_member: bool = False) -> NodeConfig:
    """Create booking summary confirmation node with all details before personal info collection"""

    # Use full center name directly

    # Format service details
    # NOTE: Use service_name from booked_slots to support multi-service bundles and separate bookings
    services_text = []
    for i, slot in enumerate(selected_slots):
        # Convert UTC slot times to Italian local time for user display
        from services.timezone_utils import utc_to_italian_display

        italian_start = utc_to_italian_display(slot["start_time"])
        italian_end = utc_to_italian_display(slot["end_time"])

        # Fallback to original if conversion fails
        if not italian_start or not italian_end:
            logger.warning(f"⚠️ Timezone conversion failed for booking summary, using original times")
            start_time_str = slot["start_time"].replace("T", " ").replace("+00:00", "")
            end_time_str = slot["end_time"].replace("T", " ").replace("+00:00", "")
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        else:
            # Use converted Italian times
            start_dt = datetime.strptime(italian_start, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(italian_end, "%Y-%m-%d %H:%M:%S")

        formatted_date = start_dt.strftime("%d %B %Y")
        formatted_time = start_dt.strftime("%-H:%M")

        # Get service name from slot (supports bundle/combined/separate scenarios)
        service_name = slot.get("service_name", "Service")

        # Get the price from booked_slots (this is the correct multiplied price for bundles)
        service_cost = slot.get('price', 0)

        # Fallback to health_services only if price is missing from booked_slots
        if service_cost == 0 and 'health_services' in slot and len(slot['health_services']) > 0:
            health_service = slot['health_services'][0]
            if is_cerba_member:
                service_cost = health_service.get('cerba_card_price', health_service.get('price', 0))
            else:
                service_cost = health_service.get('price', 0)
            logger.warning(f"⚠️ Using fallback price from health_services for {service_name}: {service_cost}€")
        else:
            logger.info(f"✅ Using stored price for {service_name}: {service_cost}€")

        services_text.append(f"• {service_name} il {formatted_date} alle {formatted_time} - {int(service_cost)} euro")

    services_summary = "\n".join(services_text)

    # Create summary content
    membership_text = " (with Cerba Card discount)" if is_cerba_member else ""

    summary_content = f"""Here's a summary of your booking:

**Services:**
{services_summary}

**Health Center:**
{selected_center.name}

**Total Cost:** {int(total_cost)} euro{membership_text}

Would you like to proceed with this booking? If yes, I'll just need to collect some personal information to complete your appointment. If you'd like to change the time slot, I can show you other available times for the same service and date."""

    return NodeConfig(
        name="booking_summary_confirmation",
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        role_messages=[{
            "role": "system",
            "content": f"""Present EXACTLY the booking summary below. DO NOT hallucinate times/dates/prices.

Date/time formatting: Remove leading zeros ("07:30"→"7:30"), times ending :00 say "o'clock".

When user confirms/cancels/changes, call confirm_booking_summary function. DO NOT say "booking confirmed" - booking happens LATER. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": summary_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="confirm_booking_summary",
                handler=confirm_booking_summary_and_proceed,
                description="Confirm the booking summary and proceed to personal info collection or handle changes",
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["proceed", "cancel", "change"],
                        "description": "proceed: continue with current booking, cancel: stop completely, change: modify the time slot (keeps same service and date but shows other available times)"
                    }
                },
                required=["action"]
            )
        ]
    )

def create_center_search_processing_node(address: str, tts_message: str) -> NodeConfig:
    """Create a processing node that speaks immediately before performing center search"""
    from flows.handlers.booking_handlers import perform_center_search_and_transition

    return NodeConfig(
        name="center_search_processing",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing health center search in {address}. Immediately call perform_center_search to execute the actual search. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Now searching for health centers in {address} that provide all selected services. Please wait."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_center_search",
                handler=perform_center_search_and_transition,
                description="Execute the actual center search after TTS message",
                properties={},
                required=[]
            )
        ]
    )


def create_slot_search_processing_node(service_name: str, tts_message: str) -> NodeConfig:
    """Create a processing node that speaks immediately before performing slot search"""
    from flows.handlers.booking_handlers import perform_slot_search_and_transition

    return NodeConfig(
        name="slot_search_processing",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing slot search for {service_name}. Immediately call perform_slot_search to execute the actual search. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Now searching for available appointment slots for {service_name}. Please wait."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_slot_search",
                handler=perform_slot_search_and_transition,
                description="Execute the actual slot search after TTS message",
                properties={},
                required=[]
            )
        ]
    )


def create_automatic_slot_search_node(service_name: str, tts_message: str) -> NodeConfig:
    """
    Create a processing node for automatic slot search (for 2nd+ services in separate scenario)
    This node automatically searches for slots without asking user for date/time
    """
    from flows.handlers.booking_handlers import perform_slot_search_and_transition

    return NodeConfig(
        name="automatic_slot_search",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing automatic slot search for {service_name}. The date and time have been automatically calculated based on the first service. Immediately call perform_slot_search to execute the search. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Searching for available slots for {service_name} on the same day as your first service, starting 1 hour after your first appointment ends. Please wait."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_slot_search",
                handler=perform_slot_search_and_transition,
                description="Execute automatic slot search with pre-calculated date/time",
                properties={},
                required=[]
            )
        ]
    )


def create_slot_booking_processing_node(service_name: str, tts_message: str) -> NodeConfig:
    """Create a processing node that speaks immediately before performing slot booking"""
    from flows.handlers.booking_handlers import perform_slot_booking_and_transition

    return NodeConfig(
        name="slot_booking_processing",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing slot booking for {service_name}. Immediately call perform_slot_booking to execute the actual booking. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Now booking the selected time slot for {service_name}. Please wait for confirmation."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_slot_booking",
                handler=perform_slot_booking_and_transition,
                description="Execute the actual slot booking after TTS message",
                properties={},
                required=[]
            )
        ]
    )
