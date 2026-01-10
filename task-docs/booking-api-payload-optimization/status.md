# Status: Booking API Payload Optimization

## Current Phase: Implementation Complete - Ready for Testing

## Summary

**Task**: Optimize booking API payload to send only patient UUID (not full details) when patient is found in database.

## Changes Made

### 1. Modified `perform_booking_creation_and_transition()` - Lines 592-608

**Before:**
```python
# Always sent all patient details + UUID
patient_data = {
    "name": ..., "surname": ..., "email": ..., ...
}
if patient_found_in_db:
    patient_data["uuid"] = patient_db_id
```

**After:**
```python
# Conditional: UUID-only for existing, full details for new
if patient_found_in_db and patient_db_id:
    patient_data = {"uuid": patient_db_id}
else:
    patient_data = {"name": ..., "surname": ..., ...}
```

### 2. Updated SMS Function Call - Lines 660-663

**Before:**
```python
await send_booking_confirmation_sms_async(booking_response, booking_data, ...)
# Problem: booking_data.patient wouldn't have name/phone for existing patients
```

**After:**
```python
sms_patient_info = {"patient": {"name": patient_name, "phone": patient_phone}}
await send_booking_confirmation_sms_async(booking_response, sms_patient_info, ...)
# Fixed: Always has name/phone from already-extracted parameters
```

## Testing Instructions

### Test 1: Existing Patient (Primary Test)
```bash
python test.py --caller-phone +393333319326 --patient-dob 1979-06-19
```

**Expected log output:**
```
✅ Using simplified payload with patient UUID only: 2c85d36d-98ff-47ea-ab75-d31e424b3525
📝 Creating final booking with data: {'patient': {'uuid': '...'}, ...}
```

### Test 2: New Patient
```bash
python test.py --start-node booking
```

**Expected log output:**
```
📝 Creating booking for new patient with full details
📝 Creating final booking with data: {'patient': {'name': '...', 'surname': '...', ...}, ...}
```

## Verification Checklist

- [ ] Existing patient: Log shows "Using simplified payload with patient UUID only"
- [ ] Existing patient: Booking API returns success (status 200)
- [ ] Existing patient: SMS is sent with correct patient name
- [ ] New patient: Log shows "Creating booking for new patient with full details"
- [ ] New patient: Booking API returns success

## Rollback

If issues occur, revert the changes in `flows/handlers/patient_detail_handlers.py`:
- Lines 592-608: Restore original patient_data construction
- Lines 660-663: Restore original SMS call with `booking_data`
