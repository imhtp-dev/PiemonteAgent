# Booking API Payload Optimization

## Problem
When creating a booking for an **existing patient** (found via phone lookup), the booking API was receiving redundant data - both the patient UUID AND all patient details (name, email, phone, etc.). This was inefficient since the backend already has all patient information.

## Solution
Use conditional payload construction:

**Existing patient** → Send only UUID:
```python
{'patient': {'uuid': '2c85d36d-98ff-47ea-ab75-d31e424b3525'}, ...}
```

**New patient** → Send full details:
```python
{'patient': {'name': '...', 'surname': '...', 'email': '...', ...}, ...}
```

### Implementation
```python
# In perform_booking_creation_and_transition()
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
        "fiscal_code": patient_fiscal_code,
        "gender": patient_gender.upper()
    }
    logger.info("📝 Creating booking for new patient with full details")
```

## Key Code Reference
- `flows/handlers/patient_detail_handlers.py` - lines 592-608 (patient_data construction)
- `services/booking_api.py` - lines 222-233 (validation handles UUID-only)

## Related: SMS Removal
**Linkup (the booking API) sends SMS automatically** when a booking is created. There's no need to send SMS from our side.

- Removed: `send_booking_confirmation_sms_async()` function
- Removed: `from services.sms_service import send_booking_confirmation_sms`
- File `services/sms_service.py` exists but is unused (can be deleted)

## Gotchas
1. The `validate_booking_data()` in `booking_api.py` already handles UUID-only patients - it skips patient field validation when UUID is present
2. State variables `patient_found_in_db` and `patient_db_id` are set during patient lookup flow
3. Working payload format confirmed by user:
```python

{
    'patient': {'uuid': '...'},
    'booking_type': 'private',
    'health_services': [{'uuid': '...', 'slot': '...'}],
    'reminder_authorization': True,
    'marketing_authorization': True,
    'sms_notification': True  # Added by prepare_booking_data()
}
```

## Date Learned
2025-12-23

## Related
- Patient lookup: `services/patient_lookup.py` - `lookup_by_phone_and_dob()`
- Booking flow: `flows/handlers/booking_handlers.py` - `confirm_booking_summary_and_proceed()`
