# Implementation Plan: 3-Failure Transfer System

## Overview

Implement automatic transfer to human operator with smart failure detection.

**Three Failure Categories:**

| Category | Threshold | Examples |
|----------|-----------|----------|
| **Knowledge Gap** | 1 (immediate) | KB returns nothing, agent says "I don't know" |
| **User Requested Transfer** | 1 | User says "transfer me" then agent fails once |
| **Technical Failures** | 3 | API errors, no slots, booking fails |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     FAILURE TRACKING SYSTEM                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   flow_manager.state["failure_tracker"] = {                     │
│       "failure_count": 0,           # Global failure counter     │
│       "transfer_requested": False,  # User asked for transfer   │
│       "in_transfer_attempt": False, # Agent trying to help      │
│       "failure_history": []         # Log of failures           │
│   }                                                              │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                         LOGIC FLOWS                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   FLOW A: User says "transfer me"                               │
│   ─────────────────────────────────                             │
│   1. Set transfer_requested = True                               │
│   2. Agent: "Please tell me what you need,                       │
│             if I can't help, I'll transfer you"                  │
│   3. User describes request                                      │
│   4. Agent attempts to help                                      │
│   5. If agent FAILS → Immediate transfer (threshold = 1)        │
│   6. If agent SUCCEEDS → Reset, continue normal flow            │
│                                                                  │
│   FLOW B: Normal conversation failures                           │
│   ─────────────────────────────────────                         │
│   1. Agent fails (API error, validation, etc.)                   │
│   2. Increment failure_count                                     │
│   3. If failure_count >= 3 → Transfer                           │
│   4. Otherwise → Continue, try again                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Implementation

### Step 1: Create Failure Tracker Utility
**File:** `utils/failure_tracker.py` (NEW)

Simple utility class for tracking failures in flow state.

### Step 2: Create TrackedFlowManager Subclass
**File:** `flows/tracked_flow_manager.py` (NEW)

Subclass FlowManager and override `_call_handler` to automatically track ALL handler failures in ONE place.

```python
from pipecat_flows import FlowManager
from utils.failure_tracker import FailureTracker

class TrackedFlowManager(FlowManager):
    """FlowManager with automatic failure tracking.

    Overrides _call_handler to intercept ALL handler calls and track failures.
    No changes needed to individual handlers - tracking is automatic.
    """

    async def _call_handler(self, handler, args):
        """Override to add failure tracking to every handler call."""
        # Initialize tracker if needed
        if "failure_tracker" not in self.state:
            FailureTracker.initialize(self.state)

        # Call original handler
        result = await super()._call_handler(handler, args)

        # Process and track result
        # ... (tracking logic)

        return result
```

**Why this approach:**
- ONE place handles ALL failures automatically
- Zero changes to existing handlers
- Zero latency impact on success path (just one `if` check)
- Clean separation of concerns

**Breakage prevention:**
- Pin pipecat-flows version in requirements.txt
- Add startup validation check for `_call_handler` method
- Clear error message if API changes

### Step 3: Update flows/manager.py
**File:** `flows/manager.py` (MODIFY)

Change `from pipecat_flows import FlowManager` to use our `TrackedFlowManager`.

### Step 4: Modify Transfer Request Handler
**File:** `flows/handlers/global_handlers.py` (MODIFY)

Change `global_request_transfer` to ask "what do you need?" instead of immediate transfer.

### Step 5: Add Version Compatibility Check
**File:** `flows/tracked_flow_manager.py`

```python
def _validate_pipecat_flows_compatibility():
    """Ensure pipecat-flows has expected internal API."""
    from pipecat_flows import FlowManager
    if not hasattr(FlowManager, '_call_handler'):
        raise RuntimeError(
            "pipecat-flows version incompatible: _call_handler method not found. "
            "Please check pipecat-flows version or update TrackedFlowManager."
        )
```

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `utils/failure_tracker.py` | CREATE | Failure tracking utility class |
| `flows/tracked_flow_manager.py` | CREATE | TrackedFlowManager subclass with auto-tracking |
| `flows/manager.py` | MODIFY | Use TrackedFlowManager instead of FlowManager |
| `flows/handlers/global_handlers.py` | MODIFY | Change transfer to ask "what do you need?" |

**NOTE:** NO changes needed to individual handlers - tracking is automatic via subclass!

## Testing Strategy

1. **Test Mode:** `python voice_test.py --start-node greeting`
2. **Test Cases:**
   - A: Say "transfer me" → Should ask what you need
   - B: Ask question agent doesn't know → Immediate transfer (knowledge gap)
   - C: API fails 3 times → Transfer after 3rd failure
   - D: Say "transfer me", then ask something agent fails → Immediate transfer
   - E: Fail once, succeed next → NO transfer, counter resets

## Failure Detection Points

### Immediate Transfer (threshold = 1):

| Category | Detection Method |
|----------|------------------|
| Knowledge base returns nothing | `confidence == 0` or `answer is None` |
| Agent says "I don't know" | Detect phrases: "non so", "non posso aiutarti" |
| User requested transfer + fail | `transfer_requested == True` |

### 3-Failure Transfer:

| Category | Examples |
|----------|----------|
| API returns no results | No services found, no centers, no slots |
| API network error | Timeout, connection refused |
| Handler exception | Unhandled error |
| Booking creation fails | 400/401/409 errors |

### Do NOT Track:

- User validation errors (invalid format - user can fix)
- User says "no" to confirmation
- User wants to change something
- Normal flow transitions

## Success Criteria

1. Knowledge gaps trigger immediate transfer
2. User request + 1 failure = transfer
3. Normal flow: 3 failures = transfer
4. Success resets counter
5. All failures logged for analytics

