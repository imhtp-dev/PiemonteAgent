"""
Patient Summary and Selective Editing Nodes
Handles patient confirmation and selective field editing for found patients
"""

from pipecat_flows import NodeConfig, FlowsFunctionSchema
from flows.handlers.patient_summary_handlers import (
    handle_patient_summary_response,
    handle_phone_edit
)
from config.settings import settings


def create_patient_summary_node(patient_data: dict) -> NodeConfig:
    """
    Create patient summary confirmation node

    Args:
        patient_data: Patient record from database lookup

    Returns:
        NodeConfig for patient summary confirmation
    """
    from services.patient_lookup import get_patient_summary_text

    summary_text = get_patient_summary_text(patient_data)

    return NodeConfig(
        name="patient_summary_confirmation",
        role_messages=[{
            "role": "system",
            "content": f"""Inform patient that their details are found in Cerba database and confirm phone number for SMS booking confirmation.

CRITICAL: When the patient says "si", "ok", "procedi", "confermo", or any confirmation → IMMEDIATELY call handle_summary_response with action="confirm_phone". Do NOT just acknowledge verbally — you MUST call the function.
When the patient wants to change the number → call handle_summary_response with action="change_phone".

Never say "booking confirmed" or "I'll let you know" — just call the function. {settings.language_config}"""
        }],
        task_messages=[{
            "role": "system",
            "content": summary_text
        }],
        functions=[
            FlowsFunctionSchema(
                name="handle_summary_response",
                handler=handle_patient_summary_response,
                description="Handle patient's response to phone verification - confirm or change phone number",
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["confirm_phone", "change_phone"],
                        "description": "User's choice: confirm_phone (proceed with this number) or change_phone (update phone number)"
                    }
                },
                required=["action"]
            )
        ]
    )


def create_name_edit_node() -> NodeConfig:
    """Create node for editing patient name"""
    return NodeConfig(
        name="edit_patient_name",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's corrected first and last name. Ask for both first name and last name. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Please tell me your correct first name and last name."
        }],
        functions=[
            FlowsFunctionSchema(
                name="update_name",
                handler=handle_name_edit,
                description="Update patient's first and last name",
                properties={
                    "first_name": {
                        "type": "string",
                        "description": "Patient's first name"
                    },
                    "last_name": {
                        "type": "string",
                        "description": "Patient's last name"
                    }
                },
                required=["first_name", "last_name"]
            )
        ]
    )


def create_phone_edit_node() -> NodeConfig:
    """Create node for editing patient phone number"""
    return NodeConfig(
        name="edit_patient_phone",
        role_messages=[{
            "role": "system",
            "content": f"Collect the patient's corrected phone number. Ask them to speak digit by digit slowly for better accuracy. {settings.language_config}"
        }],
        task_messages=[{
            "role": "system",
            "content": "Please tell me your correct phone number, digit by digit, slowly."
        }],
        functions=[
            FlowsFunctionSchema(
                name="update_phone",
                handler=handle_phone_edit,
                description="Update patient's phone number",
                properties={
                    "phone": {
                        "type": "string",
                        "description": "Patient's corrected phone number"
                    }
                },
                required=["phone"]
            )
        ]
    )


# FISCAL CODE EDIT NODE REMOVED - Fiscal code is now hardcoded for new patients