# Decisions: 3-Failure Transfer System

## Decisions Made

### 1. Failure Thresholds (THREE TIERS)
| Scenario | Threshold | Rationale |
|----------|-----------|-----------|
| Knowledge gap (agent doesn't know) | 1 | Can't help = transfer |
| User requested transfer + fail | 1 | Already frustrated |
| Normal technical failures | 3 | Give agent chance to recover |

### 2. What Counts as Failure

**IMMEDIATE TRANSFER (threshold = 1):**
- Knowledge base returns nothing/low confidence
- Agent says "non so", "non posso aiutarti", etc.
- User said "transfer me" and then agent fails

**3-FAILURE TRANSFER:**
- API returns empty/error (search, booking)
- Handler returns `{"success": False}`
- Unhandled exceptions

**DO NOT TRACK:**
- User validation errors - user can fix
- User says "no" or wants to change
- Normal flow control

### 3. Keep Transfer as Global (Modified Behavior)
**Decision:** Keep `request_transfer` in GLOBAL_FUNCTIONS
**Behavior Change:** Instead of immediate transfer, ask "what do you need?"
**Rationale:** User can still ask for transfer at any node, but agent gets one chance to help

### 4. Implementation Approach
**Decision:** Subclass FlowManager → `TrackedFlowManager`
**Method:** Override `_call_handler` internal method to intercept ALL handler calls
**Rationale:**
- ONE place handles ALL failures automatically
- Zero changes to existing handlers
- Zero latency impact (just one `if` check per call)

**Risk Mitigation:**
- Pin pipecat-flows version in requirements.txt
- Add startup validation: check `_call_handler` method exists
- Clear error message if pipecat-flows updates break compatibility

### 5. User Transfer Request Flow
```
User: "Transfer me to operator"
Agent: "Per favore, dimmi di cosa hai bisogno. Se non riesco ad aiutarti, ti trasferirò."
User: [describes request]
Agent: [attempts to help]
→ If FAIL: Immediate transfer
→ If SUCCESS: Continue normal flow
```

### 6. Failure Messages (Italian)
- On transfer: "Mi scusi, non riesco ad aiutarti con questa richiesta. Ti trasferisco a un operatore umano."

### 7. Analytics Logging
**Decision:** YES - track all failures
- Total failures per call
- Failure reasons
- Handler that failed
- Transfer rate

### 8. Email Collection - REMOVED
**Decision:** Skip email collection entirely
**New Flow:** `collect_full_name → collect_phone → [confirm_phone] → collect_reminder_authorization`
**(This is a separate task to implement)**

---

## Confirmed Answers

| Question | Answer |
|----------|--------|
| Transfer at any node? | YES (Option A) |
| Failure messages language? | Italian |
| Track validation failures? | NO - user can fix |
| Analytics logging? | YES |

---

## Assumptions

1. **Transfer node exists** and works correctly
2. **Escalation API** called before transfer
3. **State persists** throughout call
4. **Handlers return** tuple `(result_dict, NodeConfig)`

