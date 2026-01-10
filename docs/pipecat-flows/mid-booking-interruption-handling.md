# Mid-Booking Interruption Handling

## Problem
When user asks for transfer or info question mid-booking, the agent would respond but NOT return to the booking flow. User had to restart the booking.

## Solution
Strengthen the booking reminder in global function results so LLM continues the booking after handling the interruption.

```python
def _add_booking_reminder(result: Dict[str, Any], flow_manager: FlowManager) -> Dict[str, Any]:
    """Add booking continuation reminder if booking is in progress"""
    if flow_manager.state.get("booking_in_progress"):
        result["IMPORTANT_INSTRUCTION"] = "A booking is in progress. After responding to the user, you MUST immediately continue with the booking by repeating the last question you asked. Do NOT abandon the booking unless user explicitly says to cancel."
        result["continue_booking"] = True
    return result
```

Used in all global handlers:
```python
# In global_request_transfer
return _add_booking_reminder({
    "success": True,
    "message": "Per favore, dimmi di cosa hai bisogno..."
}, flow_manager), None

# In global_knowledge_base, global_pricing, etc.
return _add_booking_reminder(response, flow_manager), None
```

## Key Code Reference
- `flows/handlers/global_handlers.py` - lines 20-25 (`_add_booking_reminder` function)
- `flows/handlers/global_handlers.py` - line 428-432 (transfer handler)
- `flows/handlers/global_handlers.py` - line 71 (knowledge base handler)

## Gotchas
1. **Weak hints don't work** - Just adding `"continue_booking_reminder": "text"` is ignored by LLM
2. **Use strong instruction** - Key name `IMPORTANT_INSTRUCTION` + explicit "you MUST" language
3. **Return `None` for node** - Stay at current node, don't transition away
4. **Info functions already worked** - Because they use `_add_booking_reminder`
5. **Transfer was broken** - Because it didn't call `_add_booking_reminder`

## Flow Comparison

**Before:**
1. Patient: "Transfer me to human" (mid-booking)
2. Agent: "What do you need help with?"
3. Patient: "Continue booking"
4. Agent: [stuck, doesn't know where to continue]

**After:**
1. Patient: "Transfer me to human" (mid-booking)
2. Agent: "What do you need help with? ... Now, as I was asking, what is your gender?"
3. [Continues from last question]

## Date Learned
2026-01-02

## Related
- Global functions stay at current node by returning `(result, None)`
- `booking_in_progress` state flag must be set when booking starts
