"""
User Idle Handler for Healthcare Booking Agent
Uses pipecat's built-in user_idle_timeout on LLMUserAggregator.

Setup:
    1. Pass user_idle_timeout to LLMUserAggregatorParams
    2. Register on_user_turn_idle event on context_aggregator.user()
    3. Register on_user_turn_started to reset retry count
"""

from loguru import logger
from pipecat.frames.frames import (
    TTSSpeakFrame,
    EndTaskFrame,
    LLMMessagesAppendFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from config.settings import settings

# Default idle timeout in seconds
DEFAULT_IDLE_TIMEOUT = 15.0


class IdleHandler:
    """Escalating idle handler matching pipecat's recommended pattern."""

    def __init__(self):
        self._retry_count = 0

    def reset(self):
        self._retry_count = 0

    async def handle_idle(self, aggregator):
        self._retry_count += 1
        is_italian = "Italian" in settings.language_config

        if self._retry_count == 1:
            content = (
                "L'utente non ha risposto. Chiedi brevemente e gentilmente se è ancora lì."
                if is_italian
                else "The user has been quiet. Politely and briefly ask if they're still there."
            )
            message = {"role": "system", "content": content}
            await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))

        elif self._retry_count == 2:
            content = (
                "L'utente è ancora inattivo. Chiedi se vuole continuare la conversazione."
                if is_italian
                else "The user is still inactive. Ask if they'd like to continue."
            )
            message = {"role": "system", "content": content}
            await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))

        else:
            goodbye = (
                "Sembra che tu sia occupato. Buona giornata, arrivederci!"
                if is_italian
                else "It seems like you're busy right now. Have a nice day!"
            )
            await aggregator.push_frame(TTSSpeakFrame(goodbye))
            await aggregator.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
