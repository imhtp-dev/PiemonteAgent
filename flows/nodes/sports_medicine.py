"""
Sports Medicine booking flow nodes.
Non-Agonistic flow (Phase 1). Agonistic detected → escalate to operator.

Flow:
  router → sports_medicine_type → sports_medicine_protocol → sports_medicine_address
  → sports_medicine_facility → sports_medicine_slots → sports_medicine_summary
  → sports_medicine_demographics → router (reset_context)
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema, ContextStrategyConfig, ContextStrategy
from config.settings import settings

from flows.handlers.sports_medicine_handlers import (
    handle_visit_type_selection,
    handle_protocol_selection,
    handle_address_collection,
    handle_facility_selection,
    handle_slot_selection,
    handle_summary_confirmation,
    handle_demographics_collection,
)


def create_sports_medicine_type_node(pre_detected_type: str = None) -> NodeConfig:
    """Ask agonistic vs non-agonistic, or auto-detect from context.

    Args:
        pre_detected_type: "agonistic", "non_agonistic", or None (ambiguous)
    """
    if pre_detected_type == "non_agonistic":
        task = (
            "The patient wants a NON-AGONISTIC (non-competitive) sports medical certificate. "
            "Confirm: 'Perfetto, procediamo con la visita non agonistica.' "
            "Then IMMEDIATELY call select_visit_type with visit_type='non_agonistic'."
        )
    elif pre_detected_type == "agonistic":
        task = (
            "The patient wants an AGONISTIC (competitive) sports medical certificate. "
            "Confirm: 'Perfetto, procediamo con la visita agonistica.' "
            "Then IMMEDIATELY call select_visit_type with visit_type='agonistic'."
        )
    else:
        task = (
            "The patient wants a sports medical certificate but hasn't specified the type. "
            "Ask: 'La visita è per attività sportiva agonistica o non agonistica?' "
            "Wait for their answer, then call select_visit_type."
        )

    return NodeConfig(
        name="sports_medicine_type",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà, a healthcare booking assistant for Cerba Healthcare sports medicine.
You are determining whether the patient needs an agonistic (competitive) or non-agonistic (recreational) visit.

AGONISTIC = competitive sports, official leagues, federations, certified athletes.
NON-AGONISTIC = recreational sports, gym, school PE, non-competitive activities.

If the patient already stated their type, DO NOT ask again. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": task
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_visit_type",
                handler=handle_visit_type_selection,
                description="Record whether the visit is agonistic or non-agonistic",
                properties={
                    "visit_type": {
                        "type": "string",
                        "enum": ["agonistic", "non_agonistic"],
                        "description": "agonistic = competitive, non_agonistic = recreational"
                    }
                },
                required=["visit_type"]
            )
        ],
        respond_immediately=True
    )


def create_sports_medicine_protocol_node() -> NodeConfig:
    """Ask Standard vs B1 extended protocol (non-agonistic only)."""
    return NodeConfig(
        name="sports_medicine_protocol",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà, a healthcare booking assistant for sports medicine.
The patient needs a non-agonistic visit. Now determine the protocol type.

Two options:
- **Protocollo Standard**: Visita clinica generale + ECG a riposo
- **Protocollo B1 (esteso)**: Visita clinica generale + ECG a riposo + ECG sotto sforzo + Spirometria + Esame urine

Explain both clearly and let the patient choose. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": (
                "Explain: 'Per la visita non agonistica ci sono due opzioni: "
                "il protocollo standard che include visita clinica ed ECG a riposo, "
                "oppure il protocollo B1 esteso che include anche ECG sotto sforzo, spirometria e esame urine. "
                "Quale preferisci?'"
            )
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_protocol",
                handler=handle_protocol_selection,
                description="Record protocol choice: standard or B1 extended",
                properties={
                    "is_b1": {
                        "type": "boolean",
                        "description": "true = B1 extended protocol, false = standard"
                    }
                },
                required=["is_b1"]
            )
        ]
    )


def create_sports_medicine_address_node() -> NodeConfig:
    """Collect patient address for region resolution via geocoding."""
    return NodeConfig(
        name="sports_medicine_address",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà collecting the patient's address to find nearby sports medicine facilities.
The address is used to determine which region they are in (Lombardia, Piemonte, etc.).
A city name is sufficient — full street address is not required.
{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": "Ask: 'In quale città ti trovi? Mi serve per cercare le sedi più vicine a te.'"
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_address",
                handler=handle_address_collection,
                description="Collect patient's city/address for region resolution",
                properties={
                    "address": {
                        "type": "string",
                        "description": "Patient's city or address (e.g. 'Milano', 'Torino', 'Via Roma 15 Milano')"
                    }
                },
                required=["address"]
            )
        ]
    )


def create_sports_medicine_facility_node(facilities: list, is_b1: bool = False) -> NodeConfig:
    """Present available facilities and let patient choose.

    Args:
        facilities: List of {ID, Name, PossAGO, Note} from Sedi/GetList
        is_b1: Whether B1 protocol is selected (affects available facilities message)
    """
    # Format facility list for prompt
    facility_lines = []
    for i, f in enumerate(facilities, 1):
        name = f.get("Name", "")
        note = f.get("Note", "")
        line = f"{i}. {name}"
        if note:
            line += f" ({note})"
        facility_lines.append(line)

    facility_text = "\n".join(facility_lines)
    protocol = "B1 esteso" if is_b1 else "standard"

    return NodeConfig(
        name="sports_medicine_facility",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà presenting available sports medicine facilities.
Protocol: {protocol}
Present the facilities naturally (not as a numbered list). Let the patient choose.

Available facilities:
{facility_text}

If patient asks for the closest or most convenient, help them choose based on the names.
{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": (
                f"Present the available facilities for the {protocol} protocol. "
                f"Say: 'Per il protocollo {protocol} ho trovato queste sedi disponibili nella tua zona:' "
                f"then list the facility names conversationally. "
                "Ask which one they prefer."
            )
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_facility",
                handler=handle_facility_selection,
                description="Patient selects a facility",
                properties={
                    "facility_index": {
                        "type": "integer",
                        "description": "1-based index of the selected facility from the list"
                    }
                },
                required=["facility_index"]
            )
        ]
    )


def create_sports_medicine_slots_node(slots: list, facility_name: str) -> NodeConfig:
    """Present available time slots.

    Args:
        slots: List of {Slot_ID, Slot_Date} from Slot/Find
        facility_name: Name of selected facility
    """
    # Format slots for prompt
    slot_lines = []
    for i, s in enumerate(slots, 1):
        slot_date = s.get("Slot_Date", "")
        slot_lines.append(f"{i}. {slot_date}")

    slot_text = "\n".join(slot_lines)

    return NodeConfig(
        name="sports_medicine_slots",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà presenting available appointment slots at {facility_name}.
Present dates and times in natural Italian (e.g., "lunedì 5 novembre alle 9:00").
Let the patient choose one.

Available slots:
{slot_text}

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": (
                f"Present the available slots at {facility_name}. "
                "Read the dates naturally in Italian. Ask which one they prefer."
            )
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_slot",
                handler=handle_slot_selection,
                description="Patient selects a time slot",
                properties={
                    "slot_index": {
                        "type": "integer",
                        "description": "1-based index of the selected slot"
                    }
                },
                required=["slot_index"]
            )
        ]
    )


def create_sports_medicine_summary_node(
    facility_name: str,
    slot_date: str,
    protocol: str,
    price_info: str = ""
) -> NodeConfig:
    """Read back booking summary and ask for confirmation.

    Args:
        facility_name: Selected facility name
        slot_date: Selected slot date/time
        protocol: "Standard Non-Agonistica" or "B1 Esteso Non-Agonistica"
        price_info: Formatted price string if available
    """
    price_section = f"\nPrezzo: {price_info}" if price_info else ""

    return NodeConfig(
        name="sports_medicine_summary",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà summarizing the sports medicine appointment before collecting patient details.

Appointment details:
- Sede: {facility_name}
- Data e ora: {slot_date}
- Protocollo: {protocol}{price_section}

Read the summary clearly. Ask if they want to:
1. Confirm and proceed (→ collect patient details)
2. Change date/time (→ back to slot search)
3. Choose a different facility (→ back to facility selection)

{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": (
                "Read back the appointment summary. "
                "Ask: 'Vuoi confermare, cambiare data/ora, o scegliere una sede diversa?'"
            )
        }],
        functions=[
            FlowsFunctionSchema(
                name="confirm_summary",
                handler=handle_summary_confirmation,
                description="Patient confirms, changes slot, or changes facility",
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["confirm", "change_slot", "change_facility"],
                        "description": "confirm = proceed to demographics, change_slot = new slot search, change_facility = pick different facility"
                    }
                },
                required=["action"]
            )
        ]
    )


def create_sports_medicine_demographics_node() -> NodeConfig:
    """Collect patient demographics for booking."""
    return NodeConfig(
        name="sports_medicine_demographics",
        role_messages=[{
            "role": "system",
            "content": f"""You are Voilà collecting patient information for a sports medicine booking.

Required fields (collect in this order):
1. Nome (first name)
2. Cognome (last name)
3. Sesso (M/F)
4. Data di nascita (format: YYYY/MM/DD)
5. Email
6. Telefono — Ask: "Il numero da cui sta chiamando è il suo recapito principale?" If no, ask for correct number.
7. Consenso promemoria — Ask: "Acconsente a ricevere promemoria per l'appuntamento?"

Optional (ask if available):
- Codice fiscale
- Luogo di nascita

Collect ALL required fields before calling submit_demographics.
Do NOT call the function until you have all required data.
{settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": (
                "Start collecting patient details. "
                "Say: 'Perfetto! Ora ho bisogno dei tuoi dati per completare la prenotazione. "
                "Qual è il tuo nome e cognome?'"
            )
        }],
        functions=[
            FlowsFunctionSchema(
                name="submit_demographics",
                handler=handle_demographics_collection,
                description="Submit all collected patient demographics",
                properties={
                    "nome": {
                        "type": "string",
                        "description": "First name"
                    },
                    "cognome": {
                        "type": "string",
                        "description": "Last name"
                    },
                    "sex": {
                        "type": "string",
                        "enum": ["M", "F"],
                        "description": "Gender"
                    },
                    "dt_nascita": {
                        "type": "string",
                        "description": "Date of birth in YYYY/MM/DD format"
                    },
                    "email": {
                        "type": "string",
                        "description": "Email address"
                    },
                    "telefono": {
                        "type": "string",
                        "description": "Phone number"
                    },
                    "consenso_promemoria": {
                        "type": "boolean",
                        "description": "Consent to receive appointment reminders"
                    },
                    "cod_fiscale": {
                        "type": "string",
                        "description": "Italian tax code (optional)"
                    },
                    "luogo_nascita": {
                        "type": "string",
                        "description": "Place of birth (optional)"
                    }
                },
                required=["nome", "cognome", "sex", "dt_nascita", "email", "telefono", "consenso_promemoria"]
            )
        ]
    )


