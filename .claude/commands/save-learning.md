# Save Learning

Capture important learnings from a completed task or debugging session.

## Purpose
Build a project-specific knowledge base that Claude checks BEFORE researching from scratch.
This prevents solving the same problem twice.

## Process

### Step 1: Identify the Learning
Determine:
- What category? (pipecat, talkdesk, voice-ai-patterns, cerba-api)
- What's the core lesson?
- What problem does this solve?

### Step 2: Create Learning Document
Create file at `docs/{category}/{descriptive-name}.md`

### Step 3: Document Structure
```markdown
# {Title}

## Problem
What issue or challenge was encountered?

## Solution
How was it solved? Include code snippets if relevant.

## Key Code Reference
Which files in our project implement this?
- `path/to/file.py` - lines X-Y

## Gotchas
What to watch out for?

## Date Learned
{date}

## Related
Links to related learnings or external docs
```

### Step 4: Update Sub-Agent Awareness
If this learning is critical, consider updating the relevant sub-agent
in `.claude/agents/` to explicitly check for this document.

## Categories

| Category | Use For |
|----------|---------|
| `pipecat` | Framework patterns, pipeline issues, transport configs |
| `talkdesk` | Talkdesk integration, WebSocket, caller ID handling |
| `voice-ai-patterns` | STT/TTS issues, VAD, interrupts, turn-taking |
| `cerba-api` | Healthcare API quirks, booking logic, patient data |

## Arguments
$ARGUMENTS - Brief description of what was learned

## Example Usage
```
/save-learning Discovered that STT language must be switched to multi-language mode 
for email collection, otherwise Italian STT mangles email addresses.
Category: voice-ai-patterns
```