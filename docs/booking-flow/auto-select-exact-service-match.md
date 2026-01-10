# Auto-Select Service on Exact Match

## Problem
When patient says "I want to book RX Caviglia Destra" and the service exists, agent would still list all search results and ask "Which one do you want?" - unnecessary step that slows down the flow.

## Solution
After searching services, compare normalized search term against normalized service names. If exact match found, auto-select and skip to next node.

```python
def _normalize_service_name(name: str) -> str:
    """Normalize service name for comparison"""
    if not name:
        return ""
    # Lowercase, strip whitespace, collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', name.lower().strip())
    return normalized

def _find_exact_match(search_term: str, services: List[HealthService]) -> Optional[HealthService]:
    """Find exact match between search term and service names"""
    normalized_search = _normalize_service_name(search_term)

    for service in services:
        normalized_name = _normalize_service_name(service.name)
        if normalized_search == normalized_name:
            return service
    return None
```

In search handler:
```python
exact_match = _find_exact_match(search_term, search_result.services)

if exact_match:
    # Auto-select - skip selection node
    flow_manager.state["selected_services"].append(exact_match)
    return result, create_collect_address_node()  # Skip to address

# No exact match - show options as usual
return result, create_service_selection_node(services, search_term)
```

## Key Code Reference
- `flows/handlers/service_handlers.py` - lines 15-38 (helper functions)
- `flows/handlers/service_handlers.py` - lines 106-129 (auto-select in main search)
- `flows/handlers/service_handlers.py` - lines 234-257 (auto-select in refined search)

## Gotchas
1. **Don't use substring matching** - "RX Caviglia" would match both "RX Caviglia Destra" and "RX Caviglia Sinistra"
2. **Normalize both sides** - API returns services with trailing spaces like "RX Caviglia Destra "
3. **Only exact match** - Partial matches should still show options
4. **Latency**: < 5ms - just string comparison, no API calls

## Flow Comparison

**Before:**
1. Patient: "Book RX Caviglia Destra"
2. Agent: "I found RX Caviglia Destra, RX Colonna... Which one?"
3. Patient: "The first one"
4. Agent: "What's your address?"

**After:**
1. Patient: "Book RX Caviglia Destra"
2. Agent: "Perfect! What's your address?"

## Date Learned
2026-01-02

## Related
- `docs/booking-flow/` - Other booking flow optimizations
