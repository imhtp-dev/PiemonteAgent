"""
Patient information collection flow handlers
"""

import re
from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs


async def collect_address_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect address and dynamically transition to gender collection"""
    address = args.get("address", "").strip()
    
    if address:
        flow_manager.state["patient_address"] = address
        logger.info(f"📍 Address collected: {address}")
        
        from flows.nodes.patient_info import create_collect_gender_node
        return {"success": True, "address": address}, create_collect_gender_node()
    else:
        return {"success": False, "message": "Please provide your address"}, None


async def collect_gender_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect gender and dynamically transition to DOB collection"""
    gender = args.get("gender", "").lower()

    if gender in ["m", "f", "male", "female", "maschio", "femmina"]:
        # Normalize to m/f
        normalized_gender = "m" if gender in ["m", "male", "maschio"] else "f"
        flow_manager.state["patient_gender"] = normalized_gender
        logger.info(f"👤 Gender collected: {normalized_gender}")

        from flows.nodes.patient_info import create_collect_dob_node
        return {"success": True, "gender": normalized_gender}, create_collect_dob_node()
    else:
        return {"success": False, "message": "Please specify Male or Female"}, None


async def collect_dob_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect date of birth and dynamically transition to birth city collection"""
    dob = args.get("date_of_birth", "").strip()

    # Convert various date formats to YYYY-MM-DD
    if dob:
        # This is a simplified conversion - you might want more robust date parsing
        try:
            # Assume format is already YYYY-MM-DD or convert from common formats
            if re.match(r'^\d{4}-\d{2}-\d{2}$', dob):
                flow_manager.state["patient_dob"] = dob
                logger.info(f"📅 DOB collected: {dob}")

                # Skip birth city collection - go directly to verification
                address = flow_manager.state.get("patient_address", "")
                gender = flow_manager.state.get("patient_gender", "")

                from flows.nodes.patient_info import create_verify_basic_info_node
                return {"success": True, "date_of_birth": dob}, create_verify_basic_info_node(address, gender, dob)
            else:
                return {"success": False, "message": "Please provide your date of birth again"}, None
        except Exception:
            return {"success": False, "message": "Invalid date format. Use YYYY-MM-DD"}, None
    else:
        return {"success": False, "message": "Please provide your date of birth"}, None


# BIRTH CITY HANDLER REMOVED - No longer needed without fiscal code generation


async def verify_basic_info_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Verify basic patient information and transition to health center search"""
    action = args.get("action", "")

    if action == "confirm":
        logger.info("✅ Basic patient information verified, proceeding to health center search")

        intent = flow_manager.state.get("intent")

        if intent == "price_inquiry":
            # Price inquiry — skip orange box/flow navigation, go straight to center search
            from flows.nodes.booking import create_final_center_search_node
            return {
                "success": True,
                "message": "Basic information verified, searching for health centers"
            }, create_final_center_search_node()

        # Normal booking flow — orange box
        from flows.nodes.booking import create_orange_box_node
        return {
            "success": True,
            "message": "Basic information verified, searching for health centers"
        }, create_orange_box_node()

    elif action == "change":
        field_to_change = args.get("field_to_change", "")
        new_value = args.get("new_value", "").strip()

        if not field_to_change or not new_value:
            return {"success": False, "message": "Please specify what you want to change"}, None

        # Update the specific field in state
        if field_to_change == "address":
            flow_manager.state["patient_address"] = new_value
            logger.info(f"📍 Address updated to: {new_value}")
        elif field_to_change == "gender":
            # Normalize gender
            normalized_gender = "m" if new_value.lower() in ["m", "male", "maschio"] else "f"
            flow_manager.state["patient_gender"] = normalized_gender
            logger.info(f"👤 Gender updated to: {normalized_gender}")
        elif field_to_change == "date_of_birth":
            flow_manager.state["patient_dob"] = new_value
            logger.info(f"📅 DOB updated to: {new_value}")

        # Create new verification node with updated values
        address = flow_manager.state.get("patient_address", "")
        gender = flow_manager.state.get("patient_gender", "")
        dob = flow_manager.state.get("patient_dob", "")

        from flows.nodes.patient_info import create_verify_basic_info_node
        return {
            "success": True,
            "message": f"Updated {field_to_change}. Please verify again.",
            "field_updated": field_to_change,
            "new_value": new_value
        }, create_verify_basic_info_node(address, gender, dob)

    else:
        logger.info("🔄 Invalid action, restarting address collection")

        # Clear state and restart
        flow_manager.state.pop("patient_address", None)
        flow_manager.state.pop("patient_gender", None)
        flow_manager.state.pop("patient_dob", None)

        from flows.nodes.patient_info import create_collect_address_node
        return {
            "success": False,
            "message": "Let's collect your information again."
        }, create_collect_address_node()