# Implementation Notes: Global Info Functions

## Pipecat Global Functions Research

### What are Global Functions?

Global functions (`global_functions`) are functions available at **every conversation node**, regardless of current state. They are:
- Defined once during `FlowManager` initialization
- Automatically prepended to all node function lists
- Ideal for cross-cutting features (FAQ, info, transfer)
- **Do NOT interrupt flow** - execute and return without forcing transition

**Key code** from `manager.py:793-794`:
```python
# Mix in global functions that should be available at every node
functions_list = self._global_functions + functions_list
```

### Registration Syntax

```python
flow_manager = FlowManager(
    task=task,
    llm=llm,
    context_aggregator=context_aggregator,
    transport=transport,
    global_functions=[func1, func2, ...],  # List of functions
)
```

### Two Formats Supported

#### Format A: FlowsFunctionSchema
```python
get_info_func = FlowsFunctionSchema(
    name="get_info",
    handler=async_handler,
    description="Get information",
    properties={"query": {"type": "string"}},
    required=["query"],
)
```

#### Format B: Direct Functions
```python
async def get_info(
    flow_manager: FlowManager,
) -> tuple[Result, None]:
    return Result(), None
```

### Return Pattern

Global functions **MUST** return `tuple(result, next_node)`:
- Return `(result, None)` to stay at current node (most common)
- Return `(result, NodeConfig)` to transition to new node

### State Access

Global functions receive `flow_manager` and can fully access state:
```python
async def global_func(flow_manager: FlowManager):
    data = flow_manager.state.get("key")
    flow_manager.state["new_key"] = value
    return Result(), None
```

---

## Current Info Agent Analysis

### 6 Tools in Info Agent

| # | Function Name | Handler | Parameters | Dependencies |
|---|---------------|---------|------------|--------------|
| 1 | `knowledge_base_lombardia` | `query_knowledge_base_handler` | `query: str` | knowledge_base_service |
| 2 | `get_competitive_pricing` | `get_competitive_pricing_handler` | `age, gender, sport, region` | pricing_service |
| 3 | `get_price_non_agonistic_visit_lombardia` | `get_non_competitive_pricing_handler` | `ecg_under_stress: bool` | pricing_service |
| 4 | `get_exam_by_visit` | `get_exam_by_visit_handler` | `visit_type: enum` | exam_service |
| 5 | `get_exam_by_sport` | `get_exam_by_sport_handler` | `sport: str` | exam_service |
| 6 | `call_graph_lombardia` | `get_clinic_info_handler` | `query: str` | clinic_info_service |
| 7 | `request_transfer` | `request_transfer_handler` | `reason: str` | escalation_service |

### Handler Signature Pattern (Current)
```python
async def handler(
    args: FlowArgs,
    flow_manager: FlowManager
) -> Tuple[Dict[str, Any], NodeConfig]
```

### Key Insight: Handler Compatibility

Current handlers return `Tuple[Dict, NodeConfig]` but global functions need:
- `Tuple[Result, None]` to stay at current node
- `Tuple[Result, NodeConfig]` to transition

**Solution**: Wrap existing handlers to return `None` instead of greeting node for success cases.

---

## 6 Separate Tools vs 1-2 Unified Tools

### Option A: 6 Separate Global Functions

**Pros:**
- LLM knows exact tool for each task
- Clear parameter requirements per function
- Smaller context per function call
- Easier debugging (know which tool failed)
- Parallel development/testing

**Cons:**
- More tokens in system prompt (6 tool definitions)
- LLM must choose between more options
- More code to maintain

### Option B: 1-2 Unified Tools

**Example unified approach:**
```python
async def get_info(args: FlowArgs, flow_manager: FlowManager):
    intent = args.get("intent")  # "pricing", "exam", "clinic", etc.
    # Route internally to sub-handlers
```

**Pros:**
- Fewer tokens in system prompt
- Simpler LLM decision (fewer choices)
- Single entry point

**Cons:**
- LLM must correctly identify intent + provide right params
- More complex parameter validation
- Harder to debug (which sub-handler failed?)
- Single point of failure
- Loses specificity (LLM may provide wrong params)

### Recommendation: 6 Separate Tools

For your use case, **6 separate tools is better** because:
1. Each tool has distinct parameters (age/gender/sport vs query vs visit_type)
2. LLM knows exactly what params to collect
3. Easier to maintain and debug
4. No additional routing logic needed
5. Matches current working implementation

---

## Flow Scenarios Analysis

### Scenario 1: Info-only call
```
Patient calls → Asks info question → Global function answers → Patient hangs up
```
**Works**: Global function answers, no booking flow started, conversation ends naturally.

### Scenario 2: Booking-only call
```
Patient calls → "I want to book" → Booking flow starts → Completes → Hangs up
```
**Works**: Router starts booking flow, global functions available but not called.

### Scenario 3: Info mid-booking
```
Patient calls → "I want to book" → Booking flow → "Wait, what are your hours?"
→ Global function answers → Booking continues from state → Completes
```
**Works**: Global function answers, returns `None`, booking state preserved, continues.

### Scenario 4: Booking after info
```
Patient calls → Asks info → Global function → "OK I want to book"
→ ??? How to start booking?
```
**Challenge**: Global functions return `None` to stay at current node. Need a way to transition to booking.

**Solution**: Make `start_booking` a global function that DOES transition:
```python
async def start_booking(flow_manager: FlowManager):
    return Result(), create_booking_greeting_node()  # Transitions!
```

### Scenario 5: Transfer anytime
```
Patient calls → Any point → "Let me talk to a human" → Transfer
```
**Works**: `request_transfer` global function transitions to transfer node.

---

## Key Files to Modify

1. `flows/manager.py` - Add global_functions to FlowManager init
2. `flows/handlers/global_handlers.py` (new) - Global function wrappers
3. `flows/nodes/greeting.py` - Remove redundant info routing
4. `bot.py` - Potentially simplify agent routing

## Key Files to Reference

1. `info_agent/flows/handlers/api_handlers.py` - Current handler implementations
2. `info_agent/flows/handlers/transfer_handlers.py` - Transfer logic
3. `info_agent/services/*.py` - Service implementations (reuse as-is)
4. `_refs/pipecat-flows/examples/food_ordering.py` - Global function example
