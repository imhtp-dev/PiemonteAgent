# LLM Not Calling Function - Hallucinating Completion

## Problem
At `booking_summary_confirmation` node, user said "yes" to confirm booking. Instead of calling `confirm_booking_summary` function, the LLM:
1. Collected personal details conversationally (name, phone)
2. Said "booking confirmed" without ever calling the function
3. Flow never transitioned to next node

The LLM "hallucinated" completing the booking without using the required function.

## Root Cause
Node system prompt said: "Ask for confirmation before proceeding to personal information collection"

LLM interpreted user's "yes" as confirmation and thought it should now collect personal details. But there was no function defined for collecting personal details at that node - so LLM just did it conversationally.

## Solution
Add explicit instructions in node prompt forcing function calls:

```python
role_messages=[{
    "role": "system",
    "content": f"""...existing instructions...

⚠️ MANDATORY FUNCTION CALL RULES:
- When user says YES/OK/proceed → IMMEDIATELY call confirm_booking_summary with action="proceed"
- When user wants to cancel → call confirm_booking_summary with action="cancel"
- When user wants different time → call confirm_booking_summary with action="change"
- DO NOT collect personal details yourself! The function will handle the next step.
- DO NOT say "booking confirmed" - the booking is NOT complete yet!
- You MUST call the function to proceed. Never skip the function call.
..."""
}]
```

## Key Code Reference
- `flows/nodes/booking.py:1145-1200` - create_booking_summary_confirmation_node

## Gotchas
1. If node has only one function, LLM might skip it and respond conversationally
2. Always add "MUST call function" instructions for critical transitions
3. Explicitly tell LLM what NOT to do ("DO NOT collect details yourself")
4. Check logs for "Function called:" entries to verify function was invoked

## How to Debug
```bash
grep "_run_function_call" call_logs/your_log.log | tail -20
# If expected function not in list, LLM didn't call it
```

## Date Learned
2025-12-26

## Related
- `docs/pipecat/llm-ignores-function-result-message.md`
