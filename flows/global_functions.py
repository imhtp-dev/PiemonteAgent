"""
Global Function Schemas
8 global functions available at every node in the conversation.
"""

from pipecat_flows import FlowsFunctionSchema

from flows.handlers.global_handlers import (
    global_knowledge_base,
    global_competitive_pricing,
    global_non_competitive_pricing,
    global_exam_by_visit,
    global_exam_by_sport,
    global_clinic_info,
    global_request_transfer,
    global_start_booking,
)


# ============================================================================
# GLOBAL FUNCTIONS LIST
# Order matters - first functions appear first in LLM's function list
# ============================================================================

GLOBAL_FUNCTIONS = [
    # 1. Knowledge Base - General FAQ queries
    FlowsFunctionSchema(
        name="knowledge_base_new",
        description="ONLY when user explicitly asks an info question (e.g., 'Come si fa...', 'Cosa devo portare...', 'È obbligatorio...', 'Siete convenzionati...'). Do NOT call this when user is answering your question, selecting a service, confirming something, or providing personal info like name/address/date.",
        properties={
            "query": {
                "type": "string",
                "description": "Natural language question in Italian"
            }
        },
        required=["query"],
        handler=global_knowledge_base,
    ),

    # 2. Competitive (Agonistic) Pricing
    FlowsFunctionSchema(
        name="get_competitive_pricing",
        description="ONLY when user explicitly asks about agonistic/competitive sports visit pricing. Requires all 4 parameters: age, gender, sport name, and region. Do NOT call this when user is answering your question or providing personal info.",
        properties={
            "age": {
                "type": "integer",
                "description": "Athlete's age in years"
            },
            "gender": {
                "type": "string",
                "enum": ["M", "F"],
                "description": "Gender: M for male, F for female"
            },
            "sport": {
                "type": "string",
                "description": "Sport name in Italian (calcio, nuoto, pallavolo, etc.)"
            },
            "region": {
                "type": "string",
                "description": "Italian region or province (Lombardia, Piemonte, Milano, etc.)"
            }
        },
        required=["age", "gender", "sport", "region"],
        handler=global_competitive_pricing,
    ),

    # 3. Non-Competitive Pricing
    FlowsFunctionSchema(
        name="get_price_non_agonistic_visit",
        description="ONLY when user explicitly asks about non-agonistic/non-competitive sports visit pricing. Do NOT call this when user is answering your question or providing personal info.",
        properties={
            "ecg_under_stress": {
                "type": "boolean",
                "description": "True if ECG under stress (ECG sotto sforzo) is required, False for resting ECG"
            }
        },
        required=["ecg_under_stress"],
        handler=global_non_competitive_pricing,
    ),

    # 4. Exam List by Visit Type
    FlowsFunctionSchema(
        name="get_exam_by_visit",
        description="ONLY when user explicitly asks what exams are required for a visit type code. Valid codes: A1, A2, A3 (agonistic), B1, B2, B3, B4, B5 (non-agonistic). Do NOT call this when user is answering your question.",
        properties={
            "visit_type": {
                "type": "string",
                "enum": ["A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5"],
                "description": "Visit type code"
            }
        },
        required=["visit_type"],
        handler=global_exam_by_visit,
    ),

    # 5. Exam List by Sport
    FlowsFunctionSchema(
        name="get_exam_by_sport",
        description="ONLY when user explicitly asks what exams are required for a specific sport (e.g., 'Cosa prevede la visita per il calcio?'). Do NOT call this when user is answering your question.",
        properties={
            "sport": {
                "type": "string",
                "description": "Sport name in Italian (calcio, nuoto, pallavolo, basket, tennis, etc.)"
            }
        },
        required=["sport"],
        handler=global_exam_by_sport,
    ),

    # 6. Clinic Info (Call Graph)
    FlowsFunctionSchema(
        name="call_graph",
        description="ONLY when user explicitly asks about clinic hours, closures, or doctors (e.g., 'Che orari avete?', 'Quando siete aperti?', 'Siete aperti sabato?'). Do NOT call this when user is providing a city/address as an answer to your question or confirming a center selection.",
        properties={
            "query": {
                "type": "string",
                "description": "Natural language query including location (e.g., 'orari della sede di Milano', 'chiusure estive Novara', 'medici cardiologi a Torino')"
            }
        },
        required=["query"],
        handler=global_clinic_info,
    ),

    # 7. Request Transfer (NOW ASKS WHAT USER NEEDS FIRST)
    FlowsFunctionSchema(
        name="request_transfer",
        description="ONLY when user EXPLICITLY asks to speak to a human operator (e.g., 'voglio parlare con un operatore', 'passami un umano', 'operatore'). NEVER use this for booking actions, slot selections, confirmations, or any other purpose. If user is confirming a time, date, or service, use the node-specific function instead. Agent tries to help first, transfers only if it fails.",
        properties={
            "reason": {
                "type": "string",
                "description": "Reason for transfer request"
            }
        },
        required=["reason"],
        handler=global_request_transfer,
    ),

    # 8. Start Booking (TRANSITIONS)
    FlowsFunctionSchema(
        name="start_booking",
        description="ONLY when user explicitly asks to book a NEW appointment (e.g., 'Voglio prenotare...', 'Prenota per me...', 'Devo prenotare...'). Do NOT call this when a booking is already in progress or when user is answering your question.",
        properties={
            "service_request": {
                "type": "string",
                "description": "What the patient wants to book (e.g., 'visita sportiva', 'esame del sangue', 'ecografia')"
            }
        },
        required=["service_request"],
        handler=global_start_booking,
    ),
]
