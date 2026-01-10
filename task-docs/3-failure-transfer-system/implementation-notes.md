# Implementation Notes: 3-Failure Transfer System

## Research Summary

### 1. Pipecat-Flows Framework Capabilities

**State Management:**
- `flow_manager.state` is a single persistent dictionary
- Survives across all node transitions
- Perfect for tracking failure counts globally

**Handler Wrapping:**
- Framework has `_call_handler()` method that invokes all handlers
- Can intercept via decorator pattern or custom FlowManager subclass
- Handler signature detection: 0, 1, or 2+ params

**Failure Detection:**
- Handlers return `{"success": False}` for failures
- No built-in failure counting - must implement ourselves
- Can track same-node visits via state

### 2. Current Failure Points in Codebase

**A. Handler Return Failures (`{"success": False}`)**
| File | Failure Points |
|------|----------------|
| patient_detail_handlers.py | 17 failure points (validation) |
| booking_handlers.py | 48 failure points (API, booking) |
| service_handlers.py | 8 failure points (search) |
| global_handlers.py | 19 failure points (info APIs) |

**B. API Failures:**
- Service Search: No results found
- Center Search: No centers in area
- Slot Search: Returns empty list (silent)
- Booking API: 400/401/409 status codes
- Sorting API: Network/auth errors

**C. Current Transfer Trigger Points:**
- Knowledge base query fails → Transfer
- Pricing API fails → Transfer
- Exam info API fails → Transfer
- Clinic info API fails → Transfer
- User requests transfer → Transfer

### 3. Current Transfer Mechanism

**Location:** `flows/handlers/global_handlers.py:396-433`

```python
async def global_request_transfer(args: FlowArgs, flow_manager: FlowManager):
    reason = args.get("reason", "user request")
    flow_manager.state["transfer_requested"] = True
    flow_manager.state["transfer_reason"] = reason
    await _handle_transfer_escalation(flow_manager)
    return {"success": True}, create_transfer_node()
```

**Transfer is currently GLOBAL** - LLM can call it anytime.

### 4. Failure Types to Track

1. **Handler failures**: `{"success": False}` returns
2. **API failures**: Service errors, network errors
3. **Same-node loops**: LLM stuck at same node 3+ times
4. **Validation failures**: Invalid input from user

### 5. Key Files to Modify

| File | Changes Needed |
|------|----------------|
| `flows/global_functions.py` | Remove `request_transfer` from GLOBAL_FUNCTIONS |
| `flows/handlers/global_handlers.py` | Modify `global_request_transfer` logic |
| `flows/manager.py` | Add failure tracking initialization |
| `utils/failure_tracker.py` | NEW: Failure tracking utility |
| All handlers | Wrap with failure tracking |
