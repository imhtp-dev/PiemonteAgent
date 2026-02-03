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
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from pipecat_flows import FlowManager, NodeConfig
from pipecat_flows.types import FlowArgs, FlowResult, FunctionHandler

from utils.failure_tracker import FailureTracker
from utils.tracing import add_flow_state_attributes, trace_error

# Get tracer for handler spans
_tracer = trace.get_tracer("flow_handlers")


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
    FlowManager with automatic failure tracking and node transition tracing.

    Overrides _call_handler to intercept ALL handler calls and track failures.
    Overrides _set_node to emit spans for every node transition.
    When failure threshold is reached, automatically transitions to transfer node.

    No changes needed to individual handlers - tracking is completely automatic.
    """

    async def _set_node(self, node_id: str, node_config) -> None:
        """Override to emit a span for every node transition (visible in LangFuse)."""
        previous_node = self.state.get("current_node", "none")

        with _tracer.start_as_current_span(f"node.{node_id}") as span:
            span.set_attribute("node.name", node_id)
            span.set_attribute("node.previous", previous_node)
            span.set_attribute("node.transition", f"{previous_node} → {node_id}")

            # Add flow state context
            add_flow_state_attributes(self.state)

            # Track node in state
            self.state["current_node"] = node_id
            self.state.setdefault("node_history", []).append(node_id)
            span.set_attribute("node.history_length", len(self.state["node_history"]))

            try:
                await super()._set_node(node_id, node_config)
                span.set_attribute("node.transition_success", True)
                logger.info(f"📍 Node transition: {previous_node} → {node_id}")
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)[:200]))
                span.record_exception(e)
                span.set_attribute("node.transition_success", False)
                logger.error(f"❌ Node transition failed: {previous_node} → {node_id}: {e}")
                raise

    async def _call_handler(
        self,
        handler: FunctionHandler,
        args: FlowArgs
    ) -> Union[FlowResult, Tuple[Dict[str, Any], NodeConfig]]:
        """
        Override to add failure tracking and LangFuse tracing to every handler call.

        This intercepts ALL handler invocations and:
        1. Creates OpenTelemetry span for the handler (visible in LangFuse)
        2. Adds flow state attributes to the span
        3. Initializes failure tracker if needed
        4. Calls the original handler
        5. Checks result for failure/knowledge gap
        6. Tracks failures and determines if transfer needed
        7. Records errors to the span for LangFuse visibility
        8. Returns modified result with transfer node if threshold reached
        """
        handler_name = getattr(handler, "__name__", "unknown_handler")

        # Create span for this handler call (visible in LangFuse)
        with _tracer.start_as_current_span(f"handler.{handler_name}") as span:
            # Add handler metadata
            span.set_attribute("handler.name", handler_name)
            span.set_attribute("handler.module", getattr(handler, "__module__", "unknown"))

            # Add flow state attributes for debugging
            add_flow_state_attributes(self.state)

            # Add handler args (truncated for safety)
            if args:
                try:
                    args_str = str(args)[:300]
                    span.set_attribute("handler.args", args_str)
                except Exception:
                    pass

            # Initialize failure tracker if not exists
            if "failure_tracker" not in self.state:
                FailureTracker.initialize(self.state)

            # Call original handler via parent class
            try:
                result = await super()._call_handler(handler, args)
                span.set_attribute("handler.success", True)
            except Exception as e:
                # Handler threw exception - record to LangFuse and track as failure
                logger.error(f"❌ Handler {handler_name} raised exception: {e}")

                # Record exception to LangFuse span
                span.set_status(Status(StatusCode.ERROR, str(e)[:200]))
                span.record_exception(e)
                span.set_attribute("handler.success", False)
                span.set_attribute("handler.error_type", type(e).__name__)
                span.set_attribute("handler.error_message", str(e)[:500])

                should_transfer = FailureTracker.record_failure(
                    self.state,
                    reason=f"Exception: {str(e)}",
                    handler_name=handler_name
                )
                span.set_attribute("handler.triggered_transfer", should_transfer)

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

            # Add result summary to span
            try:
                result_keys = list(result_dict.keys())[:10]
                span.set_attribute("handler.result_keys", str(result_keys))
            except Exception:
                pass

            # Check for knowledge gap (immediate transfer trigger)
            if FailureTracker.is_knowledge_gap(result_dict):
                FailureTracker.mark_knowledge_gap(self.state)
                span.set_attribute("handler.knowledge_gap", True)

            # Check if handler reported failure
            success = result_dict.get("success", True)

            if not success:
                # Record failure to span
                failure_msg = result_dict.get("message") or result_dict.get("error") or "Unknown failure"
                span.set_attribute("handler.success", False)
                span.set_attribute("handler.failure_reason", str(failure_msg)[:200])

                # Check if this is an ignorable error (user can fix)
                if FailureTracker.is_ignorable_error(result_dict):
                    logger.debug(f"⏭️ Ignorable error in {handler_name} - not counting as failure")
                    span.set_attribute("handler.ignorable_error", True)
                else:
                    # Track the failure
                    reason = str(failure_msg)
                    should_transfer = FailureTracker.record_failure(
                        self.state,
                        reason=reason,
                        handler_name=handler_name
                    )
                    span.set_attribute("handler.triggered_transfer", should_transfer)

                    # Add failure count to span
                    failure_count = self.state.get("failure_tracker", {}).get("count", 0)
                    span.set_attribute("flow.failure_count", failure_count)

                    if should_transfer:
                        # Override next_node to transfer
                        logger.warning(f"🚨 Transferring to operator after {handler_name} failure")
                        span.set_attribute("handler.outcome", "transfer_triggered")
                        from flows.nodes.transfer import create_transfer_node
                        return result_dict, create_transfer_node()
            else:
                # Success - reset failure counter UNLESS pending_transfer
                # pending_transfer means user requested transfer but we're asking what they need
                # We don't want to reset the transfer_requested flag in that case
                if result_dict.get("pending_transfer"):
                    logger.debug("⏸️ Pending transfer - not resetting failure tracker")
                    span.set_attribute("handler.outcome", "success_pending_transfer")
                else:
                    FailureTracker.reset(self.state)
                    span.set_attribute("handler.outcome", "success")

            # Add next node info to span
            if next_node:
                try:
                    next_node_name = getattr(next_node, 'name', str(type(next_node).__name__))
                    span.set_attribute("handler.next_node", str(next_node_name)[:100])
                except Exception:
                    pass

            # Return original result (possibly modified next_node)
            if isinstance(result, tuple):
                return result_dict, next_node
            else:
                return result_dict
