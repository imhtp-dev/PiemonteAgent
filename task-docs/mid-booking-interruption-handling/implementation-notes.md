# Implementation Notes: Mid-Booking Interruption Handling

## Problem Statement

When user requests transfer or asks a question mid-booking:
- Current: Returns static message, flow gets stuck or ends
- Desired: Handle interruption → return to exact booking position

## Current Implementation Analysis

### Global Functions (flows/global_functions.py)

8 global functions available at every node:
1. `knowledge_base_lombardia` - FAQs
2. `get_competitive_pricing` - Agonistic pricing
3. `get_price_non_agonistic_visit_lombardia` - Non-agonistic pricing
4. `get_exam_by_visit` - Exams by visit type
5. `get_exam_by_sport` - Exams by sport
6. `call_graph_lombardia` - Clinic info
7. `request_transfer` - Transfer to human ⚠️ PROBLEM
8. `start_booking` - Start booking flow

### Current Behavior (global_handlers.py)

**Info functions (KB, pricing, exams, clinic)**:
- Return `(result, None)` → stay at current node ✅ WORKS
- Have `_add_booking_reminder()` helper that adds continuation message

**Transfer function**:
- Returns `(result, None)` → stays at current node ✅
- But LLM doesn't know to continue booking - just responds to transfer request
- No mechanism to "resume" where user was

### Key Finding from Pipecat-Flows Research

**NO built-in "return to previous node" mechanism**

FlowManager tracks:
- `_current_node` - only node ID, not full config
- `state` - persistent dict across transitions

**What we need to implement manually:**
1. Store "previous node config" before any interruption
2. After handling interruption, restore that node config

## Existing Partial Solution

```python
def _add_booking_reminder(result: Dict[str, Any], flow_manager: FlowManager) -> Dict[str, Any]:
    """Add booking continuation reminder if booking is in progress"""
    if flow_manager.state.get("booking_in_progress"):
        result["continue_booking_reminder"] = "Ora continuiamo con la tua prenotazione."
    return result
```

This adds a text reminder but:
- LLM doesn't always continue the booking
- No actual node restoration
- Flow position is lost

## Technical Solution Options

### Option A: Store Node Config in State (Recommended)

1. Before any booking node transition, store current node config
2. Global functions that handle interruptions check `booking_in_progress`
3. After handling, return stored node config to resume

```python
# In each booking handler, before transitioning:
flow_manager.state["current_booking_node_config"] = create_current_node()
flow_manager.state["current_booking_node_name"] = "collect_phone"

# In global_request_transfer, after handling:
if flow_manager.state.get("booking_in_progress"):
    return result, flow_manager.state.get("current_booking_node_config")
```

### Option B: Re-create Node from State

1. Store minimal info needed to recreate node
2. Have a factory function that recreates node from state

```python
flow_manager.state["booking_position"] = {
    "node_type": "collect_phone",
    "params": {"phone": "393..."}
}

# Then recreate:
node = recreate_booking_node(flow_manager.state["booking_position"])
```

### Option C: LLM-Driven Continuation (Current Approach - Weak)

Just tell LLM to continue booking in the result message.
- Unreliable - LLM may not comply
- No actual flow restoration
- Current approach, not working well

## Files to Modify

1. **flows/handlers/global_handlers.py**
   - Modify `global_request_transfer` to return to stored booking node
   - Modify info handlers to return stored booking node on error

2. **flows/handlers/booking_handlers.py** (or each booking handler)
   - Store current node config in state before transitions

3. **flows/nodes/booking.py**
   - Each node creation should store itself in state

## State Keys Needed

```python
flow_manager.state["booking_in_progress"] = True  # Already exists
flow_manager.state["current_booking_node_config"] = NodeConfig(...)  # NEW
flow_manager.state["current_booking_node_name"] = "collect_phone"  # NEW
flow_manager.state["booking_position_data"] = {...}  # NEW - data to recreate node
```

## Complexity Consideration

Some nodes are stateful and need data to recreate:
- `slot_selection` needs slots list
- `center_selection` needs centers list
- `booking_summary` needs all booking data

**Solution**: Store the creation params, not just the config
