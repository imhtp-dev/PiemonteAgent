"""
Greeting and initial conversation nodes
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema, ContextStrategy, ContextStrategyConfig
from flows.handlers.service_handlers import search_health_services_and_transition
from config.settings import settings


def create_greeting_node(initial_booking_request: str = None) -> NodeConfig:
    """Create the initial greeting node with automatic search trigger if coming from info agent

    Args:
        initial_booking_request: If provided, LLM will immediately search for this service
    """

    # Build task message based on whether we have a pre-filled request
    if initial_booking_request:
        task_content = f"""The user has already requested to book: "{initial_booking_request}"

IMMEDIATELY call search_health_services with search_term="{initial_booking_request}" to find matching services.
Do NOT ask the user what they want - they already told you. Just acknowledge and search.

Example response: "Perfetto, cerco subito i servizi disponibili per {initial_booking_request}."
Then call the function. {settings.language_config}"""
    else:
        task_content = f"""Say: 'Sono Ualà, assistente virtuale di Cerba HealthCare. Quale servizio vorresti prenotare?'

When the user mentions ANY service name, immediately call search_health_services to search for it. {settings.language_config}"""

    return NodeConfig(
        name="greeting",
        role_messages=[{
            "role": "system",
            "content": f"You are Ualà, a calm and friendly virtual assistant (female voice) for Cerba Healthcare. Speak with warmth and clarity like a human, not like a robot. {settings.language_config}"
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
        respond_immediately=True,  # Bot should start the conversation
        # Reset context to clear the router prompt — its aggressive instructions
        # ("NEVER answer without calling a function", call_graph examples, etc.)
        # cause global function misfires throughout booking. The greeting node is
        # self-contained: service_request is passed via task_messages, not history.
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
    )