# Status: Codebase Restructure

## Current Phase: PLANNING COMPLETE - AWAITING REVIEW

---

## Analysis Summary

### Files to DELETE (dead code)
| Folder/File | Reason |
|-------------|--------|
| `info_agent/flows/` | Replaced by global_functions |
| `info_agent/services/` | Moved to main services/ |
| `info_agent/config/` | Moved to config/ |
| `info_agent/utils/` | Moved to utils/ |
| `info_agent/pipeline/` | Empty |
| `info_agent/*.py` (except api/) | Unused |
| `extra/` | Old JSON files |
| `test_traces.json/` | Test artifacts |
| `setting.local.json` | Typo duplicate |
| `DEPLOYMENT_GUIDE.md` | Duplicate of DEPLOYMENT.md |

### Files to MOVE
| From | To |
|------|------|
| `info_agent/api/*` | `api/` |
| `test_*.py` | `tests/` |
| `load_test/` | `tests/load/` |
| `*.sh`, `talkdesk_hangup.py` | `scripts/` |

### New Folder Structure
```
api/          # HTTP endpoints (new)
tests/        # Test files (new)
  load/       # Load testing
scripts/      # Utility scripts (new)
```

---

## Pending Decisions (Need Your Input)

1. **Rename cryptic service files?** (amb_json_flow_eng.py, get_flowNb.py, slotAgenda.py)
2. **What to do with primer.md, agent_routing_flow.md?** (move to docs? delete?)
3. **Keep chat_service.py?** (verify if used)
4. **Rename CLAUDE.md → README.md?**

---

## What's Next

After your review:
1. Execute Phase 1: Delete dead code
2. Execute Phase 2: Create new folders
3. Execute Phase 3: Move files
4. Execute Phase 4: Update imports
5. Execute Phase 5: Update documentation
6. Test and verify
