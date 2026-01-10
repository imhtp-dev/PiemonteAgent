# Implementation Plan: Booking API Payload Optimization

## Overview
Optimize the booking API payload to send only patient UUID when the patient already exists in the database, instead of sending all patient details redundantly.

## Step-by-Step Implementation

### Step 1: Modify `perform_booking_creation_and_transition()` in `patient_detail_handlers.py`

**Location**: Lines 592-616

**Current Code:**
```python
# Prepare booking data - include all patient details, plus UUID if patient exists
patient_data = {
    "name": patient_name,
    "surname": patient_surname,
    "email": patient_email,
    "phone": patient_phone,
    "date_of_birth": patient_dob,
    "fiscal_code": patient_fiscal_code,
    "gender": patient_gender.upper()
}

# Add UUID if patient exists in database
if patient_found_in_db and patient_db_id:
    patient_data["uuid"] = patient_db_id
    logger.info(f"✅ Including patient UUID for existing patient: {patient_db_id}")
else:
    logger.info("📝 Creating booking for new patient")
```

**New Code:**
```python
# Prepare booking data - ONLY UUID for existing patients, full details for new patients
if patient_found_in_db and patient_db_id:
    # Existing patient: API only needs UUID (it has all other info)
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

### Step 2: Update SMS Function Call

**Location**: Line 661

**Current Code:**
```python
await send_booking_confirmation_sms_async(booking_response, booking_data, selected_services, booked_slots)
```

**Issue**: When we only send UUID in `booking_data`, the SMS function can't extract patient name/phone.

**Solution**: Pass patient name and phone explicitly from the already-extracted parameters:
```python
# Create a patient info dict for SMS regardless of booking payload format
sms_patient_info = {
    "patient": {
        "name": patient_name,
        "phone": patient_phone
    }
}
await send_booking_confirmation_sms_async(booking_response, sms_patient_info, selected_services, booked_slots)
```

### Step 3: Add Debug Logging for Verification

After the `booking_data` construction (before `create_booking()` call), add:
```python
logger.info(f"📝 Final booking payload format: {'UUID-only' if patient_found_in_db else 'full-details'}")
logger.debug(f"📝 Booking data patient section: {booking_data['patient']}")
```

## Files to Modify

| File | Changes |
|------|---------|
| `flows/handlers/patient_detail_handlers.py` | Lines 592-616: Conditional patient_data construction |
| `flows/handlers/patient_detail_handlers.py` | Line 661: Update SMS function call with explicit patient info |

## No Changes Required

- `services/booking_api.py` - Already validates UUID-only patients correctly
- `services/patient_lookup.py` - Patient lookup unchanged
- Node files in `flows/nodes/` - No impact

## Testing Strategy

### Test 1: Existing Patient (Primary Test)
```bash
python test.py --caller-phone +393333319326 --patient-dob 1979-06-19
```
- Should find the patient via phone lookup
- Booking payload should contain ONLY `{'patient': {'uuid': '...'}, ...}`
- Check log for "Using simplified payload with patient UUID only"

### Test 2: New Patient
```bash
python test.py --start-node booking
```
- Complete full patient data collection
- Booking payload should contain full patient details
- Check log for "Creating booking for new patient with full details"

### Test 3: SMS Verification
- Verify SMS is still sent with correct patient name after both test scenarios
- Check SMS logs for correct `patient_name` and `patient_phone`

## Expected Log Output

**Existing Patient:**
```
✅ Using simplified payload with patient UUID only: 2c85d36d-98ff-47ea-ab75-d31e424b3525
📝 Final booking payload format: UUID-only
📝 Booking data patient section: {'uuid': '2c85d36d-98ff-47ea-ab75-d31e424b3525'}
```

**New Patient:**
```
📝 Creating booking for new patient with full details
📝 Final booking payload format: full-details
📝 Booking data patient section: {'name': '...', 'surname': '...', ...}
```

## Rollback Plan

If issues occur, revert to the original code that always sends full patient details (current behavior works, just not optimized).
