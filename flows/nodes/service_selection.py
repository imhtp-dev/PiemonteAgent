"""
Service search and selection nodes
"""

from typing import List
from pipecat_flows import NodeConfig, FlowsFunctionSchema

from models.requests import HealthService
from flows.handlers.service_handlers import (
    select_service_and_transition,
    refine_search_and_transition,
    search_health_services_and_transition
)
from config.settings import settings


def create_service_selection_node(services: List[HealthService] = None, search_term: str = "") -> NodeConfig:
    """Dynamically create enhanced service selection node with top 3 services"""
    if services:
        # Format services for presentation (top 3)
        top_services = services[:3]
        service_options = "\n".join([service.name for service in top_services])
        
        task_content = f"""I found these services for '{search_term}':

{service_options}

Choose one of these services, or tell me 'say the full service name' if none of these match what you're looking for."""
    else:
        task_content = "Choose one of the found services, or tell me 'say the full service name' for a more specific search."
    
    return NodeConfig(
        name="service_selection",
        role_messages=[{
            "role": "system",
            "content": f"Help the patient choose from the top 3 search results, and also tell them that if none of these services match, they should say the full service name to refine the search. **CRITICAL: NEVER use 1., 2., 3., or numbers when listing services. List only the service names separated by commas or line breaks, without numerical prefixes.** Speak naturally like a human. 🔇 SILENT FUNCTION CALLS: When calling select_service or refine_search, call it IMMEDIATELY with NO preceding text. Do NOT say 'Cerco', 'Un momento', 'Let me search' or similar — the system handles status messages automatically. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": task_content
        }],
        functions=[
            FlowsFunctionSchema(
                name="select_service",
                handler=select_service_and_transition,
                description="Select a specific service from search results",
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
                handler=refine_search_and_transition,
                description="Refine your search with a more specific service name",
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


def create_search_retry_node(error_message: str) -> NodeConfig:
    """Dynamically create node for search retry with custom error message"""
    return NodeConfig(
        name="search_retry",
        role_messages=[{
            "role": "system",
            "content": f"Help the patient try searching for the service again with a better term. 🔇 SILENT FUNCTION CALLS: When calling search_health_services, call it IMMEDIATELY with NO preceding text. Do NOT say 'Cerco', 'Un momento', 'Let me search' or similar — the system handles status messages automatically. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"{error_message} Try searching with the full service name."
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
        ]
    )


