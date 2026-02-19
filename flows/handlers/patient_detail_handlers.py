"""
Patient detail collection flow handlers
"""

from typing import Dict, Any, Tuple
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs
from services.call_logger import call_logger



async def collect_first_name_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect patient first name and transition to surname collection"""
    first_name = args.get("first_name", "").strip()

    if not first_name or len(first_name) < 2:
        return {"success": False, "message": "Please provide your first name"}, None

    # Store first name separately
    flow_manager.state["patient_first_name"] = first_name

    logger.info(f"👤 Patient first name collected: {first_name}")

    from flows.nodes.patient_details import create_collect_surname_node
    return {
        "success": True,
        "first_name": first_name,
        "message": "First name collected successfully"
    }, create_collect_surname_node()


async def collect_surname_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect patient surname and transition to phone collection"""
    surname = args.get("surname", "").strip()

    if not surname or len(surname) < 2:
        return {"success": False, "message": "Please provide your surname"}, None

    # Store surname separately
    flow_manager.state["patient_surname"] = surname

    # Also store combined full name for backward compatibility
    first_name = flow_manager.state.get("patient_first_name", "")
    flow_manager.state["patient_full_name"] = f"{first_name} {surname}"

    logger.info(f"👤 Patient surname collected: {surname}")
    logger.info(f"👤 Full name: {first_name} {surname}")

    from flows.nodes.patient_details import create_collect_phone_node
    return {
        "success": True,
        "surname": surname,
        "full_name": f"{first_name} {surname}",
        "message": "Surname collected successfully"
    }, create_collect_phone_node()


async def collect_phone_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect patient phone and transition to phone confirmation"""
    phone = args.get("phone", "").strip().lower()
    raw_phone = args.get("phone", "")  # Keep original for debugging

    # Check if user confirmed to use the calling number
    caller_phone_from_talkdesk = flow_manager.state.get("caller_phone_from_talkdesk", "")

    # DEBUG: Log what we received
    call_logger.log_phone_debug("PHONE_CONFIRMATION_ATTEMPT", {
        "user_said": phone,
        "raw_user_input": raw_phone,  # Add raw input for debugging
        "caller_phone_from_talkdesk": caller_phone_from_talkdesk,
        "flow_state_keys": list(flow_manager.state.keys()),
        "all_flow_state": {k: str(v)[:100] for k, v in flow_manager.state.items()}  # Truncate long values
    })

    # CRITICAL DEBUG: If phone is empty, log this as a major issue
    if not phone.strip():
        call_logger.log_error(Exception("LLM called collect_phone with EMPTY phone parameter!"), {
            "raw_args": str(args),
            "expected": "User response should be passed in phone parameter",
            "debug_tip": "Check LLM function calling behavior"
        })

    # If user says "yes" and we have caller phone from Talkdesk, use it
    if phone in ["yes", "si", "sì", "correct", "okay", "ok", "va bene"] and caller_phone_from_talkdesk:
        phone_clean = ''.join(filter(str.isdigit, caller_phone_from_talkdesk))
        logger.info(f"📞 Using caller's phone number from Talkdesk: {phone_clean}")
    else:
        # DEBUG: Why didn't we use the Talkdesk phone?
        if phone in ["yes", "si", "sì", "correct", "okay", "ok", "va bene"]:
            call_logger.log_error(Exception("User confirmed but NO caller_phone_from_talkdesk found!"), {
                "user_input": phone,
                "expected_phone": "caller_phone_from_talkdesk should exist",
                "flow_state": {k: str(v)[:50] for k, v in flow_manager.state.items()}
            })
        else:
            call_logger.log_phone_debug("USER_DID_NOT_CONFIRM", {
                "user_input": phone,
                "reason": "User provided different phone number"
            })
        # User provided a different phone number
        if not phone or len(phone) < 8:
            return {"success": False, "message": "Please provide a valid phone number"}, None

        # Clean phone number (remove spaces, dashes, etc.)
        phone_clean = ''.join(filter(str.isdigit, phone))

        if len(phone_clean) < 8:
            return {"success": False, "message": "Please provide a valid phone number with at least 8 digits"}, None

        logger.info(f"📞 Patient provided different phone: {phone_clean}")

    # Store phone in state
    flow_manager.state["patient_phone"] = phone_clean

    logger.info(f"📞 Patient phone collected: {phone_clean}")

    # CRITICAL: Check if user confirmed using caller phone (said "yes")
    # If YES → skip phone confirmation and go directly to reminder authorization
    # If NO (provided different phone) → go to phone confirmation
    user_confirmed_caller_phone = phone in ["yes", "si", "sì", "correct", "okay", "ok", "va bene"] and caller_phone_from_talkdesk

    if user_confirmed_caller_phone:
        # User confirmed caller phone - skip confirmation, go directly to reminder authorization
        # EMAIL COLLECTION REMOVED - go straight to authorizations
        logger.info(f"✅ User confirmed caller phone - skipping confirmation, going to reminder authorization")
        from flows.nodes.patient_details import create_collect_reminder_authorization_node
        return {
            "success": True,
            "phone": phone_clean,
            "message": "Phone number confirmed (caller phone)",
            "skipped_confirmation": True
        }, create_collect_reminder_authorization_node()
    else:
        # User provided different phone - need confirmation
        logger.info(f"📞 User provided different phone - going to confirmation node")
        from flows.nodes.patient_details import create_confirm_phone_node
        return {
            "success": True,
            "phone": phone_clean,
            "message": "Phone number collected successfully"
        }, create_confirm_phone_node(phone_clean)


async def confirm_phone_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Confirm phone number and transition to reminder authorization (email removed)"""
    action = args.get("action", "")

    if action == "confirm":
        # EMAIL COLLECTION REMOVED - go straight to reminder authorization
        logger.info("✅ Phone confirmed, proceeding to reminder authorization")

        from flows.nodes.patient_details import create_collect_reminder_authorization_node
        return {
            "success": True,
            "message": "Phone confirmed, proceeding to authorization questions"
        }, create_collect_reminder_authorization_node()

    elif action == "change":
        logger.info("🔄 Phone needs to be changed, returning to phone collection")

        from flows.nodes.patient_details import create_collect_phone_node
        return {
            "success": False,
            "message": "Let's collect your phone number again"
        }, create_collect_phone_node()

    else:
        return {"success": False, "message": "Please confirm if the phone number is correct or if you want to change it"}, None


# EMAIL COLLECTION REMOVED - Flow now goes directly from phone to reminder authorization


async def collect_reminder_authorization_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect reminder authorization and transition to marketing authorization"""
    reminder_auth = args.get("reminder_authorization", False)

    # Store reminder authorization in state
    flow_manager.state["reminder_authorization"] = reminder_auth

    logger.info(f"📧 Reminder authorization: {'Yes' if reminder_auth else 'No'}")

    from flows.nodes.patient_details import create_collect_marketing_authorization_node
    return {
        "success": True,
        "reminder_authorization": reminder_auth,
        "message": "Reminder preference collected"
    }, create_collect_marketing_authorization_node()


async def collect_marketing_authorization_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Collect marketing authorization and proceed directly to booking completion"""
    marketing_auth = args.get("marketing_authorization", False)

    # COMPREHENSIVE DEBUG LOGGING FOR FINAL STEP
    logger.info("🔍 DEBUG: === MARKETING AUTHORIZATION HANDLER ===")
    logger.info(f"🔍 DEBUG: Args received: {args}")
    logger.info(f"🔍 DEBUG: marketing_auth = {marketing_auth}")

    # Store marketing authorization in state
    flow_manager.state["marketing_authorization"] = marketing_auth

    logger.info(f"📢 Marketing authorization: {'Yes' if marketing_auth else 'No'}")
    logger.info("✅ All patient details collected, proceeding directly to final booking")

    # Log current state before final booking
    logger.info(f"🔍 DEBUG: State keys before final booking: {list(flow_manager.state.keys())}")
    logger.info(f"🔍 DEBUG: selected_slot exists: {'selected_slot' in flow_manager.state}")
    logger.info(f"🔍 DEBUG: booked_slots exists: {'booked_slots' in flow_manager.state}")

    # Skip bulk verification - proceed directly to booking creation
    logger.info("🔍 DEBUG: Calling confirm_details_and_create_booking...")
    try:
        result = await confirm_details_and_create_booking({"details_confirmed": True}, flow_manager)
        logger.info(f"🔍 DEBUG: confirm_details_and_create_booking returned: {result}")
        return result
    except Exception as e:
        logger.error(f"❌ DEBUG: Exception in confirm_details_and_create_booking: {e}")
        raise


async def confirm_details_and_create_booking(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Confirm patient details and create final booking"""
    details_confirmed = args.get("details_confirmed", False)

    if not details_confirmed:
        # If details not confirmed, restart full name collection
        logger.info("🔄 Patient details not confirmed, restarting collection")
        from flows.nodes.patient_details import create_collect_full_name_node
        return {
            "success": False,
            "message": "Let's collect your details again"
        }, create_collect_full_name_node()

    logger.info("✅ Patient details confirmed, proceeding to final booking")

    # COMPREHENSIVE DEBUG LOGGING - Track exactly what we have in state
    logger.info("🔍 DEBUG: Starting booking data validation")
    logger.info(f"🔍 DEBUG: Complete flow_manager.state keys: {list(flow_manager.state.keys())}")

    # Get all required data from state
    selected_services = flow_manager.state.get("selected_services", [])
    booked_slots = flow_manager.state.get("booked_slots", [])

    logger.info(f"🔍 DEBUG: selected_services = {selected_services}")
    logger.info(f"🔍 DEBUG: selected_services type = {type(selected_services)}")
    logger.info(f"🔍 DEBUG: selected_services length = {len(selected_services) if selected_services else 'None'}")

    logger.info(f"🔍 DEBUG: booked_slots = {booked_slots}")
    logger.info(f"🔍 DEBUG: booked_slots type = {type(booked_slots)}")
    logger.info(f"🔍 DEBUG: booked_slots length = {len(booked_slots) if booked_slots else 'None'}")

    # Check if selected_slot exists
    selected_slot_exists = "selected_slot" in flow_manager.state
    selected_slot_value = flow_manager.state.get("selected_slot", "NOT_FOUND")
    logger.info(f"🔍 DEBUG: selected_slot exists? {selected_slot_exists}")
    logger.info(f"🔍 DEBUG: selected_slot value = {selected_slot_value}")

    # If booked_slots is empty but we have selected_slot, this is a CRITICAL ERROR
    # The slot should have been reserved via create_slot() API in select_slot_and_book()
    if not booked_slots and "selected_slot" in flow_manager.state:
        logger.error("❌ CRITICAL ERROR: booked_slots is empty but selected_slot exists!")
        logger.error("❌ This means slot reservation (create_slot API) was skipped or failed!")
        selected_slot = flow_manager.state["selected_slot"]
        logger.error(f"❌ selected_slot data = {selected_slot}")

        # This should NOT happen in the fixed flow, but provide fallback with clear error
        logger.error("❌ FALLBACK: Cannot create valid booking without reserved slot UUID")
        logger.error("❌ The providing_entity_availability_uuid cannot be used for final booking")

        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Slot reservation failed - cannot complete booking"
        }, create_error_node("Slot reservation failed. The time slot was not properly reserved. Please start the booking process again.")
    else:
        if booked_slots:
            logger.info(f"🔍 DEBUG: booked_slots already exists: {booked_slots}")
        if not selected_slot_exists:
            logger.error("❌ DEBUG: No selected_slot found in state - this is a problem!")

    # Get patient data with extensive logging - using separate first name and surname
    patient_first_name = flow_manager.state.get("patient_first_name", "")
    patient_surname = flow_manager.state.get("patient_surname", "")
    patient_phone = flow_manager.state.get("patient_phone", "")
    patient_email = flow_manager.state.get("patient_email", "")

    patient_found_in_db = flow_manager.state.get("patient_found_in_db", False)

    # Use separate first name and surname for API
    patient_name = patient_first_name

    logger.info(f"🔍 DEBUG: patient_first_name = '{patient_first_name}'")
    logger.info(f"🔍 DEBUG: patient_surname = '{patient_surname}'")
    logger.info(f"🔍 DEBUG: patient_name (for API) = '{patient_name}'")
    logger.info(f"🔍 DEBUG: patient_surname (for API) = '{patient_surname}'")
    logger.info(f"🔍 DEBUG: patient_phone = '{patient_phone}'")
    logger.info(f"🔍 DEBUG: patient_email = '{patient_email}'")

    # Also check for patient data from test setup
    patient_data_dict = flow_manager.state.get("patient_data", {})
    patient_gender = flow_manager.state.get("patient_gender", patient_data_dict.get("gender", "m"))
    patient_dob = flow_manager.state.get("patient_dob", patient_data_dict.get("date_of_birth", ""))

    logger.info(f"🔍 DEBUG: patient_data_dict = {patient_data_dict}")
    logger.info(f"🔍 DEBUG: patient_gender = '{patient_gender}'")
    logger.info(f"🔍 DEBUG: patient_dob = '{patient_dob}'")

    reminder_auth = flow_manager.state.get("reminder_authorization", False)
    marketing_auth = flow_manager.state.get("marketing_authorization", False)

    logger.info(f"🔍 DEBUG: reminder_authorization = {reminder_auth}")
    logger.info(f"🔍 DEBUG: marketing_authorization = {marketing_auth}")

    # Detailed validation check
    # Note: email is optional per Cerba API (nullable string)
    validation_results = {
        "selected_services": bool(selected_services),
        "booked_slots": bool(booked_slots),
        "patient_name": bool(patient_name),
        "patient_surname": bool(patient_surname),
        "patient_phone": bool(patient_phone)
    }

    logger.info(f"🔍 DEBUG: Validation results: {validation_results}")

    missing_fields = [field for field, is_valid in validation_results.items() if not is_valid]
    if missing_fields:
        logger.error(f"❌ DEBUG: Missing required fields: {missing_fields}")

        # Log the specific values of missing fields
        for field in missing_fields:
            if field == "selected_services":
                logger.error(f"❌ {field}: {selected_services}")
            elif field == "booked_slots":
                logger.error(f"❌ {field}: {booked_slots}")
            elif field == "patient_name":
                logger.error(f"❌ {field}: '{patient_name}'")
            elif field == "patient_surname":
                logger.error(f"❌ {field}: '{patient_surname}'")
            elif field == "patient_phone":
                logger.error(f"❌ {field}: '{patient_phone}'")

    if not all([selected_services, booked_slots, patient_name, patient_surname,
                patient_phone]):
        logger.error("❌ FINAL VALIDATION FAILED - Creating error node")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Missing required information for booking"
        }, create_error_node("Missing required information for booking. Please start over.")

    try:
        # Store booking parameters for processing node (including service_groups for proper slot mapping)
        flow_manager.state["pending_booking_params"] = {
            "selected_services": selected_services,
            "booked_slots": booked_slots,
            "service_groups": flow_manager.state.get("service_groups", []),
            "booking_scenario": flow_manager.state.get("booking_scenario", "legacy"),
            "patient_name": patient_name,
            "patient_surname": patient_surname,
            "patient_phone": patient_phone,
            "patient_email": patient_email,
            "patient_gender": patient_gender,
            "patient_dob": patient_dob,
            "reminder_auth": reminder_auth,
            "marketing_auth": marketing_auth,
            # Include patient UUID for existing patients (API optimization)
            "patient_found_in_db": patient_found_in_db,
            "patient_db_id": flow_manager.state.get("patient_db_id", "")
        }

        # Create intermediate node with pre_actions for immediate TTS
        booking_status_text = "Creazione della prenotazione con tutti i dettagli forniti. Attendi..."

        from flows.nodes.patient_details import create_booking_processing_node
        return {
            "success": True,
            "message": "Starting booking creation"
        }, create_booking_processing_node(booking_status_text)

    except Exception as e:
        logger.error(f"❌ Booking creation initialization error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Booking creation failed. Please try again."
        }, create_error_node("Booking creation failed. Please try again.")


async def perform_booking_creation_and_transition(args: FlowArgs, flow_manager: FlowManager) -> Tuple[Dict[str, Any], NodeConfig]:
    """Perform the actual booking creation after TTS message"""
    try:
        # COMPREHENSIVE DEBUG LOGGING FOR BOOKING CREATION
        logger.info("🔍 DEBUG: === BOOKING CREATION STARTED ===")
        logger.info(f"🔍 DEBUG: Args received: {args}")

        # Get stored booking parameters
        params = flow_manager.state.get("pending_booking_params", {})
        logger.info(f"🔍 DEBUG: pending_booking_params exists: {bool(params)}")
        logger.info(f"🔍 DEBUG: pending_booking_params keys: {list(params.keys()) if params else 'EMPTY'}")

        if not params:
            logger.error("❌ DEBUG: No pending_booking_params found!")
            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": "Missing booking parameters"
            }, create_error_node("Missing booking parameters. Please start over.")

        # Extract parameters
        selected_services = params["selected_services"]
        booked_slots = params["booked_slots"]
        service_groups = params.get("service_groups", [])
        booking_scenario = params.get("booking_scenario", "legacy")
        patient_name = params["patient_name"]
        patient_surname = params["patient_surname"]
        patient_phone = params["patient_phone"]
        patient_email = params["patient_email"]
        patient_gender = params["patient_gender"]
        patient_dob = params["patient_dob"]
        reminder_auth = params["reminder_auth"]
        marketing_auth = params["marketing_auth"]
        # Get patient UUID for existing patients
        patient_found_in_db = params.get("patient_found_in_db", False)
        patient_db_id = params.get("patient_db_id", "")

        logger.info(f"🔍 DEBUG: Extracted booking parameters:")
        logger.info(f"   - selected_services: {selected_services}")
        logger.info(f"   - booked_slots: {booked_slots}")
        logger.info(f"   - patient_name: '{patient_name}'")
        logger.info(f"   - patient_surname: '{patient_surname}'")
        logger.info(f"   - patient_phone: '{patient_phone}'")
        logger.info(f"   - patient_email: '{patient_email}'")
        logger.info(f"   - patient_gender: '{patient_gender}'")
        logger.info(f"   - patient_dob: '{patient_dob}'")
        logger.info(f"   - reminder_auth: {reminder_auth}")
        logger.info(f"   - marketing_auth: {marketing_auth}")
        logger.info(f"   - patient_found_in_db: {patient_found_in_db}")
        logger.info(f"   - patient_db_id: '{patient_db_id}'")

        # Import and call booking service with retry logic
        from services.booking_api import create_booking
        from utils.api_retry import retry_api_call

        # Prepare booking data - ONLY UUID for existing patients, full details for new patients
        if patient_found_in_db and patient_db_id:
            # Existing patient: API only needs UUID (backend has all patient info)
            patient_data = {"uuid": patient_db_id}
            logger.info(f"✅ Using simplified payload with patient UUID only: {patient_db_id}")
        else:
            # New patient: Send all required details
            patient_data = {
                "name": patient_name,
                "surname": patient_surname,
                "email": patient_email,
                "phone": patient_phone,
                "date_of_birth": patient_dob,
                "gender": patient_gender.upper()
            }
            logger.info("📝 Creating booking for new patient with full details")

        booking_data = {
            "patient": patient_data,
            "booking_type": "private",
            "health_services": [],
            "reminder_authorization": reminder_auth,
            "marketing_authorization": marketing_auth
        }

        # Add health services with their slot UUIDs (group-aware mapping)
        logger.info(f"🔍 BOOKING API MAPPING: Scenario={booking_scenario}, Groups={len(service_groups)}, Slots={len(booked_slots)}")

        if booking_scenario in ["bundle", "separate", "combined"] and service_groups:
            # Use service_groups structure for proper slot mapping
            for group_index, service_group in enumerate(service_groups):
                if group_index < len(booked_slots):
                    slot_uuid = booked_slots[group_index]["slot_uuid"]
                    group_services = service_group["services"]
                    is_bundled = service_group.get("is_group", False)

                    logger.info(f"   Group {group_index}: {len(group_services)} services, bundled={is_bundled}, slot={slot_uuid}")

                    # Add ALL services in this group with the SAME slot UUID
                    for service in group_services:
                        booking_data["health_services"].append({
                            "uuid": service.uuid,
                            "slot": slot_uuid
                        })
                        logger.info(f"      → Mapped {service.name} (UUID: {service.uuid}) to slot {slot_uuid}")
        else:
            # Legacy mode: 1:1 mapping
            logger.info(f"   Using legacy 1:1 mapping")
            for i, service in enumerate(selected_services):
                if i < len(booked_slots):
                    booking_data["health_services"].append({
                        "uuid": service.uuid,
                        "slot": booked_slots[i]["slot_uuid"]
                    })
                    logger.info(f"      → Mapped {service.name} (UUID: {service.uuid}) to slot {booked_slots[i]['slot_uuid']}")

        logger.info(f"📝 Creating final booking with data: {booking_data}")

        # Create the booking with retry logic
        booking_response, booking_error = retry_api_call(
            api_func=create_booking,
            max_retries=2,
            retry_delay=1.0,
            func_name="Booking Creation API",
            booking_data=booking_data
        )

        # Handle API failure after all retries
        if booking_error:
            logger.error(f"❌ Booking creation failed after 2 retries: {booking_error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {
                "success": False,
                "error": str(booking_error),
                "message": "Mi dispiace, c'è un problema tecnico con la prenotazione finale. Ti trasferisco a un operatore."
            }, await create_transfer_node_with_escalation(flow_manager)

        if booking_response and booking_response.get("success", False):
            # Store booking information
            flow_manager.state["final_booking"] = booking_response["booking"]

            logger.success(f"🎉 Booking created successfully: {booking_response['booking'].get('code', 'N/A')}")

            from flows.nodes.booking_completion import create_booking_success_final_node
            return {
                "success": True,
                "booking_code": booking_response["booking"].get("code", ""),
                "booking_uuid": booking_response["booking"].get("uuid", ""),
                "message": "Booking created successfully"
            }, create_booking_success_final_node(booking_response["booking"], selected_services, booked_slots)
        else:
            # Booking failed
            error_msg = booking_response.get("message", "Booking creation failed")
            logger.error(f"❌ Booking creation failed: {error_msg}")

            from flows.nodes.completion import create_error_node
            return {
                "success": False,
                "message": error_msg
            }, create_error_node(f"Booking creation failed: {error_msg}")

    except Exception as e:
        logger.error(f"Booking creation error: {e}")
        from flows.nodes.completion import create_error_node
        return {
            "success": False,
            "message": "Failed to create booking"
        }, create_error_node("Failed to create booking. Please try again.")