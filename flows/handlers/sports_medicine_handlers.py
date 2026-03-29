"""
Sports Medicine flow handlers.
Non-Agonistic booking via CHC MDS API. Agonistic → escalate to operator.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, Optional
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig, FlowArgs

from services.sports_medicine_api import mds_api, MDSAPIError
from services.geocoding_service import geocode_address, GeocodingError

ITALIAN_TZ = ZoneInfo("Europe/Rome")


# ============================================================================
# 1. VISIT TYPE SELECTION
# ============================================================================

async def handle_visit_type_selection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Set isAgonistic, route accordingly.
    Non-agonistic → protocol selection node.
    Agonistic → escalate to operator (Phase 2).
    """
    visit_type = args.get("visit_type", "").strip().lower()

    if visit_type == "agonistic":
        logger.info("🏅 Sports medicine: AGONISTIC detected → escalating to operator")
        flow_manager.state["sports_medicine_mode"] = True
        flow_manager.state["is_agonistic"] = True
        flow_manager.state["transfer_reason"] = "Prenotazione visita sportiva agonistica"
        flow_manager.state["transfer_requested"] = True
        flow_manager.state["transfer_type"] = "capability_limitation"

        from flows.handlers.global_handlers import _handle_transfer_escalation
        await _handle_transfer_escalation(flow_manager)

        from flows.nodes.transfer import create_transfer_node
        return {
            "success": True,
            "visit_type": "agonistic",
            "message": "La prenotazione per visite agonistiche richiede un operatore. Ti trasferisco subito."
        }, create_transfer_node()

    # Non-agonistic → continue flow
    logger.info("🏃 Sports medicine: NON-AGONISTIC → protocol selection")
    flow_manager.state["sports_medicine_mode"] = True
    flow_manager.state["is_agonistic"] = False

    from flows.nodes.sports_medicine import create_sports_medicine_protocol_node
    return {
        "success": True,
        "visit_type": "non_agonistic"
    }, create_sports_medicine_protocol_node()


# ============================================================================
# 2. PROTOCOL SELECTION (Standard vs B1)
# ============================================================================

async def handle_protocol_selection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Set isB1Protocol, transition to address collection."""
    is_b1 = args.get("is_b1", False)

    flow_manager.state["is_b1_protocol"] = is_b1
    protocol_name = "B1 esteso" if is_b1 else "standard"
    logger.info(f"📋 Sports medicine protocol: {protocol_name}")

    from flows.nodes.sports_medicine import create_sports_medicine_address_node
    return {
        "success": True,
        "protocol": protocol_name
    }, create_sports_medicine_address_node()


# ============================================================================
# 3. ADDRESS COLLECTION + GEOCODING → REGION → GROUP → FACILITIES
# ============================================================================

async def handle_address_collection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Geocode address → resolve region → get ID_Group → fetch facilities → transition."""
    address = args.get("address", "").strip()

    if not address:
        return {"success": False, "message": "Per favore, dimmi in quale città ti trovi."}, None

    try:
        # Step 1: Geocode address → region
        logger.info(f"📍 Geocoding address: {address}")
        geo_result = geocode_address(address)

        if not geo_result or not geo_result.get("region"):
            return {
                "success": False,
                "message": "Non sono riuscita a individuare la tua posizione. Puoi ripetere il nome della città?"
            }, None

        region = geo_result["region"]
        flow_manager.state["patient_address"] = address
        flow_manager.state["resolved_region"] = region
        flow_manager.state["geocode_result"] = geo_result
        logger.info(f"📍 Resolved region: {region} (from '{address}')")

        # Step 2: Match region → ID_Group
        group = mds_api.find_group_by_region(region)
        if not group:
            return {
                "success": False,
                "message": f"Mi dispiace, non abbiamo sedi di medicina sportiva nella regione {region}. Puoi provare con un altro indirizzo?"
            }, None

        id_group = group.get("ID") or group.get("id")
        flow_manager.state["mds_id_group"] = id_group
        logger.info(f"📍 Matched group: {id_group} ({group.get('Name', '')})")

        # Step 3: Fetch facilities
        facilities = mds_api.get_facilities(id_group)
        if isinstance(facilities, dict) and "Error_Code" in facilities:
            return {
                "success": False,
                "message": "Si è verificato un errore nel recuperare le sedi disponibili. Riprovo."
            }, None

        if not facilities:
            return {
                "success": False,
                "message": f"Mi dispiace, non ci sono sedi disponibili nella regione {region}."
            }, None

        # Step 4: Filter by PossAGO if B1 protocol
        is_b1 = flow_manager.state.get("is_b1_protocol", False)
        if is_b1:
            facilities = [f for f in facilities if f.get("PossAGO") is True]
            if not facilities:
                return {
                    "success": False,
                    "message": (
                        "Mi dispiace, nessuna sede nella tua zona supporta il protocollo B1. "
                        "Vuoi procedere con il protocollo standard?"
                    )
                }, None

        flow_manager.state["mds_facilities"] = facilities
        logger.info(f"📍 Found {len(facilities)} facilities for {id_group}")

        from flows.nodes.sports_medicine import create_sports_medicine_facility_node
        return {
            "success": True,
            "region": region,
            "facility_count": len(facilities)
        }, create_sports_medicine_facility_node(facilities, is_b1)

    except (MDSAPIError, GeocodingError) as e:
        logger.error(f"Sports medicine address handler error: {e}")
        return {
            "success": False,
            "message": "Si è verificato un errore tecnico. Puoi riprovare con il nome della città?"
        }, None


# ============================================================================
# 4. FACILITY SELECTION → VALIDATE TYPE → SEARCH SLOTS
# ============================================================================

async def handle_facility_selection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Validate visit type at facility → get Tipo_AB → search slots → transition."""
    facility_index = args.get("facility_index", 0)
    facilities = flow_manager.state.get("mds_facilities", [])

    # Validate index
    if facility_index < 1 or facility_index > len(facilities):
        return {
            "success": False,
            "message": f"Per favore, scegli un numero tra 1 e {len(facilities)}."
        }, None

    facility = facilities[facility_index - 1]
    id_sede = facility.get("ID")
    sede_name = facility.get("Name", "")
    id_group = flow_manager.state.get("mds_id_group")
    is_b1 = flow_manager.state.get("is_b1_protocol", False)

    flow_manager.state["mds_selected_facility"] = facility
    flow_manager.state["mds_id_sede"] = id_sede
    flow_manager.state["mds_sede_name"] = sede_name
    logger.info(f"🏥 Selected facility: {sede_name} (ID={id_sede})")

    try:
        # Validate type → get Tipo_AB
        validation = mds_api.validate_type(
            id_group=id_group, id_sede=id_sede,
            ago=False, b1=is_b1
        )

        # Check for error response
        if isinstance(validation, dict) and "Error_Code" in validation:
            return {
                "success": False,
                "message": f"Questa sede non supporta il tipo di visita richiesto. {validation.get('Error_Message', '')} Vuoi scegliere un'altra sede?"
            }, None

        if not validation.get("Validate"):
            note = validation.get("Note", "")
            return {
                "success": False,
                "message": f"Questa sede non è disponibile per il tipo di visita richiesto. {note} Vuoi scegliere un'altra sede?"
            }, None

        tipo_ab = validation.get("Tipo_AB")
        flow_manager.state["mds_tipo_ab"] = tipo_ab
        logger.info(f"✅ Validated: Tipo_AB={tipo_ab}")

        # Show info note if present
        info_note = validation.get("Note", "")
        if info_note:
            flow_manager.state["mds_validation_note"] = info_note

        # Search slots (starting from tomorrow)
        tomorrow = (datetime.now(ITALIAN_TZ) + timedelta(days=1)).strftime("%Y/%m/%d")
        slots = mds_api.find_slots(
            id_sede=id_sede, tipo_ab=tipo_ab,
            date_rif=tomorrow, days=30, n_slots=10
        )

        if isinstance(slots, dict) and "Error_Code" in slots:
            return {
                "success": False,
                "message": "Errore nella ricerca degli slot disponibili. Riprovo."
            }, None

        if not slots:
            return {
                "success": False,
                "message": "Mi dispiace, non ci sono appuntamenti disponibili nei prossimi 30 giorni per questa sede. Vuoi provare con un'altra sede?"
            }, None

        flow_manager.state["mds_slots"] = slots
        logger.info(f"📅 Found {len(slots)} slots at {sede_name}")

        from flows.nodes.sports_medicine import create_sports_medicine_slots_node
        return {
            "success": True,
            "facility": sede_name,
            "slot_count": len(slots)
        }, create_sports_medicine_slots_node(slots, sede_name)

    except MDSAPIError as e:
        logger.error(f"Facility selection error: {e}")
        return {
            "success": False,
            "message": "Si è verificato un errore tecnico. Vuoi riprovare?"
        }, None


# ============================================================================
# 5. SLOT SELECTION → LOCK → CALCULATE PRICE → SUMMARY
# ============================================================================

async def handle_slot_selection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Lock selected slot, calculate price, transition to summary."""
    slot_index = args.get("slot_index", 0)
    slots = flow_manager.state.get("mds_slots", [])

    if slot_index < 1 or slot_index > len(slots):
        return {
            "success": False,
            "message": f"Per favore, scegli un numero tra 1 e {len(slots)}."
        }, None

    slot = slots[slot_index - 1]
    slot_id = slot.get("Slot_ID")
    slot_date = slot.get("Slot_Date", "")
    id_group = flow_manager.state.get("mds_id_group")
    id_sede = flow_manager.state.get("mds_id_sede")
    sede_name = flow_manager.state.get("mds_sede_name", "")

    logger.info(f"📅 Selected slot: {slot_date} (ID={slot_id})")

    try:
        # Lock slot (10 min hold)
        lock_result = mds_api.lock_slot(id_group=id_group, id_sede=id_sede, slot_id=slot_id)

        if isinstance(lock_result, dict) and "Error_Code" in lock_result:
            error_code = lock_result.get("Error_Code")
            if error_code == 10:
                # Slot taken — search again
                return {
                    "success": False,
                    "message": "Mi dispiace, questo slot è stato appena prenotato da qualcun altro. Scegli un altro orario."
                }, None
            return {
                "success": False,
                "message": f"Errore nel blocco dello slot: {lock_result.get('Error_Message', '')}. Riprova."
            }, None

        flow_manager.state["mds_slot_id"] = slot_id
        flow_manager.state["mds_slot_date"] = slot_date
        logger.info(f"🔒 Slot locked: {slot_date}")

        # Calculate price (endpoint may not be deployed — skip gracefully)
        is_b1 = flow_manager.state.get("is_b1_protocol", False)
        tipo_ab = flow_manager.state.get("mds_tipo_ab")
        price_info = ""

        try:
            price_result = mds_api.calculate_price(
                id_group=id_group, tipo_ab=tipo_ab, b1=is_b1,
                sex="M", dt_nascita="1990/01/01"  # Placeholder — real values collected later
            )
            if isinstance(price_result, dict) and "Error_Code" not in price_result and "Message" not in price_result:
                if price_result.get("has_free"):
                    price_info = "Gratuito"
                else:
                    price = price_result.get("price", 0)
                    if price:
                        price_info = f"€{price:.2f}"
                        if price_result.get("has_price_cerba_card"):
                            cerba_price = price_result.get("price_cerba_card", 0)
                            price_info += f" (con Cerba Card: €{cerba_price:.2f})"
                    alert = price_result.get("alert_message", "")
                    if alert:
                        price_info = alert
                flow_manager.state["mds_price_info"] = price_info
                logger.info(f"💰 Price: {price_info}")
        except (MDSAPIError, Exception) as e:
            logger.warning(f"Price calculation skipped (non-blocking): {e}")

        # Build summary
        protocol = "B1 Esteso Non-Agonistica" if is_b1 else "Standard Non-Agonistica"

        from flows.nodes.sports_medicine import create_sports_medicine_summary_node
        return {
            "success": True,
            "slot_date": slot_date,
            "locked": True
        }, create_sports_medicine_summary_node(sede_name, slot_date, protocol, price_info)

    except MDSAPIError as e:
        logger.error(f"Slot selection error: {e}")
        return {
            "success": False,
            "message": "Si è verificato un errore tecnico. Vuoi riprovare?"
        }, None


# ============================================================================
# 6. SUMMARY CONFIRMATION
# ============================================================================

async def handle_summary_confirmation(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Handle confirm / change_slot / change_facility."""
    action = args.get("action", "confirm")

    if action == "change_slot":
        # Re-search slots at same facility
        id_sede = flow_manager.state.get("mds_id_sede")
        tipo_ab = flow_manager.state.get("mds_tipo_ab")
        sede_name = flow_manager.state.get("mds_sede_name", "")

        tomorrow = (datetime.now(ITALIAN_TZ) + timedelta(days=1)).strftime("%Y/%m/%d")
        try:
            slots = mds_api.find_slots(id_sede=id_sede, tipo_ab=tipo_ab, date_rif=tomorrow)
            if not slots or (isinstance(slots, dict) and "Error_Code" in slots):
                return {"success": False, "message": "Nessun altro slot disponibile."}, None

            flow_manager.state["mds_slots"] = slots
            from flows.nodes.sports_medicine import create_sports_medicine_slots_node
            return {"success": True}, create_sports_medicine_slots_node(slots, sede_name)
        except MDSAPIError as e:
            return {"success": False, "message": f"Errore: {e}"}, None

    elif action == "change_facility":
        # Back to facility selection
        facilities = flow_manager.state.get("mds_facilities", [])
        is_b1 = flow_manager.state.get("is_b1_protocol", False)
        from flows.nodes.sports_medicine import create_sports_medicine_facility_node
        return {"success": True}, create_sports_medicine_facility_node(facilities, is_b1)

    else:  # confirm
        from flows.nodes.sports_medicine import create_sports_medicine_demographics_node
        return {"success": True}, create_sports_medicine_demographics_node()


# ============================================================================
# 7. DEMOGRAPHICS COLLECTION
# ============================================================================

async def handle_demographics_collection(
    args: FlowArgs, flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Validate and store patient demographics, then proceed to final booking."""
    nome = args.get("nome", "").strip()
    cognome = args.get("cognome", "").strip()
    sex = args.get("sex", "").strip().upper()
    dt_nascita = args.get("dt_nascita", "").strip()
    email = args.get("email", "").strip()
    telefono = args.get("telefono", "").strip()
    consenso = args.get("consenso_promemoria", True)
    cod_fiscale = args.get("cod_fiscale", "").strip()
    luogo_nascita = args.get("luogo_nascita", "").strip()

    # Validate required fields
    missing = []
    if not nome:
        missing.append("nome")
    if not cognome:
        missing.append("cognome")
    if sex not in ("M", "F"):
        missing.append("sesso (M/F)")
    if not dt_nascita:
        missing.append("data di nascita")
    if not email:
        missing.append("email")
    if not telefono:
        missing.append("telefono")

    if missing:
        return {
            "success": False,
            "ignorable": True,
            "message": f"Mancano i seguenti dati: {', '.join(missing)}. Per favore, forniscili."
        }, None

    # Store demographics
    flow_manager.state["mds_patient"] = {
        "nome": nome,
        "cognome": cognome,
        "sex": sex,
        "dt_nascita": dt_nascita,
        "telefono": telefono,
        "email": email,
        "consenso_promemoria": consenso,
        "cod_fiscale": cod_fiscale,
        "luogo_nascita": luogo_nascita,
    }
    logger.info(f"👤 Patient: {nome} {cognome}, {sex}, DOB={dt_nascita}")

    # Recalculate price with real patient data (endpoint may not be deployed)
    try:
        id_group = flow_manager.state.get("mds_id_group")
        tipo_ab = flow_manager.state.get("mds_tipo_ab")
        is_b1 = flow_manager.state.get("is_b1_protocol", False)
        price_result = mds_api.calculate_price(
            id_group=id_group, tipo_ab=tipo_ab, b1=is_b1,
            sex=sex, dt_nascita=dt_nascita
        )
        if isinstance(price_result, dict) and "Error_Code" not in price_result and "Message" not in price_result:
            price = price_result.get("price", 0)
            if price_result.get("has_free"):
                flow_manager.state["mds_price_info"] = "Gratuito"
            elif price:
                info = f"€{price:.2f}"
                if price_result.get("has_price_cerba_card"):
                    info += f" (con Cerba Card: €{price_result.get('price_cerba_card', 0):.2f})"
                flow_manager.state["mds_price_info"] = info
            alert = price_result.get("alert_message", "")
            if alert:
                flow_manager.state["mds_price_info"] = alert
    except (MDSAPIError, Exception) as e:
        logger.warning(f"Price recalculation skipped (non-blocking): {e}")

    # Proceed to final booking
    return await _execute_booking(flow_manager)


# ============================================================================
# 8. FINAL BOOKING (Slot/Insert)
# ============================================================================

async def _execute_booking(
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], Optional[NodeConfig]]:
    """Execute the actual POST Slot/Insert."""
    patient = flow_manager.state.get("mds_patient", {})
    id_group = flow_manager.state.get("mds_id_group")
    id_sede = flow_manager.state.get("mds_id_sede")
    tipo_ab = flow_manager.state.get("mds_tipo_ab")
    slot_id = flow_manager.state.get("mds_slot_id")
    slot_date = flow_manager.state.get("mds_slot_date")
    is_b1 = flow_manager.state.get("is_b1_protocol", False)
    sede_name = flow_manager.state.get("mds_sede_name", "")

    try:
        result = mds_api.insert_booking(
            id_group=id_group,
            id_sede=id_sede,
            tipo_ab=tipo_ab,
            slot_id=slot_id,
            slot_date=slot_date,
            b1=is_b1,
            nome=patient.get("nome", ""),
            cognome=patient.get("cognome", ""),
            sex=patient.get("sex", ""),
            dt_nascita=patient.get("dt_nascita", ""),
            telefono=patient.get("telefono", ""),
            email=patient.get("email", ""),
            consenso_promemoria=patient.get("consenso_promemoria", True),
            cod_fiscale=patient.get("cod_fiscale", ""),
            luogo_nascita=patient.get("luogo_nascita", ""),
            indirizzo=flow_manager.state.get("patient_address", ""),
            citta=flow_manager.state.get("geocode_result", {}).get("corrected", ""),
        )

        # Check for error response
        if isinstance(result, dict) and "Error_Code" in result:
            error_code = result.get("Error_Code")

            if error_code == 8:
                # Age/gender restriction — read message verbatim
                return {
                    "success": False,
                    "message": result.get("Error_Message", "Restrizione di età/sesso per questa visita.")
                }, None

            if error_code == 9:
                # Slot taken — back to slot search
                return {
                    "success": False,
                    "message": "Mi dispiace, questo slot non è più disponibile. Cerchiamo un altro orario."
                }, None

            return {
                "success": False,
                "message": f"Errore nella prenotazione: {result.get('Error_Message', 'errore sconosciuto')}"
            }, None

        # Success — transition back to router with confirmation message
        patient_name = f"{patient.get('nome', '')} {patient.get('cognome', '')}"
        logger.success(f"✅ Sports medicine booking confirmed: {patient_name} at {sede_name} on {slot_date}")

        flow_manager.state["booking_in_progress"] = False
        flow_manager.state["sports_medicine_mode"] = False

        from flows.nodes.router import create_router_node
        return {
            "success": True,
            "message": (
                f"Prenotazione confermata! {patient_name}, appuntamento presso {sede_name} il {slot_date}. "
                "Riceverà un promemoria via email. Chiedi al paziente se c'è altro che puoi fare."
            )
        }, create_router_node(reset_context=True)

    except MDSAPIError as e:
        logger.error(f"Booking insert error: {e}")
        return {
            "success": False,
            "message": "Si è verificato un errore nella prenotazione. Vuoi riprovare?"
        }, None
