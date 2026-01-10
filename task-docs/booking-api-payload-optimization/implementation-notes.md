# Implementation Notes: Booking API Payload Optimization

## Task Overview
When a patient is found in the database (by phone number), use a simplified booking API payload with just the patient UUID instead of sending all patient details.

## Research Findings

### Current Implementation Analysis

**File**: `flows/handlers/patient_detail_handlers.py` (Lines 592-649)

Current code **always** sends full patient details:
```python
# Lines 593-601 - ALWAYS creates full patient object
patient_data = {
    "name": patient_name,
    "surname": patient_surname,
    "email": patient_email,
    "phone": patient_phone,
    "date_of_birth": patient_dob,
    "fiscal_code": patient_fiscal_code,
    "gender": patient_gender.upper()
}

# Lines 604-608 - UUID added as EXTRA field (not replacement)
if patient_found_in_db and patient_db_id:
    patient_data["uuid"] = patient_db_id
```

### Desired Payload Format (from user's working example)

**When patient IS FOUND in database:**
```python
{
    'patient': {'uuid': '2c85d36d-98ff-47ea-ab75-d31e424b3525'},  # ONLY UUID
    'booking_type': 'private',
    'health_services': [{'uuid': '...', 'slot': '...'}],
    'reminder_authorization': True,
    'marketing_authorization': True,
    'sms_notification': True  # Added by prepare_booking_data()
}
```

**When patient NOT FOUND (new patient):**
```python
{
    'patient': {
        'name': '...',
        'surname': '...',
        'email': '...',
        'phone': '...',
        'date_of_birth': '...',
        'fiscal_code': '...',
        'gender': 'M'
    },
    'booking_type': 'private',
    'health_services': [{'uuid': '...', 'slot': '...'}],
    'reminder_authorization': True,
    'marketing_authorization': True,
    'sms_notification': True
}
```

### Log Evidence from Call Log (20251221_224755)

From the log file, the booking was **successful** with an existing patient:
```
2025-12-21 22:54:19.046 | INFO - Including patient UUID for existing patient: 2c85d36d-98ff-47ea-ab75-d31e424b3525
2025-12-21 22:54:21.642 | INFO - Booking API response status: 200
2025-12-21 22:54:21.642 | SUCCESS - Booking created successfully
2025-12-21 22:54:21.643 | INFO - Booking UUID: 8f97d8ca-af5e-47a5-a057-56fbc5b8bea8
2025-12-21 22:54:21.643 | INFO - Booking Code: AMB26
```

The booking **worked** but it was sending extra data (full patient details + UUID). The optimization is to send **only UUID** when patient exists.

### State Variables Involved

From `confirm_details_and_create_booking()`:
- `patient_found_in_db` - Boolean indicating if patient was found (Lines 418, 514)
- `patient_db_id` - UUID of existing patient (Lines 515, 572)

These are already being tracked and passed to `perform_booking_creation_and_transition()`.

### Booking API Validation

**File**: `services/booking_api.py` (Lines 206-286)

The `validate_booking_data()` function **already handles** UUID-only patients:
```python
# Lines 222-233 - Already validates correctly
if patient.get("uuid"):
    logger.info("Patient UUID provided - skipping patient field validation")
else:
    # Validate all required patient fields for new patients
    required_patient_fields = ["name", "surname", "email", "phone", ...]
```

This means **no changes needed** in `booking_api.py`.

### SMS Confirmation Consideration

**File**: `flows/handlers/patient_detail_handlers.py` (Lines 13-81)

The `send_booking_confirmation_sms_async()` function reads patient info from `booking_data`:
```python
# Lines 29-31
patient_info = booking_data.get("patient", {})
patient_name = patient_info.get('name', '').strip()
patient_phone = patient_info.get("phone", "")
```

**Issue**: If we only send UUID in booking_data, SMS won't have patient name/phone.

**Solution**: Get name/phone from `flow_manager.state` instead of `booking_data` when calling SMS function:
- `patient_full_name` - already stored in state
- `patient_phone` - already stored in state

### Files That Need Changes

1. **`flows/handlers/patient_detail_handlers.py`**
   - `perform_booking_creation_and_transition()` function (Lines 536-687)
   - Modify lines 592-608 for conditional patient data construction
   - Modify SMS call (line 661) to pass name/phone from state

2. **No changes needed:**
   - `services/booking_api.py` - Already handles UUID-only patients
   - `flows/handlers/booking_handlers.py` - Patient lookup unchanged
   - `flows/nodes/*` - Node structure unchanged
