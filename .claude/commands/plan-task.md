# Plan Task

Plan any significant feature or change before implementation.

## Process

### Phase 1: Setup
Create task documentation folder at `task-docs/$TASK_NAME/` with:
- `implementation-notes.md` - Full research from sub-agents
- `plan.md` - Implementation approach
- `decisions.md` - Decisions made + pending questions
- `status.md` - Current progress

### Phase 2: Research
Based on the task, use relevant sub-agents:
- **pipecat-expert**: For Pipecat framework questions
- **booking-flow-expert**: For conversation flow changes

Each sub-agent should:
1. Check `docs/` for past learnings first
2. Research source code in `_refs/` 
3. Check current project implementation
4. Provide detailed findings

Capture ALL sub-agent findings in `implementation-notes.md`.

### Phase 3: Planning
Based on research, create:

**plan.md** should contain:
- Overview of the approach
- Step-by-step implementation plan
- Files that will be created/modified
- Testing strategy (which --start-node to use)

**decisions.md** should contain:
- Decisions already made (with rationale)
- Pending decisions that need user input
- Any assumptions being made

**status.md** should contain:
- Current phase: "Planning Complete - Awaiting Review"
- What's done
- What's next

### Phase 4: Wait for Review
**STOP and wait for user review.**
- Do NOT proceed to implementation
- User will review documents and provide feedback
- Update documents based on feedback
- Only implement when user says "approved" or "go ahead"

## Arguments
$ARGUMENTS - Description of the feature/task to plan

## Example Usage
```
/plan-task Add a new flow node for collecting patient allergies before booking confirmation.
For pipecat-expert: Research how to add custom data collection in pipecat-flows.
For booking-flow-expert: Find where in the current flow this should be inserted.
```