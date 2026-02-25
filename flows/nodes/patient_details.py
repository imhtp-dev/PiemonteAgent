"""
Patient details collection nodes for booking finalization
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema, ContextStrategyConfig, ContextStrategy
from flows.handlers.patient_detail_handlers import (
    collect_first_name_and_transition,
    collect_surname_and_transition,
    collect_phone_and_transition,
    confirm_phone_and_transition,
    # EMAIL REMOVED: collect_email_and_transition,
    # EMAIL REMOVED: confirm_email_and_transition,
    collect_reminder_authorization_and_transition,
    collect_marketing_authorization_and_transition,
    confirm_details_and_create_booking
)
from config.settings import settings


def create_collect_full_name_node() -> NodeConfig:
    """
    Create first name collection node

    IMPORTANT: Context is reset at this node to clear heavy slot data from previous booking search.
    This prevents context window bloat while keeping only essential booking summary.
    """
    return create_collect_first_name_node()


def create_collect_first_name_node() -> NodeConfig:
    """
    Create first name collection node (nome)

    IMPORTANT: Context is reset at this node to clear heavy slot data from previous booking search.
    This prevents context window bloat while keeping only essential booking summary.
    """
    return NodeConfig(
        name="collect_first_name",
        context_strategy=ContextStrategyConfig(strategy=ContextStrategy.RESET),
        role_messages=[{
            "role": "system",
            "content": f"You are a healthcare booking agent collecting patient details. Collect the patient's first name only (nome). Do NOT ask for surname yet. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "What is your first name? (Solo il nome, per favore)"
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_first_name",
                handler=collect_first_name_and_transition,
                description="Collect the patient's first name only",
                properties={
                    "first_name": {
                        "type": "string",
                        "description": "Patient's first name only (nome)"
                    }
                },
                required=["first_name"]
            )
        ]
    )


def create_collect_surname_node() -> NodeConfig:
    """Create surname collection node (cognome)"""
    return NodeConfig(
        name="collect_surname",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's surname/last name only (cognome). {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "And what is your surname? (E il cognome?)"
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_surname",
                handler=collect_surname_and_transition,
                description="Collect the patient's surname/last name only",
                properties={
                    "surname": {
                        "type": "string",
                        "description": "Patient's surname/last name only (cognome)"
                    }
                },
                required=["surname"]
            )
        ]
    )


def create_collect_phone_node() -> NodeConfig:
    """Create phone number collection node"""
    return NodeConfig(
        name="collect_phone",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's phone number. Ask them to speak digit by digit slowly for better accuracy. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Can you tell me if the phone you're calling from matches your official number? If yes, just say 'yes'. If not, tell me your phone number digit by digit. Slowly! IMPORTANT: When the user says 'yes', 'si', or 'sì', call collect_phone with their exact confirmation word."
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_phone",
                handler=collect_phone_and_transition,
                description="Collect the patient's phone number or their confirmation response to use the caller ID. ALWAYS pass the user's exact response in the phone parameter.",
                properties={
                    "phone": {
                        "type": "string",
                        "description": "The exact user response: either their phone number digits OR their confirmation word (yes/si/sì/correct) if they want to use caller ID"
                    }
                },
                required=["phone"]
            )
        ]
    )


# EMAIL NODE REMOVED - create_collect_email_node was here


def create_collect_reminder_authorization_node() -> NodeConfig:
    """Create reminder authorization collection node"""
    return NodeConfig(
        name="collect_reminder_authorization",
        role_messages=[{
            "role": "system",
            "content": f"Ask if the patient wants to receive SMS reminders for their appointment. Wait for their explicit response before calling the function. Only call the function when they clearly say yes/no or similar. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Would you like to receive an SMS reminder for your scheduled appointment? Please say 'yes' or 'no'."
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_reminder_authorization",
                handler=collect_reminder_authorization_and_transition,
                description="Collect preference for reminder authorization based on user's explicit response",
                properties={
                    "reminder_authorization": {
                        "type": "boolean",
                        "description": "Whether the patient wants to receive appointment reminders (true for yes, false for no)"
                    }
                },
                required=["reminder_authorization"]
            )
        ]
    )


def create_collect_marketing_authorization_node() -> NodeConfig:
    """Create marketing authorization collection node"""
    return NodeConfig(
        name="collect_marketing_authorization",
        role_messages=[{
            "role": "system",
            "content": f"Ask if the patient wants to receive marketing updates from Cerba HealthCare. Wait for their explicit response before calling the function. Only call the function when they clearly say yes/no or similar. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Would you like to receive updates about Cerba HealthCare? Please say 'yes' or 'no'."
        }],
        functions=[
            FlowsFunctionSchema(
                name="collect_marketing_authorization",
                handler=collect_marketing_authorization_and_transition,
                description="Collect preference for marketing authorization based on user's explicit response",
                properties={
                    "marketing_authorization": {
                        "type": "boolean",
                        "description": "Whether the patient wants to receive marketing updates (true for yes, false for no)"
                    }
                },
                required=["marketing_authorization"]
            )
        ]
    )



def create_confirm_phone_node(phone: str) -> NodeConfig:
    """Create phone confirmation node"""
    from flows.handlers.patient_detail_handlers import confirm_phone_and_transition
    return NodeConfig(
        name="confirm_phone",
        role_messages=[{
            "role": "system",
            "content": f"Always write 'più' for '+' before phone number. Ask the user to confirm their phone number and WAIT for their response. Only call the confirm_phone function AFTER the user has spoken and given their response. If they say yes/correct/confirm, use action='confirm'. If they want to change it, use action='change'. Do NOT call any function until the user has responded. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": f"I have your phone number as: più{' '.join(phone)}. Is this correct? Say \"yes\" if it's correct, or \"change\" if you want to provide a different number."
        }],
        functions=[
            FlowsFunctionSchema(
                name="confirm_phone",
                handler=confirm_phone_and_transition,
                description="Confirm the phone number or request to change it",
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["confirm", "change"],
                        "description": "confirm if phone is correct, change if user wants to modify it"
                    }
                },
                required=["action"]
            )
        ]
    )


# EMAIL NODE REMOVED - create_confirm_email_node was here




# booking_processing removed — consolidated into inline handler (confirm_details_and_create_booking)
