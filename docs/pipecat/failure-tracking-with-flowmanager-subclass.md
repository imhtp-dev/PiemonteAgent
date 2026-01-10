# Failure Tracking via FlowManager Subclass

## Problem
Need to track agent failures across all nodes and automatically transfer to human operator after threshold is reached. Options were:
1. Wrap each handler individually (tedious, error-prone)
2. Subclass FlowManager and override internal method (clean, centralized)

## Solution
Subclass `FlowManager` and override `_call_handler()` to intercept ALL handler calls in ONE place.

```python
from pipecat_flows import FlowManager

class TrackedFlowManager(FlowManager):
    async def _call_handler(self, handler, args):
        # Initialize tracker
        if "failure_tracker" not in self.state:
            FailureTracker.initialize(self.state)

        # Call original
        result = await super()._call_handler(handler, args)

        # Track failures
        if isinstance(result, tuple):
            result_dict, next_node = result
            if not result_dict.get("success", True):
                if FailureTracker.record_failure(...):
                    return result_dict, create_transfer_node()

        return result
```

## Key Code Reference
- `flows/tracked_flow_manager.py` - TrackedFlowManager subclass
- `utils/failure_tracker.py` - FailureTracker utility
- `flows/manager.py` - Uses TrackedFlowManager

## Gotchas
1. `_call_handler` is internal API (prefixed with `_`) - may break on pipecat-flows update
2. Add startup validation: `if not hasattr(FlowManager, '_call_handler'): raise RuntimeError(...)`
3. Pin pipecat-flows version in requirements.txt
4. Handler results can be `dict` OR `tuple(dict, NodeConfig)` - handle both

## Three-Tier Failure Thresholds
| Scenario | Threshold |
|----------|-----------|
| Knowledge gap (KB returns nothing) | 1 |
| User requested transfer + fail | 1 |
| Normal technical failures | 3 |

## Date Learned
2025-12-26

## Related
- `_refs/pipecat-flows/src/pipecat_flows/manager.py:434-464` - Original _call_handler
