# Implementation Plan: Global Info Functions

## Overview

Eliminate separate info agent by converting 6 info tools + transfer to **global functions** available at every node. This enables:
- Info questions answered from anywhere (including mid-booking)
- Booking flow re-enabled and accessible via global function
- State preservation across info calls during booking
- Cleaner code structure with fewer folders

## Architecture Change

### Before (Current)
```
Router Node
├── route_to_info → Info Agent (separate flow) ← REMOVE
└── route_to_booking → Booking Agent ← RE-ENABLE
```

### After (Proposed)
```
Router Node (default start)
├── Global Functions (available everywhere):
│   ├── knowledge_base_lombardia
│   ├── get_competitive_pricing
│   ├── get_price_non_agonistic_visit_lombardia
│   ├── get_exam_by_visit
│   ├── get_exam_by_sport
│   ├── call_graph_lombardia
│   ├── request_transfer → Transfer node
│   └── start_booking → Booking greeting node
└── Booking Flow: greeting → service → patient → booking → completion
```

## Code Structure Cleanup

### Before (Messy)
```
├── flows/
│   ├── handlers/
│   └── nodes/
├── info_agent/           ← REMOVE ENTIRELY
│   ├── flows/
│   │   ├── handlers/
│   │   └── nodes/
│   └── services/         ← MOVE to services/
├── services/
```

### After (Clean)
```
├── flows/
│   ├── handlers/
│   │   ├── booking_handlers.py
│   │   ├── global_handlers.py      ← NEW (info functions)
│   │   └── ...
│   ├── nodes/
│   │   ├── router.py
│   │   ├── greeting.py
│   │   └── ...
│   └── global_functions.py         ← NEW (schemas)
├── services/
│   ├── knowledge_base.py           ← MOVE from info_agent
│   ├── pricing_service.py          ← MOVE from info_agent
│   ├── exam_service.py             ← MOVE from info_agent
│   ├── clinic_info_service.py      ← MOVE from info_agent
│   ├── call_data_extractor.py      ← MOVE from info_agent
│   ├── escalation_service.py       ← MOVE from info_agent
│   └── ... (existing services)
```

---

## Step-by-Step Implementation

### Step 1: Move Services from info_agent to services/

**Move these files:**
```
info_agent/services/knowledge_base.py      → services/knowledge_base.py
info_agent/services/pricing_service.py     → services/pricing_service.py
info_agent/services/exam_service.py        → services/exam_service.py
info_agent/services/clinic_info_service.py → services/clinic_info_service.py
info_agent/services/call_data_extractor.py → services/call_data_extractor.py
info_agent/services/escalation_service.py  → services/escalation_service.py
```

**Update imports** in moved files (if any cross-references).

---

### Step 2: Create Global Handlers File

**File:** `flows/handlers/global_handlers.py` (NEW)

```python
from pipecat_flows import FlowManager, FlowArgs
from typing import Dict, Any, Tuple

# Import from NEW locations
from services.knowledge_base import knowledge_base_service
from services.pricing_service import pricing_service
from services.exam_service import exam_service
from services.clinic_info_service import clinic_info_service
from services.call_data_extractor import get_call_extractor

async def global_knowledge_base(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], None]:
    """Knowledge base query - available everywhere."""
    query = args.get("query", "").strip()
    result = await knowledge_base_service.query(query)
    # Track analytics
    session_id = flow_manager.state.get("session_id")
    if session_id:
        extractor = get_call_extractor(session_id)
        extractor.add_function_call("knowledge_base_lombardia", {"query": query}, result)
    return {"success": True, "answer": result.answer}, None  # None = no transition

# Similar for other 5 info functions...

async def global_request_transfer(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """Transfer to human - DOES transition to transfer node."""
    from flows.nodes.transfer import create_transfer_node
    # Handle escalation...
    return {"success": True}, create_transfer_node()  # Transitions!

async def global_start_booking(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]:
    """Start booking flow - DOES transition."""
    from flows.nodes.greeting import create_greeting_node
    flow_manager.state["booking_in_progress"] = True
    return {"success": True}, create_greeting_node()  # Transitions!
```

### Step 3: Create Global Function Schemas

**File:** `flows/global_functions.py` (NEW)

Define `FlowsFunctionSchema` for each global function:

```python
from pipecat_flows import FlowsFunctionSchema
from flows.handlers.global_handlers import (
    global_knowledge_base,
    global_competitive_pricing,
    global_non_competitive_pricing,
    global_exam_by_visit,
    global_exam_by_sport,
    global_clinic_info,
    global_request_transfer,
    global_start_booking,
)

GLOBAL_FUNCTIONS = [
    FlowsFunctionSchema(
        name="knowledge_base_lombardia",
        handler=global_knowledge_base,
        description="Search knowledge base for general info about services, preparations, documents",
        properties={
            "query": {
                "type": "string",
                "description": "Natural language question"
            }
        },
        required=["query"],
    ),
    FlowsFunctionSchema(
        name="get_competitive_pricing",
        handler=global_competitive_pricing,
        description="Get price for agonistic/competitive sports medical visit",
        properties={
            "age": {"type": "integer", "description": "Athlete age in years"},
            "gender": {"type": "string", "enum": ["M", "F"]},
            "sport": {"type": "string", "description": "Sport name in Italian"},
            "region": {"type": "string", "description": "Italian region/province"},
        },
        required=["age", "gender", "sport", "region"],
    ),
    # ... 4 more info functions ...
    FlowsFunctionSchema(
        name="request_transfer",
        handler=global_request_transfer,
        description="Transfer call to human operator",
        properties={
            "reason": {"type": "string", "description": "Reason for transfer"}
        },
        required=["reason"],
    ),
    FlowsFunctionSchema(
        name="start_booking",
        handler=global_start_booking,
        description="Start booking flow when patient wants to book an appointment",
        properties={
            "service_request": {"type": "string", "description": "What patient wants to book"}
        },
        required=["service_request"],
    ),
]
```

### Step 4: Modify FlowManager Initialization

**File:** `flows/manager.py` (MODIFY)

```python
from flows.global_functions import GLOBAL_FUNCTIONS

def create_flow_manager(...) -> FlowManager:
    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        transport=transport,
        global_functions=GLOBAL_FUNCTIONS,  # ADD THIS
    )
    return flow_manager
```

### Step 5: Update Router Node

**File:** `flows/nodes/router.py` (MODIFY)

Change router to be a simple greeting that relies on global functions + start_booking:

```python
def create_router_node() -> NodeConfig:
    return NodeConfig(
        name="router",
        role_messages=[
            {
                "role": "system",
                "content": """You are Ualà, virtual assistant for Cerba Healthcare.

Available tools (always available):
- knowledge_base_lombardia: Answer info questions
- get_competitive_pricing: Agonistic sports visit pricing
- get_price_non_agonistic_visit_lombardia: Non-agonistic pricing
- get_exam_by_visit: Required exams by visit type
- get_exam_by_sport: Required exams by sport
- call_graph_lombardia: Clinic hours, closures, doctors
- request_transfer: Transfer to human operator
- start_booking: Begin appointment booking

If patient wants info → use appropriate info tool
If patient wants to book → use start_booking
If patient wants human → use request_transfer
"""
            }
        ],
        functions=[],  # Empty! Global functions handle everything
        pre_actions=[
            {"type": "tts_say", "text": "Ciao, sono Ualà..."}
        ],
    )
```

### Step 6: Update bot.py Agent Routing

**File:** `bot.py` (MODIFY)

- Remove info agent imports
- Remove `route_to_info` logic from agent_routing_handlers.py
- Keep `route_to_booking` logic (for future agents)
- Re-enable booking flow
- Default start node = router

```python
# Remove info agent imports
# Keep agent_routing_handlers but only for booking routing
# Initialize single flow with router node
await initialize_flow_manager(flow_manager, start_node="router")
```

### Step 7: Handle Analytics/Call Data Extraction

**File:** `flows/handlers/global_handlers.py`

Ensure all global handlers track analytics:

```python
async def global_knowledge_base(args, flow_manager):
    # ... do work ...

    # Track for analytics
    session_id = flow_manager.state.get("session_id")
    if session_id:
        extractor = get_call_extractor(session_id)
        extractor.add_function_call(
            function_name="knowledge_base_lombardia",
            parameters={"query": query},
            result={"success": True, "answer": result.answer}
        )

    return {...}, None
```

### Step 8: Handle Call End Analysis

**File:** `bot.py` (MODIFY disconnect handler)

Use unified call data extraction:

```python
async def on_disconnect(...):
    call_extractor = flow_manager.state.get("call_extractor")
    if call_extractor:
        await call_extractor.save_to_database(flow_manager.state)
```

### Step 9: Delete info_agent Folder

After all changes are tested and working:

```bash
rm -rf info_agent/
```

User has backup, safe to delete.

### Step 10: Clean Up System Prompts (End of Work)

Move system prompts to dedicated location (do last):
- Consider `prompts/` folder or `config/prompts.py`
- Not blocking - can be done after main implementation

---

## Files Summary

### New Files
| File | Description |
|------|-------------|
| `flows/handlers/global_handlers.py` | 8 global function handlers |
| `flows/global_functions.py` | FlowsFunctionSchema definitions |

### Moved Files (info_agent → services)
| From | To |
|------|-----|
| `info_agent/services/knowledge_base.py` | `services/knowledge_base.py` |
| `info_agent/services/pricing_service.py` | `services/pricing_service.py` |
| `info_agent/services/exam_service.py` | `services/exam_service.py` |
| `info_agent/services/clinic_info_service.py` | `services/clinic_info_service.py` |
| `info_agent/services/call_data_extractor.py` | `services/call_data_extractor.py` |
| `info_agent/services/escalation_service.py` | `services/escalation_service.py` |

### Modified Files
| File | Changes |
|------|---------|
| `flows/manager.py` | Add `global_functions=GLOBAL_FUNCTIONS` |
| `flows/nodes/router.py` | Update to use global functions, enable booking |
| `bot.py` | Remove info agent, keep booking routing |
| `flows/handlers/agent_routing_handlers.py` | Remove info routing, keep booking routing |

### Deleted (After Testing)
| Folder | Reason |
|--------|--------|
| `info_agent/` | Entire folder - services moved, flows replaced by global functions |

---

## Testing Strategy

### Test with `--start-node`

1. **Test info-only flow:**
   ```bash
   python bot.py --start-node=router
   # Ask: "What are your hours in Milan?"
   # Expect: Global function answers, stays at router
   ```

2. **Test booking flow:**
   ```bash
   python bot.py --start-node=router
   # Say: "I want to book a sports exam"
   # Expect: start_booking transitions to booking flow
   ```

3. **Test info mid-booking:**
   ```bash
   python bot.py --start-node=greeting  # booking greeting
   # Start booking, then ask: "Wait, what exams do I need?"
   # Expect: Global function answers, booking state preserved
   ```

4. **Test transfer:**
   ```bash
   python bot.py --start-node=router
   # Say: "I want to talk to a person"
   # Expect: request_transfer transitions to transfer node
   ```

### Manual Test Scenarios

| Scenario | Steps | Expected |
|----------|-------|----------|
| Pure info | Call → Ask info → Hang up | Info answered, call ends |
| Pure booking | Call → "Book exam" → Complete | Booking flow completes |
| Info then booking | Call → Ask info → "Now book" → Complete | Both work |
| Booking then info | Call → Start booking → Ask info mid-flow → Continue | State preserved |
| Transfer anytime | Call → At any point say "human" | Transfers |