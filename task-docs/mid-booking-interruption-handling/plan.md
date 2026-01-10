# Plan: Mid-Booking Interruption Handling

## Overview

Enable users to ask questions or request transfer mid-booking, then automatically resume booking from where they left off.

## Approach

**Store current booking node** in `flow_manager.state` at each transition, then **restore it** after handling interruptions.

## Implementation Steps

### Step 1: Create Node Storage Helper

Create helper function to store current booking position:

```python
# In flows/handlers/booking_handlers.py or new file

def store_booking_position(flow_manager: FlowManager, node_config: NodeConfig, node_name: str):
    """Store current booking node for resumption after interruption"""
    if flow_manager.state.get("booking_in_progress"):
        flow_manager.state["_resume_node_config"] = node_config
        flow_manager.state["_resume_node_name"] = node_name
        logger.debug(f"📍 Stored booking position: {node_name}")
```

### Step 2: Store Position at Key Booking Nodes

At each major booking node transition, call the helper:

**Nodes to track:**
- `flow_navigation` (asking prescription questions)
- `final_center_selection` (selecting center)
- `collect_datetime` (asking date/time)
- `slot_selection` (selecting slot)
- `booking_summary_confirmation` (confirming summary)
- `collect_full_name`, `collect_phone`, etc. (personal info)

### Step 3: Modify Global Request Transfer

```python
async def global_request_transfer(args, flow_manager) -> Tuple[Dict, Optional[NodeConfig]]:
    # ... existing logic ...

    # After handling transfer request, check if booking was in progress
    if flow_manager.state.get("booking_in_progress"):
        resume_node = flow_manager.state.get("_resume_node_config")
        if resume_node:
            logger.info(f"📍 Resuming booking at: {flow_manager.state.get('_resume_node_name')}")
            return {
                "success": True,
                "message": "Capito. Ora continuiamo con la prenotazione.",
                "handled": True
            }, resume_node  # Return stored node to resume

    # Default: stay at current node
    return result, None
```

### Step 4: Handle Info Function Failures

If KB/pricing/etc fails mid-booking, resume instead of transferring:

```python
async def global_knowledge_base(args, flow_manager):
    # ... try to answer ...

    if not result.success:
        # Check if booking in progress
        if flow_manager.state.get("booking_in_progress"):
            resume_node = flow_manager.state.get("_resume_node_config")
            return {
                "success": False,
                "message": "Non ho trovato questa informazione. Continuiamo con la prenotazione."
            }, resume_node  # Resume booking instead of transfer
        else:
            # Not booking, do transfer
            return result, create_transfer_node()
```

### Step 5: Update LLM Prompts

Add instruction to booking nodes:

```
If user asks an unrelated question, the global functions will handle it.
After handling, continue exactly where you left off in the booking process.
```

## Files to Modify

| File | Changes |
|------|---------|
| `flows/handlers/global_handlers.py` | Modify transfer + info handlers to resume booking |
| `flows/handlers/booking_handlers.py` | Add `store_booking_position()` calls |
| `flows/handlers/patient_detail_handlers.py` | Add `store_booking_position()` calls |
| `flows/handlers/flow_handlers.py` | Add `store_booking_position()` calls |

## Testing Strategy

```bash
# Test mid-booking transfer request
python voice_test.py --start-node booking

# Test flow:
1. Say "RX caviglia" (start booking)
2. After center selection, say "transfer me to human"
3. Agent should ask what you need
4. Say "actually continue with booking"
5. Should resume at center selection
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Complex nodes hard to recreate | Store NodeConfig directly, not params |
| State pollution | Clear `_resume_*` keys after successful resumption |
| LLM doesn't comply | Strong prompt + function result forces node transition |
