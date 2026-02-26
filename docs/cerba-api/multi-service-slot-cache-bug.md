# Multi-Service Slot Cache Bug

## Problem
In multi-service bookings (e.g., Ecografia + Visita Ortopedica), after Service 1 books successfully and the flow auto-transitions to Service 2, the `slot_cache` was NOT cleared. This caused:

1. **Stale cache hit**: When selecting a slot for Service 2, if the primary cache lookup (date_key) missed, the CACHE FALLBACK iterated ALL dates in cache and picked a stale slot from Service 1 (wrong PEA UUID, wrong service).
2. **API 500**: `create_slot` failed because the slot was already booked by Service 1.
3. **Full restart**: `booking_error` only had `search_health_services` (full restart from scratch), forcing patient to re-give address, gender, DOB — causing frustration and hangup.

Real call: `20260226_162704_ffc1cf1a` — 12min, booking failed. Patient hung up.

## Solution
Four fixes applied:

### Fix 1: Clear `slot_cache` between services
Added `flow_manager.state.pop("slot_cache", None)` to both service transition paths:
- Separate scenario cleanup (~line 1842)
- Legacy/second service loop cleanup (~line 1895)

### Fix 2: PEA UUID check in CACHE FALLBACK
The fallback loop now verifies `providing_entity_availability_uuid` matches before accepting a cached slot. Skips stale slots from different services with a warning log.

### Fix 3: Abort on hallucination detection
When slot NOT found in `available_slots`, the code now returns `booking_error` node instead of proceeding with `create_slot`. Previously it just logged a warning and continued.

### Fix 4: Retry from booking_error without full restart
Added `retry_slot_selection` function to `booking_error` node. Clears only slot-related state and re-searches slots for the current service. Patient data (address, gender, DOB, center) is preserved.

## Key Code Reference
- `flows/handlers/booking_handlers.py` — slot_cache cleanup, PEA check, hallucination abort, retry handler
- `flows/nodes/completion.py` — `create_error_node()` with retry function
- `flows/handlers/booking_handlers.py:retry_slot_selection_handler()` — new handler

## Gotchas
- `slot_cache` is a dict keyed by date (`{date_key: {parsed_by_time: {time: slot}}}`). It accumulates across ALL slot searches within a booking session.
- The CACHE FALLBACK (`elif selected_time:` branch) iterates ALL dates — dangerous if cache has slots from multiple services.
- The bug is timing-dependent: only triggers when primary CACHE HIT misses (date_key mismatch) AND fallback finds a stale slot first.
- `available_slots` WAS being cleared between services, but `slot_cache` was NOT — easy to miss.

## Date Learned
2026-02-26

## Related
- `docs/pipecat/tts-filler-during-api-calls.md` — TTS filler pattern used in same handlers
