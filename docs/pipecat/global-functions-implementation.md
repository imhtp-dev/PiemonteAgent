# Pipecat Global Functions Implementation

## Problem
1. LLM "acknowledges" without calling functions - says "I'm checking" or "Perfetto, procedo" but never actually invokes the tool
2. Mid-booking info questions don't redirect user back to continue booking
3. Dynamic data (like `initial_booking_request`) can't be accessed by LLM via "check flow_manager.state"

## Solution

### 1. Global Functions Setup (pipecat-flows >= 0.0.22)

```python
from pipecat_flows import FlowManager, FlowsFunctionSchema

GLOBAL_FUNCTIONS = [
    FlowsFunctionSchema(
        name="function_name",
        description="...",
        properties={...},
        required=[...],
        handler=async_handler_function,
    ),
]

flow_manager = FlowManager(
    task=task,
    llm=llm,
    context_aggregator=context_aggregator,
    transport=transport,
    global_functions=GLOBAL_FUNCTIONS,  # Available at every node
)
```

### 2. Handler Return Patterns

```python
# Stay at current node (info queries)
return {"success": True, "answer": "..."}, None

# Transition to new node (booking, transfer)
return {"success": True}, create_next_node()
```

### 3. Injecting Dynamic Data into Prompts

LLM cannot access Python variables. Inject data directly into task_messages:

```python
def create_greeting_node(initial_booking_request: str = None) -> NodeConfig:
    if initial_booking_request:
        task_content = f"""User requested: "{initial_booking_request}"
IMMEDIATELY call search_health_services with search_term="{initial_booking_request}"."""
    else:
        task_content = "Ask what service they want..."

    return NodeConfig(task_messages=[{"role": "system", "content": task_content}], ...)
```

### 4. Forcing LLM to Call Functions

Add explicit instructions in role_messages:

```python
role_messages=[{
    "role": "system",
    "content": """CRITICAL: When user confirms (yes, correct, corretto, sì, ok, etc.):
→ IMMEDIATELY call verify_basic_info with action="confirm"
→ Do NOT just say "Perfetto, procedo" without calling the function"""
}]
```

### 5. Booking Continuation Reminder

Add reminder to info function responses when booking in progress:

```python
def _add_booking_reminder(result: Dict, flow_manager: FlowManager) -> Dict:
    if flow_manager.state.get("booking_in_progress"):
        result["continue_booking_reminder"] = "Ora continuiamo con la tua prenotazione."
    return result
```

## Key Code Reference
- `flows/global_functions.py` - 8 FlowsFunctionSchema definitions
- `flows/handlers/global_handlers.py` - Handler implementations with booking reminder
- `flows/nodes/greeting.py` - Dynamic prompt injection example
- `flows/nodes/patient_info.py:105-132` - Explicit function call instruction
- `flows/manager.py` - FlowManager creation with global_functions

## Gotchas

1. **Version requirement**: `pipecat-ai-flows>=0.0.22` required for `global_functions` parameter
2. **LLM can't access state**: Never tell LLM to "check flow_manager.state" - it can only see message content
3. **Empty functions list**: Nodes using only global functions should have `functions=[]`
4. **FlowManager creation location**: If creating FlowManager directly (not via helper), must include `global_functions` parameter

## Date Learned
2024-12-24

## Related
- `_refs/pipecat-flows/CHANGELOG.md` - v0.0.22 added global_functions
- `_refs/pipecat-flows/src/pipecat_flows/manager.py:106` - Parameter definition
