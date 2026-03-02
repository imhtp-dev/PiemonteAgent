"""
Doctor selection nodes for doctor-specific booking flow.
Presented when patient requests a specific doctor for their appointment.
"""

from typing import Dict, Any, List
from pipecat_flows import NodeConfig, FlowsFunctionSchema, ContextStrategyConfig, ContextStrategy
from loguru import logger

from flows.handlers.booking_handlers import (
    select_doctor_and_search_slots,
    handle_try_different_date_for_doctor,
    handle_show_available_doctors,
    handle_book_without_doctor,
)
from config.settings import settings


def create_doctor_selection_node(doctors: List[Dict], requested_name: str, is_alternative: bool = False) -> NodeConfig:
    """Present doctors to user for selection.

    Args:
        doctors: List of doctor dicts with keys: uuid, name, professional, score
        requested_name: Original doctor name from patient
        is_alternative: True when showing available doctors (not fuzzy matches)
    """
    # Build doctor list for prompt
    doctor_lines = []
    for i, doc in enumerate(doctors, 1):
        prof = doc["professional"]
        full_name = f"{prof['name']} {prof['surname']}"
        doctor_lines.append(f"{i}. Dottor {full_name} (ID: {doc['uuid']})")

    doctor_list_text = "\n".join(doctor_lines)

    if is_alternative:
        role_content = f"""The patient originally requested Dottor {requested_name}, but that doctor is not available.
These are the available doctors at this center for the requested service:

{doctor_list_text}

IMPORTANT: Do NOT say these doctors "match" the patient's request. They are ALTERNATIVE doctors available for this service.
Present them as available options. When the patient chooses one, call select_doctor with the doctor's UUID.

{settings.language_config}"""
        task_content = f"""Tell the patient: "Il Dottor {requested_name} non è disponibile. Ecco i medici disponibili per questa prestazione:"
Then list each doctor by full name. When the patient picks one, call select_doctor.

{settings.language_config}"""
    else:
        role_content = f"""The patient requested an appointment with "{requested_name}".
Multiple doctors matched that name. Present the options and let the patient choose.

Available doctors:
{doctor_list_text}

Ask the patient which doctor they'd like. When they choose, call select_doctor with the doctor's UUID.

{settings.language_config}"""
        task_content = f"""Present the matched doctors to the patient and ask them to choose.
Say something like: "Ho trovato più medici con quel nome. Quale preferisci?"
Then list each doctor by name. When the patient picks one, call select_doctor.

{settings.language_config}"""

    return NodeConfig(
        name="doctor_selection",
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        role_messages=[{
            "role": "system",
            "content": role_content
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_doctor",
                description="Select a doctor from the list",
                properties={
                    "doctor_uuid": {
                        "type": "string",
                        "description": "UUID of the selected providing entity (doctor)"
                    }
                },
                required=["doctor_uuid"],
                handler=select_doctor_and_search_slots,
            )
        ]
    )


def create_doctor_not_available_node(requested_name: str, available_doctors: List[Dict] = None) -> NodeConfig:
    """Doctor not found on this date. Ask: try another date or see available doctors?

    Args:
        requested_name: The doctor name patient requested
        available_doctors: Optional list of available doctors to suggest as alternatives
    """
    alternatives_text = ""
    if available_doctors:
        alt_lines = []
        for i, doc in enumerate(available_doctors[:3], 1):
            prof = doc["professional"]
            alt_lines.append(f"{i}. Dottor {prof['name']} {prof['surname']} (ID: {doc['uuid']})")
        alternatives_text = f"""

Alternative doctors available:
{chr(10).join(alt_lines)}

If the patient wants one of these, call select_doctor with their UUID."""

    from datetime import datetime
    today = datetime.now()
    today_date = today.strftime("%Y-%m-%d")
    today_formatted = today.strftime("%A, %B %d, %Y")

    return NodeConfig(
        name="doctor_not_available",
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        role_messages=[{
            "role": "system",
            "content": f"""The patient wanted Dottor {requested_name}, but this doctor has no availability for the selected date.
{alternatives_text}

Today is {today_formatted} (date: {today_date}). The current year is {settings.current_year}.
You can parse natural language dates (e.g., "28 April" → "2026-04-28", "next Monday" → calculate it).

Options for the patient:
1. Try a different date (call try_different_date) — if patient mentions a date, include preferred_date
2. See available doctors at this center (call show_available_doctors) — only if alternatives exist
3. Transfer to operator (use global request_transfer)

IMPORTANT: If the patient says something like "try 28 April", extract the date and pass it as preferred_date.

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": f"""Tell the patient: "Mi dispiace, il Dottor {requested_name} non ha disponibilità per questa data."
Then ask: "Vuoi provare con un'altra data, oppure posso mostrarti i medici disponibili?"

{settings.language_config}"""
        }],
        functions=[
            FlowsFunctionSchema(
                name="try_different_date",
                description="Patient wants to try a different date for the same doctor. If they mention a date, include it.",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format if patient mentions one (e.g., 'try 28 April' → '2026-04-28')"
                    }
                },
                required=[],
                handler=handle_try_different_date_for_doctor,
            ),
            FlowsFunctionSchema(
                name="show_available_doctors",
                description="Show available doctors at this center",
                properties={},
                required=[],
                handler=handle_show_available_doctors,
            ),
        ] + ([
            FlowsFunctionSchema(
                name="select_doctor",
                description="Select an alternative doctor",
                properties={
                    "doctor_uuid": {
                        "type": "string",
                        "description": "UUID of the selected providing entity (doctor)"
                    }
                },
                required=["doctor_uuid"],
                handler=select_doctor_and_search_slots,
            )
        ] if available_doctors else [])
    )


def create_no_doctors_for_date_node(requested_name: str) -> NodeConfig:
    """No doctors available at this center for the selected date.
    Patient can: try different date, book without doctor preference, or transfer.
    """
    from datetime import datetime
    today = datetime.now()
    today_date = today.strftime("%Y-%m-%d")
    today_formatted = today.strftime("%A, %B %d, %Y")

    return NodeConfig(
        name="no_doctors_for_date",
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        role_messages=[{
            "role": "system",
            "content": f"""No doctors have availability at this center for the date the patient selected.
The patient originally wanted Dottor {requested_name}.

Today is {today_formatted} (date: {today_date}). The current year is {settings.current_year}.
You can parse natural language dates (e.g., "28 April" → "2026-04-28", "next Monday" → calculate it).

Options:
1. Try a different date (call try_different_date) — if patient mentions a date, include preferred_date
2. Book without a specific doctor preference (call book_without_doctor) — if patient mentions a date, include preferred_date
3. Transfer to operator (use global request_transfer)

IMPORTANT: If the patient says something like "check for 28 April" or "try next week without doctor", extract the date and pass it as preferred_date. This avoids asking them for the date again.

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": f"""Tell the patient: "Mi dispiace, non ci sono medici disponibili per questa data presso questo centro."
Then ask: "Vuoi provare con un'altra data, oppure posso cercare la disponibilità senza preferenza di medico?"

{settings.language_config}"""
        }],
        functions=[
            FlowsFunctionSchema(
                name="try_different_date",
                description="Patient wants to try a different date for the same doctor. If they mention a date, include it.",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format if patient mentions one (e.g., 'try 28 April' → '2026-04-28')"
                    }
                },
                required=[],
                handler=handle_try_different_date_for_doctor,
            ),
            FlowsFunctionSchema(
                name="book_without_doctor",
                description="Continue booking without doctor preference. If they mention a date, include it.",
                properties={
                    "preferred_date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format if patient mentions one"
                    }
                },
                required=[],
                handler=handle_book_without_doctor,
            ),
        ]
    )
