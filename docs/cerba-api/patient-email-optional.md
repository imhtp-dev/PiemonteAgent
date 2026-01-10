# Patient Email is Optional in Cerba Booking API

## Problem
Booking was failing with "Missing required fields: ['patient_email']" after email collection step was removed from the flow.

## Solution
Per Cerba API documentation, `patient.email` is a **"nullable string"** - meaning it's optional.

Removed email from required fields validation:

```python
# In booking_api.py
required_patient_fields = ["name", "surname", "phone", "date_of_birth", "fiscal_code", "gender"]
# Note: email removed - it's nullable per Cerba API docs

# In patient_detail_handlers.py
validation_results = {
    "selected_services": bool(selected_services),
    "booked_slots": bool(booked_slots),
    "patient_name": bool(patient_name),
    "patient_surname": bool(patient_surname),
    "patient_phone": bool(patient_phone),
    # patient_email removed - optional per Cerba API
    "patient_fiscal_code": bool(patient_fiscal_code)
}
```

## Key Code Reference
- `services/booking_api.py` - line 226-227 (required fields list)
- `flows/handlers/patient_detail_handlers.py` - lines 300-308, 331-332 (validation)

## Gotchas
1. **For NEW patients**: Email is optional, can be empty string
2. **For EXISTING patients**: Only UUID is sent, no patient fields needed
3. **API docs field types**:
   - `string` = required
   - `nullable string` = optional (can be null/empty)

## API Documentation Reference
From Cerba Booking API v2.4:
- `patient.name` - string (required)
- `patient.surname` - string (required)
- `patient.phone` - string (required)
- `patient.email` - **nullable string** (optional)
- `patient.date_of_birth` - date (required)
- `patient.fiscal_code` - string (required)
- `patient.gender` - string (required)

## Date Learned
2026-01-02

## Related
- `docs/cerba-api/booking-api-payload-optimization.md` - Existing vs new patient payloads
