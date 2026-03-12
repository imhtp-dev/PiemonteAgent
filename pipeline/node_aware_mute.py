"""
Node-Aware User Mute Strategy

Mutes user input on processing/transition nodes for their entire lifetime.
On regular conversation nodes, user can still interrupt normally.

Mutes when a node transition is pending (handler returned a next node but the
transition hasn't executed yet). This closes the race condition where user
interrupts between handler return and transition execution, causing the LLM
to re-call the same function with stale context.

After any transition completes, applies a short grace period (500ms) to discard
stale STT transcriptions that were captured during the muted period but delivered
after unmute due to STT pipeline latency.

Processing nodes have a tts_say → API call pattern where a 10-20ms gap between
TTS queue and BotStartedSpeakingFrame allows VAD to fire, causing the LLM
to pick wrong global functions (e.g. start_booking during flow_processing).

Usage:
    strategy = NodeAwareMuteStrategy()
    # After flow_manager is created, link the state AND flow_manager:
    strategy.set_flow_state(flow_manager.state)
    strategy.set_flow_manager(flow_manager)
"""

import time

from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

# Grace period (seconds) after pending transition completes.
# Discards stale STT from muted period that arrives after unmute.
POST_TRANSITION_GRACE_SECS = 0.5

# Processing nodes — always mute, no user input allowed for entire node lifetime.
# Closes the timing gap between tts_say queue and BotStartedSpeakingFrame.
PROCESSING_NODES = {
    "silent_center_search_processing",     # Silent center search + flow generation
    "final_center_search",                 # Search centers
    "slot_search",                         # Search slots
    "slot_refresh",                        # Refresh slots (respond_immediately)
}


class NodeAwareMuteStrategy(BaseUserMuteStrategy):
    """Mutes user on processing/transition nodes for entire node lifetime.

    After any function-triggered transition completes, keeps user muted for
    a short grace period (500ms) to discard stale STT transcriptions from
    the muted period. This replaces the old post-processing guard that
    blocked interruption until bot finished its entire first utterance.

    Combined with FunctionCallUserMuteStrategy via OR logic:
    - FunctionCallUserMuteStrategy covers function execution time
    - NodeAwareMuteStrategy covers processing nodes + pending transitions + grace period
    """

    def __init__(self):
        super().__init__()
        self._flow_state: dict | None = None
        self._flow_manager = None
        self._last_node: str = ""
        # Track pending transition guard state to log only on activation/deactivation
        self._pending_transition_active = False
        # Timestamp when grace period expires (0 = no active grace period)
        self._grace_until: float = 0

    def set_flow_state(self, state: dict):
        """Link to flow_manager.state dict. Must be called after flow_manager creation."""
        self._flow_state = state
        logger.debug("NodeAwareMuteStrategy linked to flow state")

    def set_flow_manager(self, flow_manager):
        """Link to flow_manager instance to check _pending_transition.
        Must be called after flow_manager creation."""
        self._flow_manager = flow_manager
        logger.debug("NodeAwareMuteStrategy linked to flow manager (pending transition guard)")

    def _get_current_node(self) -> str:
        if not self._flow_state:
            return ""
        return self._flow_state.get("current_node", "")

    def _has_pending_transition(self) -> bool:
        """Check if FlowManager has a pending node transition waiting to execute."""
        if not self._flow_manager:
            return False
        return getattr(self._flow_manager, "_pending_transition", None) is not None

    async def process_frame(self, frame: Frame) -> bool:
        """Return True to mute user, False to allow."""
        current_node = self._get_current_node()

        # Detect node transitions (for logging only now)
        if current_node != self._last_node:
            self._last_node = current_node

        # Always mute on processing nodes — no timing gap possible
        if current_node in PROCESSING_NODES:
            return True

        # Mute while a node transition is pending (handler returned next node
        # but transition hasn't executed yet). Prevents interruption race condition
        # that causes duplicate function calls and stuck conversations.
        pending = self._has_pending_transition()
        if pending and not self._pending_transition_active:
            self._pending_transition_active = True
            logger.info(f"🔇 PENDING TRANSITION GUARD ON: muting user on node '{current_node}' — transition waiting to execute")
        elif not pending and self._pending_transition_active:
            self._pending_transition_active = False
            # Start grace period to discard stale STT from muted period
            self._grace_until = time.monotonic() + POST_TRANSITION_GRACE_SECS
            logger.info(f"🔇 PENDING TRANSITION GUARD OFF → grace period ({POST_TRANSITION_GRACE_SECS}s) on '{current_node}'")
        if pending:
            return True

        # Grace period after transition — discard stale STT transcriptions
        # that were captured during mute but delivered after unmute
        if self._grace_until:
            if time.monotonic() < self._grace_until:
                return True
            else:
                logger.info(f"🔊 Grace period expired, user unmuted on '{current_node}'")
                self._grace_until = 0

        return False
