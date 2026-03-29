"""
Greeting and initial conversation nodes
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema
from flows.handlers.service_handlers import search_health_services_and_transition
from config.settings import settings


def create_greeting_node(initial_booking_request: str = None, additional_service_request: str = None, intent: str = None, center_hint: str = None) -> NodeConfig:
    """Create the initial greeting node with automatic search trigger if coming from info agent

    Args:
        initial_booking_request: If provided, LLM will immediately search for this service
        additional_service_request: If provided, second service the patient also wants to book
        intent: "price_inquiry" or None — controls the acknowledge message
        center_hint: If provided, patient already named a center/city — skip "alcune informazioni" message
    """

    # Build task message based on whether we have a pre-filled request
    if initial_booking_request:
        if additional_service_request:
            acknowledge = (
                f'The user wants to book TWO services: "{initial_booking_request}" AND "{additional_service_request}".\n\n'
                f'Acknowledge BOTH services first, then search for the FIRST one.\n'
                f'Example: "Perfetto! Hai richiesto {initial_booking_request} e {additional_service_request}. '
                f'Procediamo prima con {initial_booking_request}, poi passeremo a {additional_service_request}."\n\n'
                f'IMMEDIATELY call search_health_services with search_term="{initial_booking_request}".\n'
                f'Do NOT ask the user what they want - they already told you. {settings.language_config}'
            )
        elif intent == "price_inquiry":
            if center_hint:
                acknowledge = (
                    f'The user wants to know about: "{initial_booking_request}" at "{center_hint}"\n\n'
                    f'First say: "Verifico subito."\n'
                    f'Then IMMEDIATELY call search_health_services with search_term="{initial_booking_request}".\n'
                    f'Do NOT ask the user what they want - they already told you. {settings.language_config}'
                )
            else:
                acknowledge = (
                    f'The user wants to know about: "{initial_booking_request}"\n\n'
                    f'First say: "Per verificare dovrò chiederti alcune informazioni."\n'
                    f'Then IMMEDIATELY call search_health_services with search_term="{initial_booking_request}".\n'
                    f'Do NOT ask the user what they want - they already told you. {settings.language_config}'
                )
        else:
            acknowledge = (
                f'The user has already requested to book: "{initial_booking_request}"\n\n'
                f'IMMEDIATELY call search_health_services with search_term="{initial_booking_request}" to find matching services.\n'
                f'Do NOT ask the user what they want - they already told you. Just acknowledge and search.\n\n'
                f'Example response: "Perfetto, cerco subito i servizi disponibili per {initial_booking_request}."\n'
                f'Then call the function. {settings.language_config}'
            )
        task_content = acknowledge
    else:
        task_content = f"""Say: 'Sono Voilà, l\'assistente virtuale di Serba Healthcare. Posso fornirti informazioni su tutte le prestazioni offerte dai nostri centri. Dimmi pure!'

When the user mentions ANY service name, immediately call search_health_services to search for it. {settings.language_config}"""

    return NodeConfig(
        name="greeting",
        role_messages=[{
            "role": "system",
            "content": f"You are Voilà, a calm and friendly virtual assistant (female voice) for Serba Healthcare. Speak with warmth and clarity like a human, not like a robot. 🔇 SILENT FUNCTION CALLS: When calling search_health_services, call it IMMEDIATELY with NO preceding text. Do NOT say 'Cerco', 'Un momento', 'Let me search' or similar — the system handles status messages automatically. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="search_health_services",
                handler=search_health_services_and_transition,
                description="Search health services using fuzzy search",
                properties={
                    "search_term": {
                        "type": "string",
                        "description": "Name of the service to search for (e.g. 'cardiology', 'blood tests', 'ankle x-ray')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 3, maximum: 5)",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 5
                    }
                },
                required=["search_term"]
            )
        ],
        respond_immediately=True  # Bot should start the conversation
    )