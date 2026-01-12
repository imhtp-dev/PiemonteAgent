"""
Patient Lookup Service
Handles phone+DOB normalization and patient lookup via Cerba API

Uses the Cerba API GET search/patient endpoint to find existing patients
by phone number and verify their date of birth.
"""

import re
from typing import Dict, Any, Optional, List
from loguru import logger


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalize phone number to consistent +country format

    Args:
        raw: Raw phone number from various sources

    Returns:
        Normalized phone number or None if unparseable
    """
    if not raw:
        return None

    # Remove all non-digit characters
    digits_only = re.sub(r'[^\d]', '', raw.strip())

    if not digits_only:
        return None

    # Handle Italian numbers
    if digits_only.startswith('39'):
        # Already has country code
        return f"+{digits_only}"
    elif digits_only.startswith('3'):
        # Missing country code, add Italian +39
        return f"+39{digits_only}"
    elif len(digits_only) >= 10:
        # Assume it's a complete number, add +39 if reasonable length
        return f"+39{digits_only}"

    # If we can't determine format, return None
    logger.warning(f"📞 Could not normalize phone: {raw}")
    return None


def normalize_dob(raw: str) -> Optional[str]:
    """
    Normalize date of birth to YYYY-MM-DD format

    Args:
        raw: Raw date string

    Returns:
        Normalized date in YYYY-MM-DD format or None
    """
    if not raw:
        return None

    # If already in YYYY-MM-DD format
    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw.strip()):
        return raw.strip()

    # Add more date format handling if needed
    # For now, return as-is if it passes basic validation
    return raw.strip()




def get_patient_id_for_logging(patient: Dict[str, Any]) -> str:
    """
    Get safe patient identifier for logging

    Args:
        patient: Patient record

    Returns:
        Safe patient ID for logging
    """
    return patient.get('id', 'unknown')


def lookup_by_phone_and_dob(phone: str, dob: str) -> Optional[Dict[str, Any]]:
    """
    Find patient record by phone number and date of birth using Cerba API

    Args:
        phone: Caller's phone number (any format)
        dob: Date of birth (any reasonable format)

    Returns:
        Matching patient record or None if not found
    """
    normalized_phone = normalize_phone(phone)
    normalized_dob = normalize_dob(dob)

    if not normalized_phone or not normalized_dob:
        logger.warning(f"📞 Lookup failed: invalid phone ({phone or ''}) or DOB")
        return None

    logger.info(f"🔍 Looking up patient via Cerba API: phone={normalized_phone[-4:] if len(normalized_phone) > 4 else '***'}, dob={normalized_dob}")

    try:
        # Import here to avoid circular imports
        from services.cerba_api import cerba_api
        
        # Call Cerba API to search by phone
        patients = cerba_api.search_patient_by_phone(normalized_phone)
        
        if not patients:
            logger.info(f"❌ No patient found in Cerba API for phone={normalized_phone[-4:] if len(normalized_phone) > 4 else '***'}")
            return None
        
        # Filter by DOB to find exact match
        for patient in patients:
            # API returns date_of_birth in YYYY-MM-DD format
            patient_dob = normalize_dob(patient.get('date_of_birth', ''))
            
            if patient_dob == normalized_dob:
                # Transform API response to expected format
                transformed_patient = {
                    'id': patient.get('uuid', ''),
                    'first_name': patient.get('name', ''),
                    'last_name': patient.get('surname', ''),
                    'dob': patient.get('date_of_birth', ''),
                    'fiscal_code': patient.get('fiscal_code', ''),
                    'phone': patient.get('phone', ''),
                    'email': patient.get('email', '')
                }
                
                patient_id = get_patient_id_for_logging(transformed_patient)
                logger.success(f"✅ Patient found via Cerba API: ID={patient_id}, name={transformed_patient.get('first_name', '')} {transformed_patient.get('last_name', '')}")
                return transformed_patient
        
        # Phone found but DOB doesn't match
        logger.info(f"❌ Phone found but DOB mismatch. Found {len(patients)} patient(s) but none match DOB={normalized_dob}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Error during Cerba API patient lookup: {e}")
        # Return None on error - don't crash the flow
        return None


def populate_patient_state(flow_manager, patient: Dict[str, Any]) -> None:
    """
    Populate flow manager state with patient data from lookup

    Args:
        flow_manager: Pipecat flow manager instance
        patient: Patient record from database
    """
    if not patient:
        return

    # Get first and last name separately
    first_name = patient.get('first_name', '')
    last_name = patient.get('last_name', '')
    full_name = f"{first_name} {last_name}".strip()

    # Populate all patient fields in state - store names separately AND combined
    flow_manager.state.update({
        "patient_first_name": first_name,  # Store first name separately
        "patient_surname": last_name,  # Store surname separately
        "patient_full_name": full_name,  # Store combined full name for backward compatibility
        "patient_phone": patient.get('phone', ''),
        "patient_email": patient.get('email', ''),
        "generated_fiscal_code": patient.get('fiscal_code', ''),
        "patient_found_in_db": True,
        "patient_db_id": patient.get('id', '')
    })

    patient_id = get_patient_id_for_logging(patient)
    logger.success(f"✅ Patient state populated for ID={patient_id}: {first_name} {last_name}")


def get_patient_summary_text(patient: Dict[str, Any]) -> str:
    """
    Generate summary text for patient confirmation

    Args:
        patient: Patient record

    Returns:
        Formatted summary text for phone verification only
    """
    return f"""Perfect! We found your details in our Cerba Healthcare database and can proceed without collecting your personal information again.

We have your information in our database and we will message the booking confirmation text to the phone number from where you are calling right now. Should we proceed or you need to change the phone number?"""


# Global patient lookup service instance
patient_lookup_service = {
    'normalize_phone': normalize_phone,
    'normalize_dob': normalize_dob,
    'lookup_by_phone_and_dob': lookup_by_phone_and_dob,
    'populate_patient_state': populate_patient_state,
    'get_patient_summary_text': get_patient_summary_text,
    'get_patient_id_for_logging': get_patient_id_for_logging
}