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
        description="Search knowledge base for general info about services, exam preparations, required documents, forms, company policies, and booking process questions. Use for: 'Come si fa...', 'Cosa devo portare...', 'È obbligatorio...', 'Siete convenzionati...', 'Come prenotare?'",
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
        description="Get price for agonistic/competitive sports medical visit. Requires all 4 parameters: age, gender, sport name, and region.",
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
        description="Get price for non-agonistic/non-competitive sports medical visit. Only need to know if ECG under stress is required.",
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
        description="Get list of required exams for a specific visit type code. Valid codes: A1, A2, A3 (agonistic), B1, B2, B3, B4, B5 (non-agonistic).",
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
        description="Get list of required exams for a specific sport. Use when patient asks 'Cosa prevede la visita per il calcio?'",
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
        description="Get clinic information: opening hours, closures, blood collection times, available doctors. Include location in query.",
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
        description="When user asks for human operator, call this to ask what they need help with. DO NOT say you will transfer - instead say 'Dimmi di cosa hai bisogno, se non riesco ad aiutarti ti trasferirò a un operatore.' Agent tries to help first, transfers only if it fails.",
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
        description="Start appointment booking flow. Use when patient wants to book an appointment: 'Voglio prenotare...', 'Prenota per me...', 'Devo prenotare...'",
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
