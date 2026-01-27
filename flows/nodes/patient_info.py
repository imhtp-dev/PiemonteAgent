"""
Patient information collection nodes
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema
from flows.handlers.patient_handlers import (
    collect_address_and_transition,
    collect_gender_and_transition,
    collect_dob_and_transition,
    verify_basic_info_and_transition
)
from config.settings import settings
from utils.italian_time import date_to_italian_words


def create_collect_address_node() -> NodeConfig:
    """Create address collection node"""
    return NodeConfig(
        name="collect_address",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's address to find nearby health centers. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Perfect! Now I need your address or city to find nearby health centers. Please tell me your address."
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_address",
                handler=collect_address_and_transition,
                description="Collect the patient's address",
                properties={
                    "address": {
                        "type": "string",
                        "description": "Patient's address or city"
                    }
                },
                required=["address"]
            )
        ]
    )


def create_collect_gender_node() -> NodeConfig:
    """Create gender collection node"""
    return NodeConfig(
        name="collect_gender",
        role_messages=[{
            "role": "system",
            "content": f"Ask patient's gender. When user answers, call collect_gender function. 'termina/termine' = 'femmina' (STT error). {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Please tell me your gender. Are you male or female?"
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_gender",
                handler=collect_gender_and_transition,
                description="Collect patient's gender",
                properties={
                    "gender": {
                        "type": "string",
                        "description": "Patient's gender (male/female)"
                    }
                },
                required=["gender"]
            )
        ]
    )


def create_collect_dob_node() -> NodeConfig:
    """Create DOB collection node"""
    return NodeConfig(
        name="collect_dob",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's date of birth for the booking. Be flexible with date formats and internally convert any natural language date to YYYY-MM-DD format. Never tell the user format requirements. Just ask and let the LLM handle the conversion. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Could you give me your date of birth?"
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_dob",
                handler=collect_dob_and_transition,
                description="Collect the patient's date of birth",
                properties={
                    "date_of_birth": {
                        "type": "string",
                        "description": "Date of birth in YYYY-MM-DD format"
                    }
                },
                required=["date_of_birth"]
            )
        ]
    )


# BIRTH CITY NODE REMOVED - No longer needed without fiscal code generation


def create_verify_basic_info_node(address: str, gender: str, dob: str) -> NodeConfig:
    """Create verification node for address, gender, and DOB (birth city removed)"""
    # Italian gender words for natural speech
    gender_italian = "maschio" if gender.lower() == "m" else "femmina" if gender.lower() == "f" else gender

    # Convert DOB to Italian words for natural TTS (e.g., "2007-04-27" → "ventisette aprile duemilaesette")
    dob_italian = date_to_italian_words(dob)

    return NodeConfig(
        name="verify_basic_info",
        role_messages=[{
            "role": "system",
            "content": f"""Present patient info for verification in a NATURAL FLOWING SENTENCE.

🚨 TTS-FRIENDLY OUTPUT RULES (CRITICAL):
- NEVER use bullet points, lists, or line breaks
- NEVER format as "Sesso: X, Data: Y" - this sounds robotic when spoken
- Speak in ONE natural Italian sentence that flows smoothly
- The date of birth is ALREADY in Italian words - speak it exactly as provided
- Example of GOOD output: "Il tuo sesso è maschio, la tua data di nascita è quindici aprile millenovecentonovanta, e il tuo indirizzo è Milano. È tutto corretto?"
- Example of BAD output: "Sesso: Maschio\\nData di nascita: 15/04/1990\\nIndirizzo: Milano"

When user responds, call verify_basic_info: confirms → action="confirm", changes → action="change" + field_to_change + new_value. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": f"""Say EXACTLY this (one natural sentence, no lists): "Ricapitoliamo le informazioni che mi hai fornito. Il sesso che hai indicato è {gender_italian}, la data di nascita è {dob_italian}, e l'indirizzo è {address}. È tutto corretto? Dimmi di sì oppure cosa devo modificare." """
        }],
        functions=[
            FlowsFunctionSchema(
                name="verify_basic_info",
                handler=verify_basic_info_and_transition,
                description="Handle verification response - confirm all details or update specific field",
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["confirm", "change"],
                        "description": "confirm if user says yes, change if user wants to modify something"
                    },
                    "field_to_change": {
                        "type": "string",
                        "enum": ["address", "gender", "date_of_birth"],
                        "description": "Which field to change (only if action is 'change')"
                    },
                    "new_value": {
                        "type": "string",
                        "description": "New value for the field (only if action is 'change')"
                    }
                },
                required=["action"]
            )
        ]
    )

def create_flow_processing_node(service_name: str, tts_message: str) -> NodeConfig:
    """Create a processing node that speaks immediately before performing flow generation"""
    from flows.handlers.flow_handlers import perform_flow_generation_and_transition

    return NodeConfig(
        name="flow_processing",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing flow generation for {service_name}. Immediately call perform_flow_generation to execute the actual flow analysis. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Now analyzing {service_name} for special requirements and additional options. Please wait."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_flow_generation",
                handler=perform_flow_generation_and_transition,
                description="Execute the actual flow generation after TTS message",
                properties={},
                required=[]
            )
        ]
    )
