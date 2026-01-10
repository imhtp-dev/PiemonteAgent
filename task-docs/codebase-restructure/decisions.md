# Codebase Restructure - Decisions

## Decisions Made

### 1. Delete entire info_agent/ after moving api/
**Rationale**: All services migrated to global_functions. Only api/ still used.

### 2. Create api/ folder at root level
**Rationale**: Standard Python pattern. Clear separation of HTTP endpoints from business logic.

### 3. Create tests/ folder
**Rationale**: Standard Python convention. Keeps test files separate from source.

### 4. Create scripts/ folder
**Rationale**: Utility scripts (deploy, rollback) shouldn't clutter root.

### 5. Keep BridgeLombardia-main/ as-is
**Rationale**: Separate deployment concern. Could be its own repo later.

### 6. Keep task-docs/ temporarily
**Rationale**: Active planning documents. Can archive later.

---

## Pending Questions

### Q1: Rename service files with cryptic names?
Files like `amb_json_flow_eng.py`, `get_flowNb.py`, `slotAgenda.py`

**Options**:
- A) Rename now (requires import updates)
- B) Leave as-is (less risk)
- C) Rename later in separate PR

**Recommendation**: B (leave as-is) - focus on structure first

---

### Q2: What to do with `primer.md` and `agent_routing_flow.md`?
These are documentation files in root.

**Options**:
- A) Move to docs/
- B) Delete if outdated
- C) Leave in root

---

### Q3: Keep or delete `chat_service.py`?
This file creates a chat API but unclear if used in production.

**Options**:
- A) Keep (might be used)
- B) Move to api/ as alternative endpoint
- C) Verify usage and decide

---

### Q4: Consolidate CLAUDE.md and README.md?
Currently no README.md, CLAUDE.md serves that purpose.

**Options**:
- A) Rename CLAUDE.md → README.md
- B) Keep CLAUDE.md, create separate README.md
- C) Keep as-is

---

## Assumptions

1. `info_agent/api/chat.py` and `qa.py` are the only files still needed from info_agent/
2. Test files are not run in production CI/CD (moving won't break pipelines)
3. Git history preservation is acceptable (moves tracked by git)
