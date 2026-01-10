# Decisions: Global Info Functions

## Decisions Made

### 1. Register All 6 Info Tools as Global (Not Unified)

**Decision:** Keep 6 separate global functions instead of 1-2 unified tools.

**Rationale:**
- Each tool has distinct parameters (age/gender/sport vs query vs visit_type)
- LLM knows exactly what params to collect for each
- Easier to debug (know which tool failed)
- Matches current working implementation
- No additional routing logic needed

**Trade-off accepted:** More tokens in system prompt (6 tool definitions), but clearer intent detection.

---

### 2. Add `start_booking` as 8th Global Function

**Decision:** Create a global function that transitions to booking flow.

**Rationale:**
- Without this, patient asking "I want to book" after info questions can't start booking
- Global functions normally return `None` (no transition)
- `start_booking` is special - it returns `NodeConfig` to transition

**Implementation:**
```python
async def global_start_booking(args, flow_manager):
    flow_manager.state["booking_in_progress"] = True
    return {"success": True}, create_booking_greeting_node()  # Transitions!
```

---

### 3. `request_transfer` as Global Function (Transitions)

**Decision:** Transfer also transitions (to transfer node).

**Rationale:**
- Patient can ask for human at any time
- Transfer node shows goodbye message
- Escalation API called before transition
- Same pattern as current transfer handler

---

### 4. Simplify Router to Empty Functions Node

**Decision:** Router node has no functions - relies entirely on global functions.

**Rationale:**
- All 8 global functions handle all scenarios
- No need for router-specific logic
- Simpler system prompt
- Router just greets, globals handle everything

---

### 5. Move Services to Main services/ Folder

**Decision:** Move `info_agent/services/*.py` to `services/` folder.

**Rationale:**
- Cleaner code structure (fewer nested folders)
- All services in one place
- Services are well-tested, just moving files
- Update imports in new global handlers

---

### 6. Keep Analytics Tracking

**Decision:** All global handlers call `call_extractor.add_function_call()`.

**Rationale:**
- Maintain Supabase analytics
- Same tracking as current info agent
- No loss of data

---

### 7. Keep Agent Routing Infrastructure

**Decision:** Keep `agent_routing_handlers.py` but only remove info agent routing.

**Rationale:**
- May need to integrate other agents in future
- Only remove `route_to_info` logic
- Keep `route_to_booking` logic
- Infrastructure stays for future extensibility

---

## Resolved Decisions (User Confirmed)

### 1. info_agent folder → DELETE

**User Decision:** Delete entirely after migration.

**Rationale:** User has backup, wants clean codebase.

---

### 2. Default start node → Router

**User Decision:** Keep `start_node="router"` as default.

---

### 3. Booking → RE-ENABLE

**User Decision:** Re-enable booking flow with this change.

**Implementation:** `start_booking` global function transitions to booking greeting.

---

### 4. System prompts → Keep in place, clean up at end

**User Decision:** Leave in router node for now, clean up file structure at end of work.

---

## Assumptions Made

1. **Services work standalone** - `info_agent/services/*.py` can be imported without info_agent flow context

2. **Call extractor pattern stays same** - `get_call_extractor(session_id)` works from any handler

3. **State structure unchanged** - `flow_manager.state` keys like `session_id`, `business_status` remain same

4. **Talkdesk integration unchanged** - `stream_sid`, `interaction_id` handling stays same

5. **Transfer escalation unchanged** - Same API call pattern for escalation

6. **No new dependencies** - Only rearranging existing code, no new packages