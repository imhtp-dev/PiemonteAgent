"""
Node-Aware User Mute Strategy

Mutes user input during bot speech ONLY on processing/transition nodes.
On regular conversation nodes, user can still interrupt normally.

Processing nodes are non-conversational — they speak a "please wait" message
(tts_say pre-action) then execute an API call. User input during these nodes
causes LLM to pick wrong global functions or generate text without calling
any function, resulting in the bot looping/getting stuck.

Usage:
    strategy = NodeAwareMuteStrategy()
    # After flow_manager is created, link the state:
    strategy.set_flow_state(flow_manager.state)
"""

from loguru import logger
from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

# Nodes where user interruption should be suppressed during bot speech.
# These are processing/transition nodes that:
# 1. Speak a "please wait" TTS message (tts_say pre-action)
# 2. Then auto-call an API function
# User input during these causes LLM misfires.
PROCESSING_NODES = {
    # tts_say pre-action + immediate API call
    "search_processing",           # Health service search
    "flow_processing",             # Flow/decision tree generation
    "center_search_processing",    # Center search API
    "slot_search_processing",      # Slot search API
    "automatic_slot_search",       # Auto slot search for 2nd+ services
    "slot_booking_processing",     # Slot booking API
    "booking_processing",          # Final booking creation API
    # Auto-call pattern (LLM told to call immediately, no user interaction)
    "orange_box_flow_generation",  # Generate decision flow
    "final_center_search",         # Search centers
    "slot_search",                 # Search slots
    "slot_refresh",                # Refresh slots (respond_immediately)
}


class NodeAwareMuteStrategy(BaseUserMuteStrategy):
    """Mutes user during bot speech on processing/transition nodes only.

    On processing nodes: behaves like AlwaysUserMuteStrategy (mute during bot speech)
    On regular nodes: does nothing (allows normal interruptions)

    Combined with FunctionCallUserMuteStrategy via OR logic:
    - FunctionCallUserMuteStrategy covers function execution time
    - NodeAwareMuteStrategy covers tts_say pre-action time on processing nodes
    """

    def __init__(self):
        super().__init__()
        self._flow_state: dict | None = None
        self._bot_speaking = False

    def set_flow_state(self, state: dict):
        """Link to flow_manager.state dict. Must be called after flow_manager creation."""
        self._flow_state = state
        logger.debug("NodeAwareMuteStrategy linked to flow state")

    def _is_processing_node(self) -> bool:
        """Check if current node is a processing/transition node."""
        if not self._flow_state:
            return False
        current_node = self._flow_state.get("current_node", "")
        return current_node in PROCESSING_NODES

    async def process_frame(self, frame: Frame) -> bool:
        """Return True to mute user, False to allow.

        Tracks bot speaking state via BotStarted/StoppedSpeakingFrame.
        Only mutes when bot is speaking AND we're on a processing node.
        """
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        if self._bot_speaking and self._is_processing_node():
            return True

        return False
