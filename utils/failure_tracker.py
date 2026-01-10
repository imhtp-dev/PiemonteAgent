"""
Failure Tracker Utility

Tracks agent failures and determines when to transfer to human operator.

Three-tier threshold system:
- Knowledge gap (agent doesn't know): threshold = 1 (immediate transfer)
- User requested transfer + fail: threshold = 1 (immediate transfer)
- Normal technical failures: threshold = 3

Usage:
    This is used by TrackedFlowManager to automatically track failures.
    No manual calls needed in handlers.
"""

from typing import Dict, Any, List
from loguru import logger


class FailureTracker:
    """Centralized failure tracking for voice agent."""

    # Phrases indicating knowledge gap (immediate transfer)
    KNOWLEDGE_GAP_PHRASES = [
        "non so",
        "non posso aiutarti",
        "non ho informazioni",
        "non sono in grado",
        "non conosco",
        "non dispongo",
        "non ho trovato",
        "information not found",
        "i don't know",
        "cannot help",
    ]

    # Error types that should NOT count as failures (user can fix)
    IGNORABLE_ERRORS = [
        "invalid email",
        "invalid phone",
        "formato non valido",
        "please provide",
        "per favore fornisci",
    ]

    @staticmethod
    def initialize(state: Dict[str, Any]) -> None:
        """Initialize failure tracking in flow state."""
        state["failure_tracker"] = {
            "failure_count": 0,
            "transfer_requested": False,
            "in_transfer_attempt": False,
            "knowledge_gap_detected": False,
            "failure_history": [],
        }
        logger.debug("🔧 Failure tracker initialized")

    @staticmethod
    def is_knowledge_gap(result: Dict[str, Any]) -> bool:
        """Detect if this is a knowledge gap requiring immediate transfer."""
        # Knowledge base returned nothing/low confidence
        if result.get("confidence") == 0:
            return True
        if result.get("answer") is None and "query" in result:
            return True

        # Check error/message for knowledge gap phrases
        message = str(result.get("message", "")).lower()
        error = str(result.get("error", "")).lower()
        combined = message + " " + error

        return any(phrase in combined for phrase in FailureTracker.KNOWLEDGE_GAP_PHRASES)

    @staticmethod
    def is_ignorable_error(result: Dict[str, Any]) -> bool:
        """Check if this error should be ignored (user can fix it)."""
        message = str(result.get("message", "")).lower()
        error = str(result.get("error", "")).lower()
        combined = message + " " + error

        return any(phrase in combined for phrase in FailureTracker.IGNORABLE_ERRORS)

    @staticmethod
    def record_failure(
        state: Dict[str, Any],
        reason: str,
        handler_name: str
    ) -> bool:
        """
        Record a failure and determine if transfer should happen.

        Args:
            state: flow_manager.state dictionary
            reason: Failure reason/message
            handler_name: Name of the handler that failed

        Returns:
            True if should transfer to human operator
        """
        tracker = state.get("failure_tracker", {})

        # Ensure tracker exists
        if not tracker:
            FailureTracker.initialize(state)
            tracker = state["failure_tracker"]

        # Increment failure count
        tracker["failure_count"] += 1
        tracker["failure_history"].append({
            "handler": handler_name,
            "reason": reason,
            "count": tracker["failure_count"],
        })

        logger.warning(
            f"🔴 Failure #{tracker['failure_count']}: {handler_name} - {reason[:100]}"
        )

        # Determine threshold based on context
        if tracker.get("knowledge_gap_detected"):
            threshold = 1
            logger.info("📚 Knowledge gap detected - threshold = 1")
        elif tracker.get("transfer_requested") or tracker.get("in_transfer_attempt"):
            threshold = 1
            logger.info("📞 Transfer was requested - threshold = 1")
        else:
            threshold = 3
            logger.info(f"⚙️ Normal failure - threshold = 3 (current: {tracker['failure_count']})")

        should_transfer = tracker["failure_count"] >= threshold

        if should_transfer:
            logger.warning(f"🚨 Failure threshold ({threshold}) reached - will transfer to operator")

        return should_transfer

    @staticmethod
    def mark_transfer_requested(state: Dict[str, Any]) -> None:
        """Mark that user explicitly requested transfer."""
        tracker = state.get("failure_tracker", {})
        if not tracker:
            FailureTracker.initialize(state)
            tracker = state["failure_tracker"]

        tracker["transfer_requested"] = True
        tracker["in_transfer_attempt"] = True
        tracker["failure_count"] = 0  # Reset for this attempt
        logger.info("📞 Transfer requested by user - next failure will trigger transfer")

    @staticmethod
    def mark_knowledge_gap(state: Dict[str, Any]) -> None:
        """Mark that a knowledge gap was detected."""
        tracker = state.get("failure_tracker", {})
        if tracker:
            tracker["knowledge_gap_detected"] = True
            logger.info("📚 Knowledge gap marked - will transfer on failure")

    @staticmethod
    def reset(state: Dict[str, Any]) -> None:
        """Reset failure counter after successful action."""
        tracker = state.get("failure_tracker", {})
        if tracker:
            old_count = tracker.get("failure_count", 0)
            tracker["failure_count"] = 0
            tracker["transfer_requested"] = False
            tracker["in_transfer_attempt"] = False
            tracker["knowledge_gap_detected"] = False
            if old_count > 0:
                logger.info(f"✅ Failure tracker reset (was {old_count})")

    @staticmethod
    def get_failure_stats(state: Dict[str, Any]) -> Dict[str, Any]:
        """Get failure statistics for logging/analytics."""
        tracker = state.get("failure_tracker", {})
        return {
            "total_failures": tracker.get("failure_count", 0),
            "transfer_requested": tracker.get("transfer_requested", False),
            "knowledge_gap": tracker.get("knowledge_gap_detected", False),
            "history": tracker.get("failure_history", []),
        }
