# LLM Function Calling Gotchas in Pipecat

## Problem
LLM says it will perform an action but doesn't actually call the function:
- "Sto verificando..." (I'm checking...) without calling API
- "Perfetto, procedo con la prenotazione" without calling booking function
- "Confermo i tuoi dati" without calling confirmation function

## Root Causes

### 1. Prompt Not Explicit Enough
LLM interprets "verify the data" as "say you verified it" not "call verify function"

### 2. LLM Can't Access Python State
Prompts like "check flow_manager.state for X" are impossible - LLM only sees messages

### 3. Missing Function Registration
FlowManager created without `global_functions` parameter

## Solution

### Explicit Function Call Instructions

Bad prompt:
```
When user confirms, proceed with booking.
```

Good prompt:
```
CRITICAL: When user confirms (says yes, correct, corretto, sì, va bene, ok):
→ IMMEDIATELY call verify_basic_info with action="confirm"
→ Do NOT just say "Perfetto, procedo" without calling the function
```

### Common Confirmation Words (Italian)
Include these in prompts to catch all variations:
- sì, si, yes
- corretto, correct, esatto
- va bene, ok, giusto
- perfetto, confermo

### Debug Checklist

1. Check logs for function call:
   ```
   Calling function [function_name:call_id] with arguments {...}
   ```

2. Verify global_functions registered:
   ```
   Registered function: function_name
   ```

3. If functions not registered, check FlowManager creation includes `global_functions`

## Key Code Reference
- `flows/nodes/patient_info.py:117-127` - Explicit confirmation instructions
- `flows/nodes/router.py:54-78` - Explicit function call rules

## Date Learned
2024-12-24

## Related
- `docs/pipecat/global-functions-implementation.md`
