"""
Patient Summary and Selective Editing Handlers
Handles patient confirmation and selective field editing for found patients
"""

from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.patient_lookup import (
    get_patient_id_for_logging,
    normalize_phone
)


async def handle_patient_summary_response(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Handle patient's response to database summary confirmation

    Args:
        args: Flow arguments containing user's action choice
        flow_manager: Pipecat flow manager instance

    Returns:
        Response data and next node configuration
    """
    action = args.get("action", "")
    patient_id = flow_manager.state.get("patient_db_id", "unknown")

    logger.info(f"📋 Patient summary response: action={action}, patient_id={patient_id}")

    if action == "confirm_phone":
        logger.success(f"✅ Patient {patient_id} confirmed phone number, proceeding to authorization")

        # Skip directly to reminder authorization (last two nodes before booking)
        from flows.nodes.patient_details import create_collect_reminder_authorization_node
        return {
            "success": True,
            "message": "Perfect! Phone number confirmed. Proceeding to final authorization"
        }, create_collect_reminder_authorization_node()

    elif action == "change_phone":
        logger.info(f"📞 Patient {patient_id} wants to change phone number")

        from flows.nodes.patient_summary import create_phone_edit_node
        return {
            "success": True,
            "message": "Let's update your phone number"
        }, create_phone_edit_node()

    else:
        # Invalid action, re-present summary
        logger.warning(f"❓ Invalid action '{action}' from patient {patient_id}")

        # Get current patient data from state to recreate summary
        current_patient = {
            "first_name": flow_manager.state.get("patient_name", ""),
            "last_name": flow_manager.state.get("patient_surname", ""),
            "phone": flow_manager.state.get("patient_phone", "")
        }

        from flows.nodes.patient_summary import create_patient_summary_node
        return {
            "success": False,
            "message": "I didn't understand. Please say 'correct' to confirm the phone number or 'change phone' to update it."
        }, create_patient_summary_node(current_patient)


async def handle_name_edit(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Handle patient name editing

    Args:
        args: Flow arguments containing corrected name
        flow_manager: Pipecat flow manager instance

    Returns:
        Response data and next node configuration
    """
    first_name = args.get("first_name", "").strip()
    last_name = args.get("last_name", "").strip()
    patient_id = flow_manager.state.get("patient_db_id", "unknown")

    if not first_name or not last_name:
        return {
            "success": False,
            "message": "Please provide both your first name and last name"
        }, None

    # Update state with corrected names
    flow_manager.state["patient_name"] = first_name
    flow_manager.state["patient_surname"] = last_name

    logger.info(f"📝 Name updated for patient {patient_id}: {first_name} {last_name}")

    # Return to summary with updated data
    updated_patient = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": flow_manager.state.get("patient_phone", "")
    }

    from flows.nodes.patient_summary import create_patient_summary_node
    return {
        "success": True,
        "message": f"Name updated to {first_name} {last_name}. Please verify your information again."
    }, create_patient_summary_node(updated_patient)


async def handle_phone_edit(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Handle patient phone number editing

    Args:
        args: Flow arguments containing corrected phone
        flow_manager: Pipecat flow manager instance

    Returns:
        Response data and next node configuration
    """
    phone = args.get("phone", "").strip()
    patient_id = flow_manager.state.get("patient_db_id", "unknown")

    if not phone:
        return {
            "success": False,
            "message": "Please provide your phone number"
        }, None

    # Normalize phone number
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return {
            "success": False,
            "message": "Please provide a valid phone number"
        }, None

    # Update state with corrected phone
    flow_manager.state["patient_phone"] = normalized_phone

    logger.info(f"📞 Phone updated for patient {patient_id}: {normalized_phone}")

    # Return to summary with updated data
    updated_patient = {
        "first_name": flow_manager.state.get("patient_name", ""),
        "last_name": flow_manager.state.get("patient_surname", ""),
        "phone": normalized_phone
    }

    from flows.nodes.patient_summary import create_patient_summary_node
    return {
        "success": True,
        "message": f"Phone number updated. Please verify your information again."
    }, create_patient_summary_node(updated_patient)


async def handle_fiscal_code_edit(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Handle patient fiscal code editing

    Args:
        args: Flow arguments containing corrected fiscal code
        flow_manager: Pipecat flow manager instance

    Returns:
        Response data and next node configuration
    """
    fiscal_code = args.get("fiscal_code", "").strip().upper()
    patient_id = flow_manager.state.get("patient_db_id", "unknown")

    if not fiscal_code:
        return {
            "success": False,
            "message": "Please provide your fiscal code"
        }, None

    # Basic fiscal code validation (16 characters, alphanumeric)
    if len(fiscal_code) != 16:
        return {
            "success": False,
            "message": "Fiscal code must be exactly 16 characters"
        }, None

    # Update state with corrected fiscal code
    flow_manager.state["generated_fiscal_code"] = fiscal_code

    logger.info(f"🆔 Fiscal code updated for patient {patient_id}: {fiscal_code}")

    # Return to summary with updated data
    updated_patient = {
        "first_name": flow_manager.state.get("patient_name", ""),
        "last_name": flow_manager.state.get("patient_surname", ""),
        "phone": flow_manager.state.get("patient_phone", ""),
        "fiscal_code": fiscal_code
    }

    from flows.nodes.patient_summary import create_patient_summary_node
    return {
        "success": True,
        "message": f"Fiscal code updated. Please verify your information again."
    }, create_patient_summary_node(updated_patient)