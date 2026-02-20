"""
Global Handlers - Available at Every Node
8 global functions for info, transfer, and booking.

Global functions:
- Return (result, None) to stay at current node (info queries)
- Return (result, NodeConfig) to transition (transfer, start_booking)
"""

from typing import Tuple, Dict, Any, Optional
import asyncio
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs

# Import services from new locations
from services.call_data_extractor import get_call_extractor


def _add_booking_reminder(result: Dict[str, Any], flow_manager: FlowManager) -> Dict[str, Any]:
    """Add booking continuation reminder if booking is in progress"""
    if flow_manager.state.get("booking_in_progress"):
        result["IMPORTANT_INSTRUCTION"] = "A booking is in progress. After responding to the user, you MUST immediately continue with the booking by repeating the last question you asked. Do NOT abandon the booking unless user explicitly says to cancel."
        result["continue_booking"] = True
    return result


# ============================================================================
# 1. KNOWLEDGE BASE (Global)
# ============================================================================

async def global_knowledge_base(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Query knowledge base for FAQs, documents, preparations.
    Returns None to stay at current node (allows mid-booking info).
    """
    try:
        query = args.get("query", "").strip()

        if not query:
            logger.warning("⚠️ Empty knowledge base query")
            return {"success": False, "error": "No query provided"}, None

        logger.info(f"📚 [GLOBAL] Knowledge Base Query: {query[:100]}...")

        from services.knowledge_base import knowledge_base_service
        result = await knowledge_base_service.query(query)

        if result.success:
            logger.success(f"✅ Knowledge base answer (confidence: {result.confidence})")

            # Track for analytics
            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="knowledge_base_new",
                    parameters={"query": query},
                    result={"confidence": result.confidence, "source": result.source}
                )

            response = {
                "success": True,
                "query": query,
                "answer": result.answer,
                "confidence": result.confidence,
                "source": result.source
            }
            return _add_booking_reminder(response, flow_manager), None  # Stay at current node
        else:
            logger.error(f"❌ Knowledge base failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {
                "success": False,
                "error": result.error,
                "message": "Information not found"
            }, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Knowledge base error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 2. COMPETITIVE (AGONISTIC) PRICING (Global)
# ============================================================================

async def global_competitive_pricing(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Get agonistic visit pricing.
    Requires: age, gender, sport, region
    """
    try:
        logger.info(f"🔥 COMPETITIVE PRICING CALLED with args: {args}")

        age = args.get("age")
        gender = args.get("gender", "").strip().upper() if args.get("gender") else ""
        sport = args.get("sport", "").strip() if args.get("sport") else ""
        region = args.get("region", "").strip() if args.get("region") else ""

        logger.info(f"   Parsed: age={age}, gender={gender}, sport={sport}, region={region}")

        missing = []
        if not age: missing.append("age")
        if not gender: missing.append("gender")
        if not sport: missing.append("sport")
        if not region: missing.append("region")

        if missing:
            logger.warning(f"⚠️ Missing params for competitive pricing: {missing}")
            return {
                "success": False,
                "missing_params": missing,
                "message": f"Mancano i seguenti parametri: {', '.join(missing)}"
            }, None  # LLM will ask for missing params

        logger.info(f"💰 [GLOBAL] Competitive Pricing: age={age}, gender={gender}, sport={sport}")

        from services.pricing_service import pricing_service
        result = await pricing_service.get_competitive_price(age, gender, sport, region)

        if result.success:
            logger.success(f"✅ Competitive price: €{result.price}")

            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="get_competitive_pricing",
                    parameters={"age": age, "gender": gender, "sport": sport, "region": region},
                    result={"price": result.price, "visit_type": result.visit_type}
                )

            response = {
                "success": True,
                "price": result.price,
                "visit_type": result.visit_type,
                "age": age,
                "gender": gender,
                "sport": sport,
                "region": region
            }
            return _add_booking_reminder(response, flow_manager), None
        else:
            logger.error(f"❌ Competitive pricing failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {"success": False, "error": result.error}, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Competitive pricing error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 3. NON-COMPETITIVE PRICING (Global)
# ============================================================================

async def global_non_competitive_pricing(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Get non-agonistic visit pricing.
    Requires: ecg_under_stress (boolean)
    """
    try:
        ecg_under_stress = args.get("ecg_under_stress")

        if ecg_under_stress is None:
            logger.warning("⚠️ Missing ECG preference")
            return {
                "success": False,
                "missing_params": ["ecg_under_stress"],
                "message": "Need to know if ECG under stress is required"
            }, None

        logger.info(f"💰 [GLOBAL] Non-Competitive Pricing: ECG under stress = {ecg_under_stress}")

        from services.pricing_service import pricing_service
        result = await pricing_service.get_non_competitive_price(ecg_under_stress)

        if result.success:
            logger.success(f"✅ Non-competitive price: €{result.price}")

            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="get_non_competitive_pricing",
                    parameters={"ecg_under_stress": ecg_under_stress},
                    result={"price": result.price}
                )

            response = {
                "success": True,
                "price": result.price,
                "ecg_under_stress": ecg_under_stress
            }
            return _add_booking_reminder(response, flow_manager), None
        else:
            logger.error(f"❌ Non-competitive pricing failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {"success": False, "error": result.error}, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Non-competitive pricing error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 4. EXAM BY VISIT TYPE (Global)
# ============================================================================

async def global_exam_by_visit(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Get exam list for visit type code (A1, A2, A3, B1-B5).
    """
    try:
        visit_type = args.get("visit_type", "").strip().upper()

        if not visit_type:
            logger.warning("⚠️ Missing visit_type")
            return {
                "success": False,
                "missing_params": ["visit_type"],
                "message": "Need visit type code (A1, A2, A3, B1-B5)"
            }, None

        logger.info(f"📋 [GLOBAL] Exam List by Visit: {visit_type}")

        from services.exam_service import exam_service
        result = await exam_service.get_exams_by_visit_type(visit_type)

        if result.success:
            logger.success(f"✅ Exams for {visit_type}: {len(result.exams)} items")

            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="get_exam_by_visit",
                    parameters={"visit_type": visit_type},
                    result={"exam_count": len(result.exams)}
                )

            response = {
                "success": True,
                "visit_type": visit_type,
                "visit_code": result.visit_code,
                "exams": result.exams,
                "exam_count": len(result.exams)
            }
            return _add_booking_reminder(response, flow_manager), None
        else:
            logger.error(f"❌ Exam by visit failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {"success": False, "error": result.error}, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Exam by visit error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 5. EXAM BY SPORT (Global)
# ============================================================================

async def global_exam_by_sport(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Get exam list for specific sport.
    """
    try:
        sport = args.get("sport", "").strip()

        if not sport:
            logger.warning("⚠️ Missing sport")
            return {
                "success": False,
                "missing_params": ["sport"],
                "message": "Need sport name"
            }, None

        logger.info(f"📋 [GLOBAL] Exam List by Sport: {sport}")

        from services.exam_service import exam_service
        result = await exam_service.get_exams_by_sport(sport)

        if result.success:
            logger.success(f"✅ Exams for {sport}: {len(result.exams)} items")

            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="get_exam_by_sport",
                    parameters={"sport": sport},
                    result={"exam_count": len(result.exams)}
                )

            response = {
                "success": True,
                "sport": sport,
                "visit_code": result.visit_code,
                "exams": result.exams,
                "exam_count": len(result.exams)
            }
            return _add_booking_reminder(response, flow_manager), None
        else:
            logger.error(f"❌ Exam by sport failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {"success": False, "error": result.error}, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Exam by sport error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 6. CLINIC INFO (Global)
# ============================================================================

async def global_clinic_info(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Get clinic information (hours, closures, blood collection times).
    Natural language query including location.
    """
    try:
        query = args.get("query", "").strip()

        if not query:
            logger.warning("⚠️ Missing clinic query")
            return {
                "success": False,
                "missing_params": ["query"],
                "message": "Need query for clinic info"
            }, None

        logger.info(f"🏥 [GLOBAL] Clinic Info: {query}")

        from services.clinic_info_service import clinic_info_service
        result = await clinic_info_service.get_clinic_info(query)

        if result.success:
            logger.success("✅ Clinic info retrieved")

            session_id = flow_manager.state.get("session_id")
            if session_id:
                call_extractor = get_call_extractor(session_id)
                call_extractor.add_function_call(
                    function_name="call_graph",
                    parameters={"query": query},
                    result={"success": True}
                )

            response = {
                "success": True,
                "query": query,
                "answer": result.answer
            }
            return _add_booking_reminder(response, flow_manager), None
        else:
            logger.error(f"❌ Clinic info failed: {result.error}")
            from flows.nodes.transfer import create_transfer_node_with_escalation
            return {"success": False, "error": result.error}, await create_transfer_node_with_escalation(flow_manager)

    except Exception as e:
        logger.error(f"❌ Clinic info error: {e}")
        from flows.nodes.transfer import create_transfer_node_with_escalation
        return {"success": False, "error": str(e)}, await create_transfer_node_with_escalation(flow_manager)


# ============================================================================
# 7. REQUEST TRANSFER (Global) - CAPABILITY LIMITS vs GENERIC REQUESTS
# ============================================================================

# Capability limitations - agent physically cannot help with these services
# Transfer IMMEDIATELY when these are detected in the reason
CAPABILITY_LIMIT_PHRASES = [
    "medicina sportiva",
    "visita sportiva",
    "certificato sportivo",
    "idoneità sportiva",
    "agonistica",
    "non agonistica",
    "laboratorio",
    "esami del sangue",
    "prelievo",
    "analisi del sangue",
]


def _is_capability_limitation(reason: str) -> bool:
    """Check if transfer reason indicates a capability limitation (agent cannot help)."""
    reason_lower = reason.lower()
    return any(phrase in reason_lower for phrase in CAPABILITY_LIMIT_PHRASES)


async def global_request_transfer(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """
    Handle user request to transfer to human operator.

    Two modes:
    1. CAPABILITY LIMITATION (sports medicine, laboratorio, etc.)
       → Agent CANNOT help → Transfer IMMEDIATELY

    2. GENERIC REQUEST (user just says "transfer me")
       → Ask what they need → Only transfer if agent then FAILS

    This reduces unnecessary transfers while ensuring capability limits are handled.
    """
    try:
        from utils.failure_tracker import FailureTracker

        reason = args.get("reason", "user request").strip()

        logger.info(f"📞 [GLOBAL] Transfer requested: {reason}")

        # Store transfer info
        flow_manager.state["transfer_reason"] = reason
        flow_manager.state["transfer_timestamp"] = str(asyncio.get_event_loop().time())

        # Check if this is a CAPABILITY LIMITATION (agent cannot help)
        if _is_capability_limitation(reason):
            logger.info(f"🚫 Capability limitation detected: {reason[:50]}... → Transferring immediately")

            # Mark transfer in state for analytics
            flow_manager.state["transfer_requested"] = True
            flow_manager.state["transfer_type"] = "capability_limitation"

            # Execute escalation API call
            await _handle_transfer_escalation(flow_manager)

            # Transition to transfer node
            from flows.nodes.transfer import create_transfer_node
            return {
                "success": True,
                "reason": reason,
                "transfer_type": "capability_limitation"
            }, create_transfer_node()

        # GENERIC REQUEST - Ask what they need first
        logger.info("📞 Generic transfer request - asking what user needs before transferring")

        # Mark transfer requested in failure tracker
        # This sets threshold to 1, so next failure = transfer
        FailureTracker.mark_transfer_requested(flow_manager.state)

        # Return with pending_transfer flag to prevent tracker reset
        return _add_booking_reminder({
            "success": True,
            "pending_transfer": True,  # Prevents TrackedFlowManager from resetting failure tracker
            "reason": reason,
            "message": "Dimmi di cosa hai bisogno. Se non riesco ad aiutarti, ti trasferirò a un operatore umano."
        }, flow_manager), None  # None = stay at current node, let user explain their request

    except Exception as e:
        logger.error(f"❌ Transfer request error: {e}")
        # On error, do transfer immediately
        from flows.nodes.transfer import create_transfer_node
        await _handle_transfer_escalation(flow_manager)
        return {
            "success": True,
            "reason": "error in transfer request",
            "error": str(e)
        }, create_transfer_node()


# ============================================================================
# 8. CHECK SERVICE PRICE (Global) - TRANSITIONS
# ============================================================================

async def global_check_service_price(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Start price inquiry flow when patient asks about cost/price of a service.
    Reuses booking flow but skips unnecessary steps, presents just the price.
    """
    try:
        service_request = args.get("service_request", "").strip()

        logger.info(f"💰 [GLOBAL] Check Service Price: {service_request}")

        # CHECK: Sports medicine services cannot be priced via this agent
        if _is_capability_limitation(service_request):
            logger.warning(f"🚫 Sports medicine price check: '{service_request}' → Redirecting to transfer")

            flow_manager.state["transfer_reason"] = f"Prezzo medicina sportiva: {service_request}"
            flow_manager.state["transfer_requested"] = True
            flow_manager.state["transfer_type"] = "capability_limitation"

            await _handle_transfer_escalation(flow_manager)

            from flows.nodes.transfer import create_transfer_node
            return {
                "success": True,
                "service_request": service_request,
                "redirected_to_transfer": True,
                "message": "Il prezzo per visite di medicina sportiva non è disponibile tramite questo servizio. Ti trasferisco a un operatore."
            }, create_transfer_node()

        # Set price inquiry intent
        flow_manager.state["intent"] = "price_inquiry"
        flow_manager.state["booking_in_progress"] = True
        flow_manager.state["initial_booking_request"] = service_request
        flow_manager.state["current_agent"] = "booking"

        # Track for analytics
        session_id = flow_manager.state.get("session_id")
        if session_id:
            call_extractor = get_call_extractor(session_id)
            call_extractor.add_function_call(
                function_name="check_service_price",
                parameters={"service_request": service_request},
                result={"action": "transition_to_price_inquiry"}
            )

        logger.success("✅ Transitioning to price inquiry flow")

        from flows.nodes.greeting import create_greeting_node
        return {
            "success": True,
            "service_request": service_request,
            "message": "Starting price inquiry"
        }, create_greeting_node(initial_booking_request=service_request if service_request else None)

    except Exception as e:
        logger.error(f"❌ Check service price error: {e}")
        return {
            "success": False,
            "error": str(e)
        }, None  # Stay at current node on error


# ============================================================================
# 9. START BOOKING (Global) - TRANSITIONS
# ============================================================================

async def global_start_booking(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Start booking flow when patient wants to book.
    This DOES transition to booking greeting node.

    EXCEPTION: Sports medicine services cannot be booked via this agent.
    If detected, redirect to transfer instead.
    """
    try:
        service_request = args.get("service_request", "").strip()

        logger.info(f"📅 [GLOBAL] Start Booking: {service_request}")

        # CHECK: Is this a sports medicine service? (capability limitation)
        if _is_capability_limitation(service_request):
            logger.warning(f"🚫 Sports medicine booking detected: '{service_request}' → Redirecting to transfer")

            # Store transfer info
            flow_manager.state["transfer_reason"] = f"Prenotazione medicina sportiva: {service_request}"
            flow_manager.state["transfer_requested"] = True
            flow_manager.state["transfer_type"] = "capability_limitation"

            # Execute escalation API call
            await _handle_transfer_escalation(flow_manager)

            # Transition to transfer node
            from flows.nodes.transfer import create_transfer_node
            return {
                "success": True,
                "service_request": service_request,
                "redirected_to_transfer": True,
                "message": "La prenotazione per visite di medicina sportiva non è disponibile tramite questo servizio. Ti trasferisco a un operatore."
            }, create_transfer_node()

        # Normal booking flow
        # Store booking intent
        flow_manager.state["booking_in_progress"] = True
        flow_manager.state["initial_booking_request"] = service_request
        flow_manager.state["current_agent"] = "booking"

        # Store additional service request if patient mentioned two services
        additional = args.get("additional_service_request", "").strip()
        if additional:
            flow_manager.state["pending_additional_request"] = additional
            logger.info(f"📋 Stored additional service request: {additional}")

        # Track for analytics
        session_id = flow_manager.state.get("session_id")
        if session_id:
            call_extractor = get_call_extractor(session_id)
            call_extractor.add_function_call(
                function_name="start_booking",
                parameters={"service_request": service_request},
                result={"action": "transition_to_booking"}
            )

        logger.success("✅ Transitioning to booking flow")

        from flows.nodes.greeting import create_greeting_node
        return {
            "success": True,
            "service_request": service_request,
            "message": "Starting booking process"
        }, create_greeting_node(
            initial_booking_request=service_request if service_request else None,
            additional_service_request=additional if additional else None
        )

    except Exception as e:
        logger.error(f"❌ Start booking error: {e}")
        return {
            "success": False,
            "error": str(e)
        }, None  # Stay at current node on error


async def global_cancel_and_restart(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Cancel current booking (delete any reserved slots) and return to router.
    Used when patient wants to abandon current booking and start fresh.
    """
    try:
        logger.info("🔄 [GLOBAL] Cancel and restart booking")

        # Delete any reserved slots
        booked_slots = flow_manager.state.get("booked_slots", [])
        cancelled_count = 0
        if booked_slots:
            from services.slotAgenda import delete_slot
            for slot in booked_slots:
                slot_uuid = slot.get("slot_uuid")
                if slot_uuid:
                    try:
                        delete_response = delete_slot(slot_uuid)
                        if delete_response.status_code == 200:
                            cancelled_count += 1
                            logger.info(f"🗑️ Cancelled slot: {slot_uuid}")
                        else:
                            logger.warning(f"⚠️ Failed to cancel slot {slot_uuid}: HTTP {delete_response.status_code}")
                    except Exception as e:
                        logger.error(f"❌ Error cancelling slot {slot_uuid}: {e}")

        # Clear all booking-related state
        booking_keys = [
            "booking_in_progress", "initial_booking_request", "current_agent",
            "selected_services", "selected_center", "booked_slots",
            "pending_additional_request", "pending_additional_resolved",
            "second_service_loop_active", "second_service_search_term",
            "preferred_date", "preferred_time", "first_available_mode",
            "time_preference", "start_time", "end_time",
            "selected_slot", "available_slots",
            "is_cerba_member", "cerba_membership_asked",
            "final_health_centers", "pending_center_search_params",
            "pending_slot_search_params", "pending_slot_booking_params",
            "cached_all_slots", "cached_search_params",
            "sorting_api_response", "sorting_api_success", "sorting_api_error",
            "sorting_api_package_detected", "service_groups", "booking_scenario",
            "current_group_index", "current_service_index",
            "pending_search_term", "pending_search_limit",
            "services_found", "current_search_term",
            "generated_flow", "pending_flow_params",
            "patient_gender", "patient_dob", "patient_address",
            "current_search_radius", "intent",
            "auto_date", "auto_start_time",
            "llm_interpretation_reasoning", "llm_interpretation_summary",
        ]
        for key in booking_keys:
            flow_manager.state.pop(key, None)

        # Track for analytics
        session_id = flow_manager.state.get("session_id")
        if session_id:
            call_extractor = get_call_extractor(session_id)
            call_extractor.add_function_call(
                function_name="cancel_and_restart",
                parameters={},
                result={"action": "cancelled_and_restarted", "cancelled_slots": cancelled_count}
            )

        logger.success(f"✅ Booking cancelled ({cancelled_count} slots deleted), returning to router")

        from flows.nodes.router import create_router_node
        return {
            "success": True,
            "cancelled_slots": cancelled_count,
            "message": "Booking cancelled. Returning to main menu."
        }, create_router_node(reset_context=True)

    except Exception as e:
        logger.error(f"❌ Cancel and restart error: {e}")
        from flows.nodes.router import create_router_node
        return {
            "success": False,
            "error": str(e),
            "message": "Error during cancellation, returning to main menu."
        }, create_router_node(reset_context=True)


# ============================================================================
# 10. CANCEL PREVIOUS APPOINTMENT (Global) - TRANSFER TO DISDETTA QUEUE
# ============================================================================

async def global_cancel_previous_appointment(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """
    Transfer to Disdetta queue (1|1|5) for cancelling/rescheduling previous appointments.
    Unlike cancel_and_restart which resets the current flow, this transfers to a human operator.
    """
    try:
        reason = args.get("reason", "Cancellazione/spostamento appuntamento precedente")
        logger.info(f"📞 [GLOBAL] Cancel previous appointment: {reason}")

        flow_manager.state["transfer_requested"] = True
        flow_manager.state["transfer_type"] = "previous_appointment_cancellation"
        flow_manager.state["transfer_reason"] = reason
        flow_manager.state["transfer_timestamp"] = str(asyncio.get_event_loop().time())

        await _handle_transfer_escalation(flow_manager)

        from flows.nodes.transfer import create_transfer_node
        return {
            "success": True,
            "reason": reason,
            "transfer_type": "previous_appointment_cancellation"
        }, create_transfer_node()

    except Exception as e:
        logger.error(f"❌ Cancel previous appointment error: {e}")
        from flows.nodes.transfer import create_transfer_node
        await _handle_transfer_escalation(flow_manager)
        return {
            "success": True,
            "reason": "error in cancel previous appointment",
            "error": str(e)
        }, create_transfer_node()


# ============================================================================
# HELPER: Sector Determination
# ============================================================================

def _determine_escalation_sector(flow_manager: FlowManager) -> str:
    """Determine escalation sector: 'booking' (1|1|x) or 'info' (2|2|x)."""
    state = flow_manager.state
    # Disdetta/reschedule of previous appointment
    if state.get("transfer_type") == "previous_appointment_cancellation":
        return "booking"
    # Sports medicine transfer (blocked from booking)
    transfer_reason = state.get("transfer_reason", "").lower()
    if any(phrase in transfer_reason for phrase in ["medicina sportiva", "visita sportiva", "certificato sportivo", "idoneità sportiva"]):
        return "booking"
    # Active booking flow (has selected services)
    if state.get("selected_services") or state.get("booking_in_progress"):
        return "booking"
    # Default: info
    return "info"


# ============================================================================
# HELPER: Transfer Escalation
# ============================================================================

async def _handle_transfer_escalation(flow_manager: FlowManager) -> None:
    """
    Handle escalation API call for transfer.
    Runs LLM analysis and calls bridge escalation API.
    """
    try:
        logger.info("🚀 Starting transfer escalation...")

        call_extractor = flow_manager.state.get("call_extractor")
        session_id = flow_manager.state.get("session_id")

        if not call_extractor or not session_id:
            logger.error("❌ Missing call_extractor or session_id")
            return

        # Run analysis
        analysis = await call_extractor.analyze_for_transfer(flow_manager.state)

        logger.success("✅ Transfer analysis complete")
        logger.info(f"   Summary: {analysis['summary'][:100]}...")

        # Determine sector for Talkdesk routing
        sector = _determine_escalation_sector(flow_manager)

        # Force service code for specific transfer types
        service_code = analysis["service"]
        transfer_reason = flow_manager.state.get("transfer_reason", "").lower()
        if any(phrase in transfer_reason for phrase in ["medicina sportiva", "visita sportiva", "certificato sportivo", "idoneità sportiva"]):
            service_code = "4"  # Medicina dello sport
        if flow_manager.state.get("transfer_type") == "previous_appointment_cancellation":
            service_code = "5"  # Disdetta

        logger.info(f"   Sector: {sector}, Service: {service_code}")

        # Get stream_sid for Talkdesk
        stream_sid = flow_manager.state.get("stream_sid", "")

        # Call escalation API
        from services.escalation_service import call_escalation_api

        success = await call_escalation_api(
            summary=analysis["summary"][:250],
            sentiment=analysis["sentiment"],
            action="transfer",
            duration=str(analysis["duration_seconds"]),
            service=service_code,
            call_id=session_id,
            stream_sid=stream_sid,
            sector=sector
        )

        # Store for later
        flow_manager.state["transfer_analysis"] = analysis
        flow_manager.state["transfer_api_success"] = success

        if success:
            logger.success(f"✅ Escalation API success for {session_id}")
        else:
            logger.warning("⚠️ Escalation API failed, ending call anyway")

    except Exception as e:
        logger.error(f"❌ Escalation error: {e}")
        flow_manager.state["transfer_escalation_error"] = str(e)
