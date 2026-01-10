# Decisions: Booking API Payload Optimization

## Decisions Made

### 1. Payload Structure for Existing Patients
**Decision**: Send only `{'patient': {'uuid': '...'}}` when patient exists in database

**Rationale**:
- The user provided a working payload format that confirms this is the expected API behavior
- Reduces data transfer overhead
- Follows API design principle: don't send redundant data the server already has

### 2. SMS Function Handling
**Decision**: Pass patient name/phone explicitly to SMS function instead of extracting from booking_data

**Rationale**:
- When using UUID-only payload, booking_data won't have name/phone
- These values are already available as extracted parameters in the function
- No changes needed to the SMS function signature

### 3. No Changes to booking_api.py
**Decision**: Leave `services/booking_api.py` unchanged

**Rationale**:
- The `validate_booking_data()` function already handles UUID-only patients correctly (lines 222-233)
- The `prepare_booking_data()` and `create_booking()` functions work with any patient data structure
- The validation already skips patient field checks when UUID is present

### 4. Logging Strategy
**Decision**: Add clear logging to distinguish between payload formats

**Rationale**:
- Easy debugging and verification
- Clear audit trail for which payload format was used
- Helps with troubleshooting API issues

## Assumptions

1. **API Behavior**: The Cerba booking API accepts `{'patient': {'uuid': '...'}}` format and uses the stored patient information

2. **Patient Data State**: When `patient_found_in_db` is true, all these state variables are populated:
   - `patient_db_id` (UUID)
   - `patient_full_name`
   - `patient_phone`
   - `patient_email`
   - `generated_fiscal_code`

3. **SMS Requirements**: The SMS function only needs patient name and phone number, which are already available from flow state

## Pending Questions

None - The user has provided a working payload format, confirming the expected API behavior.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| API rejects UUID-only payload | Low | High | User confirmed this format works; easy rollback |
| SMS fails due to missing data | Low | Medium | Explicitly pass name/phone to SMS function |
| State variables missing | Low | Medium | Existing validation catches missing fields |

## Approval Notes

This is a straightforward optimization with clear benefits:
- Reduces payload size for existing patients
- Aligns with user's confirmed working format
- Minimal code changes (single file, ~20 lines modified)
- Easy rollback if issues arise
