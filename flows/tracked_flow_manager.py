"""
TrackedFlowManager - FlowManager with Automatic Failure Tracking

Subclasses pipecat-flows FlowManager to intercept ALL handler calls
and automatically track failures. No changes needed to individual handlers.

Failure Thresholds:
- Knowledge gap (agent doesn't know): 1 (immediate transfer)
- User requested transfer + fail: 1 (immediate transfer)
- Normal technical failures: 3

Usage:
    Replace FlowManager with TrackedFlowManager in flows/manager.py
"""

from typing import Any, Dict, Tuple, Union
from loguru import logger

from pipecat_flows import FlowManager, NodeConfig
from pipecat_flows.types import FlowArgs, FlowResult, FunctionHandler

from utils.failure_tracker import FailureTracker


def validate_pipecat_flows_compatibility() -> None:
    """
    Validate that pipecat-flows has the expected internal API.

    Call this at startup to catch compatibility issues early.
    Raises RuntimeError if _call_handler method is missing.
    """
    if not hasattr(FlowManager, '_call_handler'):
        raise RuntimeError(
            "pipecat-flows version incompatible: _call_handler method not found. "
            "TrackedFlowManager requires pipecat-flows with _call_handler method. "
            "Please check pipecat-flows version or update TrackedFlowManager implementation."
        )
    logger.debug("✅ pipecat-flows compatibility validated")


# Validate on module import
validate_pipecat_flows_compatibility()


class TrackedFlowManager(FlowManager):
    """
    FlowManager with automatic failure tracking.

    Overrides _call_handler to intercept ALL handler calls and track failures.
    When failure threshold is reached, automatically transitions to transfer node.

    No changes needed to individual handlers - tracking is completely automatic.
    """

    async def _call_handler(
        self,
        handler: FunctionHandler,
        args: FlowArgs
    ) -> Union[FlowResult, Tuple[Dict[str, Any], NodeConfig]]:
        """
        Override to add failure tracking to every handler call.

        This intercepts ALL handler invocations and:
        1. Initializes failure tracker if needed
        2. Calls the original handler
        3. Checks result for failure/knowledge gap
        4. Tracks failures and determines if transfer needed
        5. Returns modified result with transfer node if threshold reached
        """
        handler_name = getattr(handler, "__name__", "unknown_handler")

        # Initialize failure tracker if not exists
        if "failure_tracker" not in self.state:
            FailureTracker.initialize(self.state)

        # Call original handler via parent class
        try:
            result = await super()._call_handler(handler, args)
        except Exception as e:
            # Handler threw exception - track as failure
            logger.error(f"❌ Handler {handler_name} raised exception: {e}")
            should_transfer = FailureTracker.record_failure(
                self.state,
                reason=f"Exception: {str(e)}",
                handler_name=handler_name
            )
            if should_transfer:
                from flows.nodes.transfer import create_transfer_node
                return {"success": False, "error": str(e)}, create_transfer_node()
            raise  # Re-raise if not transferring

        # Parse result - could be dict or tuple(dict, NodeConfig)
        if isinstance(result, tuple):
            result_dict, next_node = result
        else:
            result_dict = result
            next_node = None

        # Ensure result_dict is a dict
        if not isinstance(result_dict, dict):
            result_dict = {"result": result_dict}

        # Check for knowledge gap (immediate transfer trigger)
        if FailureTracker.is_knowledge_gap(result_dict):
            FailureTracker.mark_knowledge_gap(self.state)

        # Check if handler reported failure
        success = result_dict.get("success", True)

        if not success:
            # Check if this is an ignorable error (user can fix)
            if FailureTracker.is_ignorable_error(result_dict):
                logger.debug(f"⏭️ Ignorable error in {handler_name} - not counting as failure")
            else:
                # Track the failure
                reason = result_dict.get("message") or result_dict.get("error") or "Unknown failure"
                should_transfer = FailureTracker.record_failure(
                    self.state,
                    reason=str(reason),
                    handler_name=handler_name
                )

                if should_transfer:
                    # Override next_node to transfer
                    logger.warning(f"🚨 Transferring to operator after {handler_name} failure")
                    from flows.nodes.transfer import create_transfer_node
                    return result_dict, create_transfer_node()
        else:
            # Success - reset failure counter
            FailureTracker.reset(self.state)

        # Return original result (possibly modified next_node)
        if isinstance(result, tuple):
            return result_dict, next_node
        else:
            return result_dict
