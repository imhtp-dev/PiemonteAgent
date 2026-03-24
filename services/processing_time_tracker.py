"""
Processing Time Tracker for Healthcare Booking Agent
Monitors response time and injects "processing" message if agent takes too long to respond.

Key behavior:
- Timer starts on UserStoppedSpeakingFrame (fast reaction)
- Only injects "Attendi..." if a function call is active (real work happening)
- If no LLM activity after threshold (noise/no transcript/no function call), stays silent
- Stops timer when actual response arrives (LLMTextFrame or TTSSpeakFrame)
- function_call_active flag persists across timer restarts (survives interruptions during API calls)
"""

import asyncio
import os
import time
from loguru import logger
from typing import Optional
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import (
    Frame,
    TTSSpeakFrame,
    UserStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    FunctionCallsStartedFrame,
    FunctionCallResultFrame,
)
from config.settings import settings


class ProcessingTimeTracker(FrameProcessor):
    """
    Tracks processing time from when user stops speaking to when TTS response starts.
    If processing takes longer than threshold AND a function call is active,
    injects a "please wait" message. Stays silent if no real work is happening.
    """

    def __init__(self, threshold_seconds: float = 3.0):
        super().__init__()
        self._threshold = threshold_seconds
        self._processing_start_time: Optional[float] = None
        self._warning_spoken = False
        self._timer_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._waiting_for_real_response = False
        self._bot_is_responding = False
        # Persists across timer restarts — only cleared when response cycle completes
        self._function_call_active = False

        logger.debug(f"ProcessingTimeTracker initialized: {threshold_seconds}s threshold")

    async def process_frame(self, frame: Frame, direction):
        await super().process_frame(frame, direction)

        # User stopped speaking — START TIMER
        if isinstance(frame, UserStoppedSpeakingFrame):
            if not self._bot_is_responding:
                await self._start_timer()

        # User started speaking again — cancel timer task but preserve function_call_active
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._cancel_timer()

        # LLM called a function — mark real work happening, keep timer running
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._function_call_active = True
            logger.debug("⏳ Function call started — timer continues, will inject message if slow")

        # LLM started generating TEXT (actual response arriving) — stop timer, clear all
        elif isinstance(frame, LLMTextFrame):
            if not self._bot_is_responding:
                self._bot_is_responding = True
                await self._stop_timer()

        # TTS is about to speak — stop timer (response is being spoken)
        elif isinstance(frame, TTSSpeakFrame):
            if self._waiting_for_real_response:
                # Bot's actual response following our "please wait" message
                self._bot_is_responding = True
                await self._stop_timer()
            elif not self._warning_spoken:
                # Bot's response before we needed to inject anything
                self._bot_is_responding = True
                await self._stop_timer()
            # else: This is our own injected "Attendi..." message — don't mark as final response

            # Cancel timer after setting flag (redundant but safe)
            if self._timer_task and not self._timer_task.done():
                try:
                    self._timer_task.cancel()
                except Exception as e:
                    logger.error(f"❌ Error cancelling timer: {e}")

        await self.push_frame(frame, direction)

    async def _start_timer(self):
        """Start monitoring processing time. Preserves function_call_active flag."""
        async with self._lock:
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            self._processing_start_time = time.time()
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False
            # NOTE: Do NOT reset _function_call_active here.
            # A function call may still be running when timer restarts
            # (e.g., STT transcription arrives mid-API-call, causing
            # UserStartedSpeaking → cancel → UserStoppedSpeaking → new timer)
            self._timer_task = asyncio.create_task(self._check_processing_time())

    async def _stop_timer(self):
        """Stop monitoring — response arrived. Clears ALL state including function_call_active."""
        async with self._lock:
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            # Full reset — response cycle complete
            self._processing_start_time = None
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False
            self._function_call_active = False
            self._timer_task = None

    async def _cancel_timer(self):
        """Cancel timer (user interrupted). Preserves function_call_active flag."""
        async with self._lock:
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            self._processing_start_time = None
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False
            # NOTE: Do NOT reset _function_call_active here.
            # If an API call is in progress and STT fires a late transcription
            # causing UserStartedSpeaking, we still want the next timer to know
            # a function call is active.
            self._timer_task = None

    async def _check_processing_time(self):
        """
        Background task that checks elapsed time every 0.5 seconds.
        Only injects message if a function call is active (real work happening).
        """
        try:
            while True:
                await asyncio.sleep(0.5)

                if self._bot_is_responding:
                    break

                if self._processing_start_time and not self._warning_spoken:
                    elapsed = time.time() - self._processing_start_time

                    if elapsed > self._threshold:
                        if self._function_call_active:
                            await self._inject_processing_message()
                        else:
                            logger.debug(
                                f"⏭️ Threshold exceeded ({elapsed:.1f}s) but no function call active — skipping injection"
                            )
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Error in processing time checker: {e}")

    async def _inject_processing_message(self):
        """Inject 'please wait' message into TTS pipeline"""
        async with self._lock:
            if self._warning_spoken:
                return

            if not self._timer_task or self._timer_task.cancelled():
                return

            # Safety: abort if bot started responding while we waited for lock
            if self._bot_is_responding:
                return

            elapsed = time.time() - self._processing_start_time if self._processing_start_time else 0
            logger.info(f"🔔 Function call taking {elapsed:.1f}s — injecting processing message")

            language_instruction = settings.language_config
            message = "Attendi qualche secondo che sto cercando" if "Italian" in language_instruction else "Please wait a few seconds while I search"

            self._warning_spoken = True
            self._waiting_for_real_response = True

            await self.push_frame(TTSSpeakFrame(message))

            logger.success(f"✅ Processing message injected: '{message}'")

    async def cleanup(self):
        """Cleanup when processor is destroyed"""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass

        await super().cleanup()


def create_processing_time_tracker(threshold_seconds: float = None) -> ProcessingTimeTracker:
    """
    Create a ProcessingTimeTracker for healthcare booking agent

    Args:
        threshold_seconds: Seconds to wait before injecting message (default: read from PROCESSING_TIME_THRESHOLD env, fallback 4.0)

    Returns:
        ProcessingTimeTracker: Configured processor
    """
    if threshold_seconds is None:
        threshold_seconds = float(os.getenv("PROCESSING_TIME_THRESHOLD", "4.0"))

    logger.debug(f"Creating ProcessingTimeTracker: {threshold_seconds}s threshold")

    return ProcessingTimeTracker(threshold_seconds=threshold_seconds)
