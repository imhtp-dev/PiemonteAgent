"""
Node-Aware User Mute Strategy

Mutes user input on processing/transition nodes for their entire lifetime.
On regular conversation nodes, user can still interrupt normally.

Also mutes during the bot's FIRST utterance after transitioning from a
processing node to a conversation node. This prevents VAD false positives
caused by TTS echo/ambient noise right after the transition. Once the bot
finishes its first utterance, the user is unmuted and can interrupt normally.

Processing nodes have a tts_say → API call pattern where a 10-20ms gap between
TTS queue and BotStartedSpeakingFrame allows VAD to fire, causing the LLM
to pick wrong global functions (e.g. start_booking during flow_processing).

Usage:
    strategy = NodeAwareMuteStrategy()
    # After flow_manager is created, link the state:
    strategy.set_flow_state(flow_manager.state)
"""

from loguru import logger
from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

# Processing nodes — always mute, no user input allowed for entire node lifetime.
# Closes the timing gap between tts_say queue and BotStartedSpeakingFrame.
PROCESSING_NODES = {
    "search_processing",                   # Health service search
    "slot_search_processing",              # Slot search API
    "slot_booking_processing",             # Slot booking API
    "silent_center_search_processing",     # Silent center search + flow generation
    "center_search_processing",            # Center search API
    "automatic_slot_search",               # Auto slot search for 2nd+ services
    "booking_processing",                  # Final booking creation API
    "final_center_search",                 # Search centers
    "slot_search",                         # Search slots
    "slot_refresh",                        # Refresh slots (respond_immediately)
}


class NodeAwareMuteStrategy(BaseUserMuteStrategy):
    """Mutes user on processing/transition nodes for entire node lifetime.

    Also mutes during the bot's first utterance after leaving a processing node.
    This prevents VAD false positives from TTS echo when transitioning
    processing → conversation node.

    Combined with FunctionCallUserMuteStrategy via OR logic:
    - FunctionCallUserMuteStrategy covers function execution time
    - NodeAwareMuteStrategy covers tts_say pre-action time on processing nodes
    """

    def __init__(self):
        super().__init__()
        self._flow_state: dict | None = None
        self._bot_speaking = False
        self._last_node: str = ""
        # True when we just left a processing node and bot hasn't finished first utterance yet
        self._post_processing_guard = False

    def set_flow_state(self, state: dict):
        """Link to flow_manager.state dict. Must be called after flow_manager creation."""
        self._flow_state = state
        logger.debug("NodeAwareMuteStrategy linked to flow state")

    def _get_current_node(self) -> str:
        if not self._flow_state:
            return ""
        return self._flow_state.get("current_node", "")

    async def process_frame(self, frame: Frame) -> bool:
        """Return True to mute user, False to allow."""
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # Bot finished speaking — lift the post-processing guard
            if self._post_processing_guard:
                self._post_processing_guard = False
                logger.debug("🔊 Post-processing guard lifted (bot finished first utterance)")

        current_node = self._get_current_node()

        # Detect node transitions
        if current_node != self._last_node:
            prev = self._last_node
            self._last_node = current_node

            # If we just left a processing node → activate guard on the new conversation node
            if prev in PROCESSING_NODES and current_node not in PROCESSING_NODES:
                self._post_processing_guard = True
                logger.debug(f"🔇 Post-processing guard ON: {prev} → {current_node}")

        # Always mute on processing nodes — no timing gap possible
        if current_node in PROCESSING_NODES:
            return True

        # Mute during bot's first utterance after leaving a processing node
        # Prevents VAD false positives from TTS echo/ambient noise
        if self._post_processing_guard:
            return True

        return False
