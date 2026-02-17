"""
Second service search, selection, and sorting nodes for multi-service booking.
Used when patient requests two services — after first booking completes,
these nodes handle searching and booking the second service.
"""

from typing import List
from pipecat_flows import NodeConfig, FlowsFunctionSchema

from models.requests import HealthService
from config.settings import settings


def create_second_service_search_node(service_text: str, tts_message: str) -> NodeConfig:
    """Processing node that searches for the second service after first booking completes."""
    from flows.handlers.second_service_handlers import perform_second_service_search_and_transition

    return NodeConfig(
        name="second_service_search",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing a search for an additional health service. Immediately call perform_search to execute the search. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Now searching for '{service_text}'. Please wait for results."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_search",
                handler=perform_second_service_search_and_transition,
                description="Execute the search for the second service",
                properties={},
                required=[]
            )
        ]
    )


def create_second_service_selection_node(services: List[HealthService], search_term: str) -> NodeConfig:
    """Selection node for choosing from multiple second-service search results."""
    from flows.handlers.second_service_handlers import (
        select_second_service_and_transition,
        refine_second_service_search_and_transition
    )

    top_services = services[:3]
    service_options = "\n".join([service.name for service in top_services])

    task_content = f"""I found these services for '{search_term}':

{service_options}

Choose one of these services, or tell me 'say the full service name' if none of these match what you're looking for."""

    return NodeConfig(
        name="second_service_selection",
        role_messages=[{
            "role": "system",
            "content": f"The patient's first service has already been booked. Now help them choose from the search results for their second service. **CRITICAL: NEVER use 1., 2., 3., or numbers when listing services.** Speak naturally like a human. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_service",
                handler=select_second_service_and_transition,
                description="Select a service from search results",
                properties={
                    "service_uuid": {
                        "type": "string",
                        "description": "UUID of the selected health service"
                    }
                },
                required=["service_uuid"]
            ),
            FlowsFunctionSchema(
                name="refine_search",
                handler=refine_second_service_search_and_transition,
                description="Refine search with a more specific service name",
                properties={
                    "refined_search_term": {
                        "type": "string",
                        "description": "More specific service name for refined search"
                    }
                },
                required=["refined_search_term"]
            )
        ]
    )


def create_second_service_sorting_node(service_name: str, tts_message: str) -> NodeConfig:
    """Processing node that calls sorting API for the second service at existing center."""
    from flows.handlers.second_service_handlers import perform_second_service_sorting_and_transition

    return NodeConfig(
        name="second_service_sorting",
        pre_actions=[
            {
                "type": "tts_say",
                "text": tts_message
            }
        ],
        role_messages=[{
            "role": "system",
            "content": f"You are processing the booking setup for {service_name}. Immediately call perform_sorting to continue. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"Processing {service_name} at the selected center. Please wait."
        }],
        functions=[
            FlowsFunctionSchema(
                name="perform_sorting",
                handler=perform_second_service_sorting_and_transition,
                description="Run sorting API for the second service",
                properties={},
                required=[]
            )
        ]
    )
