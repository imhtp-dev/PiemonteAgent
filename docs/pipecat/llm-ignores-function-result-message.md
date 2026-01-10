# LLM Ignores Function Result Message

## Problem
When a function handler returns a message (e.g., "Please tell me what you need"), the LLM doesn't speak that message. Instead, it generates its own response based on the **function description**.

Example:
- User: "Transfer me to human operator"
- Handler returns: `{"message": "Dimmi di cosa hai bisogno..."}`
- LLM says: "I'll transfer you to a human operator right away" (WRONG!)

The LLM reads the function **description** ("Transfer call to human operator") and responds based on that, ignoring our actual result message.

## Solution
Update the function **description** to tell LLM what to say:

```python
# BEFORE - LLM says "I'll transfer you"
FlowsFunctionSchema(
    name="request_transfer",
    description="Transfer call to human operator.",
    ...
)

# AFTER - LLM asks what user needs
FlowsFunctionSchema(
    name="request_transfer",
    description="When user asks for human operator, call this to ask what they need. DO NOT say you will transfer - say 'Dimmi di cosa hai bisogno, se non riesco ad aiutarti ti trasferirò.'",
    ...
)
```

## Key Code Reference
- `flows/global_functions.py:124-136` - request_transfer function schema

## Gotchas
1. Function result `message` field is for logging/debugging, NOT for TTS
2. LLM generates response based on function name + description
3. To control what LLM says, put instructions IN the description
4. For complex responses, use `pre_actions` with `tts_say` in node config

## Date Learned
2025-12-26

## Related
- `docs/pipecat/failure-tracking-with-flowmanager-subclass.md`
