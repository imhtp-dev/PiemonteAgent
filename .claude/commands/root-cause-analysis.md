# Root Cause Analysis

Collaborative debugging procedure for voice agent issues.

## Process

### Step 1: Understand the Issue
Get clear description of:
- What's happening (the bug)
- What should happen (expected behavior)
- When it happens (specific flow, specific input)
- How to reproduce it

### Step 2: Form Hypothesis
Based on the issue:
1. Search codebase for relevant code
2. Check `docs/` for similar past issues
3. Form hypothesis on root cause
4. Explain hypothesis to user

### Step 3: Add Debug Logging
Add targeted logging to verify hypothesis:
```python
from loguru import logger
logger.debug(f"[DEBUG-RCA] Variable state: {variable}")
```

Focus logging on:
- State transitions in flow_manager.state
- Handler inputs and outputs
- API call requests and responses
- STT/TTS pipeline events

### Step 4: Test
- Start the server with appropriate test mode
- User reproduces the issue
- Collect logs

### Step 5: Analyze
Check logs to verify/invalidate hypothesis:
- If hypothesis CORRECT → Proceed to fix
- If hypothesis WRONG → Form new hypothesis, repeat from Step 2

### Step 6: Fix
1. Reset debug logging changes: `git checkout -- .`
2. Implement the actual fix
3. User tests the fix
4. If fixed → Commit
5. If not fixed → Iterate

### Step 7: Document
After fix is confirmed, create learning document:
```
docs/{category}/{issue-name}.md
```
With:
- Problem description
- Root cause found
- Solution implemented
- How to prevent in future

## Voice AI Specific Checks

When debugging voice issues, check:
- [ ] VAD settings (voice activity detection sensitivity)
- [ ] Interrupt handling (is bot getting cut off?)
- [ ] STT language settings (Italian vs English)
- [ ] TTS voice configuration
- [ ] WebSocket connection state
- [ ] Flow state transitions
- [ ] Handler return values (must return NodeConfig)

## Arguments
$ARGUMENTS - Description of the issue to debug

## Example Usage
```
/root-cause-analysis The bot cuts off users mid-sentence when they pause for 2+ seconds.
This happens in the service_selection flow when listing multiple options.
```