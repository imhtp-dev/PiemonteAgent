# Codebase Restructure - Plan

## Overview

Clean up dead code, organize files into logical folders, and apply Python project best practices for maintainability.

## Phase 1: Delete Dead Code

### Step 1.1: Verify info_agent/ is truly dead
```bash
grep -r "from info_agent" --include="*.py" | grep -v "_refs" | grep -v "__pycache__"
```
Expected: Only `bot.py` importing from `info_agent.api`

### Step 1.2: Delete dead info_agent subfolders
```
DELETE: info_agent/flows/           # Replaced by global_functions
DELETE: info_agent/services/        # Moved to services/
DELETE: info_agent/config/          # Moved to config/
DELETE: info_agent/utils/           # Moved to utils/
DELETE: info_agent/pipeline/        # Empty
DELETE: info_agent/chat_test.py     # Duplicate
DELETE: info_agent/main.py          # Unused
DELETE: info_agent/create_admin_user.py  # One-time script
DELETE: info_agent/call_logs/       # Old logs
```

### Step 1.3: Delete other dead files/folders
```
DELETE: extra/                      # Old JSON files
DELETE: test_traces.json/           # Test artifacts
DELETE: setting.local.json          # Typo duplicate
DELETE: DEPLOYMENT_GUIDE.md         # Duplicate of DEPLOYMENT.md
```

## Phase 2: Create New Folders

### Step 2.1: Create folder structure
```bash
mkdir -p api tests/load scripts
```

## Phase 3: Move Files

### Step 3.1: Move API files
```
info_agent/api/chat.py  → api/chat.py
info_agent/api/qa.py    → api/qa.py
info_agent/api/__init__.py → api/__init__.py
```
Update `bot.py`:
```python
# Old: from info_agent.api.chat import router as chat_router
# New: from api.chat import router as chat_router
```

### Step 3.2: Move test files
```
test_booking_api.py        → tests/test_booking_api.py
test_langfuse_connection.py → tests/test_langfuse_connection.py
load_test/*                → tests/load/
```
Create `tests/__init__.py`

### Step 3.3: Move scripts
```
deploy.sh          → scripts/deploy.sh
rollback.sh        → scripts/rollback.sh
talkdesk_hangup.py → scripts/talkdesk_hangup.py
```

## Phase 4: Rename Files (Optional)

### Step 4.1: Rename cryptic service files
```
services/amb_json_flow_eng.py → services/flow_json_service.py
services/get_flowNb.py        → services/flow_number_service.py
services/slotAgenda.py        → services/slot_agenda.py
```
**Note**: Requires updating all imports

## Phase 5: Delete Empty info_agent/

After moving api/, delete remaining:
```
DELETE: info_agent/  (entire folder)
```

## Phase 6: Update Documentation

### Step 6.1: Update CLAUDE.md with new structure
### Step 6.2: Update any path references in docs/

## Files Changed Summary

| Action | File/Folder |
|--------|-------------|
| DELETE | info_agent/flows/, services/, config/, utils/, pipeline/ |
| DELETE | extra/, test_traces.json/, setting.local.json |
| DELETE | DEPLOYMENT_GUIDE.md |
| MOVE | info_agent/api/ → api/ |
| MOVE | test_*.py → tests/ |
| MOVE | load_test/ → tests/load/ |
| MOVE | *.sh, talkdesk_hangup.py → scripts/ |
| RENAME | 3 service files (optional) |
| UPDATE | bot.py imports |
| UPDATE | CLAUDE.md |

## Testing Strategy

After each phase:
```bash
python -c "import bot; print('✓ bot.py imports OK')"
python chat_test.py --start-node=router  # Quick flow test
```

## Rollback Plan

Git commit after each phase. If issues:
```bash
git checkout HEAD~1 -- .
```
