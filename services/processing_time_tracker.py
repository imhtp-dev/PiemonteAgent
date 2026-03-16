"""
Processing Time Tracker for Healthcare Booking Agent
Monitors response time and injects "processing" message if agent takes too long to respond
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
)
from config.settings import settings


class ProcessingTimeTracker(FrameProcessor):
    """
    Tracks processing time from when user stops speaking to when TTS response starts.
    If processing takes longer than threshold, injects a "please wait" message.
    """

    def __init__(self, threshold_seconds: float = 3.0):
        """
        Initialize the processing time tracker

        Args:
            threshold_seconds: Seconds to wait before injecting processing message (default: 3.0)
        """
        super().__init__()
        self._threshold = threshold_seconds
        self._processing_start_time: Optional[float] = None
        self._warning_spoken = False
        self._timer_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._waiting_for_real_response = False  # Track if we're waiting for bot's real response
        self._bot_is_responding = False  # Track if bot is currently generating/speaking a response
        self._watchdog_task: Optional[asyncio.Task] = None  # Watchdog to force-reset after injection

        logger.debug(f"ProcessingTimeTracker initialized: {threshold_seconds}s threshold")

    async def process_frame(self, frame: Frame, direction):
        """
        Process frames flowing through the pipeline and monitor timing
        """
        await super().process_frame(frame, direction)

        # User stopped speaking - START TIMER IMMEDIATELY
        # We're now AFTER LLM so we can't see TranscriptionFrame anymore
        # We start timer here and LLMFullResponseStartFrame will stop it if LLM responds quickly
        if isinstance(frame, UserStoppedSpeakingFrame):
            # Always start fresh timer on new user turn — _start_timer() resets all flags
            # including _bot_is_responding, so stale state from a previous turn can't block us
            await self._start_timer()

        # User started speaking again - cancel monitoring (user interrupted)
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._cancel_timer()

        # LLM called a function — cancel timer. The handler will process and
        # return a node transition; injecting TTS during that transition breaks
        # pipecat-flows' node handoff (the new node's LLM prompt never fires).
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._bot_is_responding = True
            await self._stop_timer()

        # LLM started generating TEXT (actual response, not just function calls)
        # This frame flows downstream (LLM → TTS) and our processor NOW sees it!
        elif isinstance(frame, LLMTextFrame):
            if not self._warning_spoken and not self._bot_is_responding:
                # LLM is generating actual text response - bot is responding!
                self._bot_is_responding = True  # Set flag IMMEDIATELY (no await, no lock)
                await self._stop_timer()  # Then stop timer
            # else: We already stopped timer or injected "Attendi..." message

        # TTS is about to speak - cancel timer immediately (Problem 2 fix)
        elif isinstance(frame, TTSSpeakFrame):
            # CRITICAL FIX: Set flag IMMEDIATELY, BEFORE any conditions or async operations
            # This prevents race condition where timer loop acquires lock before we can set the flag
            if self._waiting_for_real_response:
                # This is the bot's actual response following our "please wait" message
                self._bot_is_responding = True  # Set flag FIRST (no await, no lock)
                await self._stop_timer()  # Then do lock operations
            elif not self._warning_spoken:
                # This is the bot's response and we haven't injected a message yet
                self._bot_is_responding = True  # Set flag FIRST (no await, no lock)
                await self._stop_timer()  # Then do lock operations
            # else: This is our own injected "Attendi..." message - DON'T mark bot as responding yet

            # Cancel timer after setting flag (redundant but safe)
            if self._timer_task and not self._timer_task.done():
                try:
                    self._timer_task.cancel()
                except Exception as e:
                    logger.error(f"❌ Error cancelling timer: {e}")

        # Pass frame through to next processor
        await self.push_frame(frame, direction)

    async def _start_timer(self):
        """Start monitoring processing time when user stops speaking"""
        async with self._lock:
            # Cancel any existing timer
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            # Cancel watchdog from previous turn
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
                self._watchdog_task = None

            # Reset state
            self._processing_start_time = time.time()
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False  # New query, bot hasn't responded yet

            # Start background timer task
            self._timer_task = asyncio.create_task(self._check_processing_time())

    async def _stop_timer(self):
        """Stop monitoring when TTS starts (response is ready)"""
        async with self._lock:
            if self._processing_start_time:
                elapsed = time.time() - self._processing_start_time
                pass  # Processing done

            # Cancel timer task
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            # Cancel watchdog
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
                self._watchdog_task = None

            # Reset state
            self._processing_start_time = None
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False  # Response complete, ready for new query
            self._timer_task = None

    async def _cancel_timer(self):
        """Cancel monitoring when user interrupts"""
        async with self._lock:
            pass  # Cancelled by user

            # Cancel timer task
            if self._timer_task and not self._timer_task.done():
                self._timer_task.cancel()
                try:
                    await self._timer_task
                except asyncio.CancelledError:
                    pass

            # Cancel watchdog
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
                self._watchdog_task = None

            # Reset state
            self._processing_start_time = None
            self._warning_spoken = False
            self._waiting_for_real_response = False
            self._bot_is_responding = False  # Cancelled, ready for new query
            self._timer_task = None

    async def _check_processing_time(self):
        """
        Background task that checks elapsed time every 0.5 seconds.
        Injects processing message if threshold is exceeded.
        """
        try:
            while True:
                await asyncio.sleep(0.5)  # Check twice per second

                # Problem 2 fix: Check if bot is responding BEFORE checking threshold
                # This prevents race condition where bot starts speaking but timer hasn't been cancelled yet
                if self._bot_is_responding:
                    pass  # Bot responding, stop
                    break

                # Check if we should inject warning message
                if self._processing_start_time and not self._warning_spoken:
                    elapsed = time.time() - self._processing_start_time

                    if elapsed > self._threshold:
                        await self._inject_processing_message()
                        break  # Exit loop after speaking once

        except asyncio.CancelledError:
            # Timer was cancelled (normal flow)
            pass
        except Exception as e:
            logger.error(f"❌ Error in processing time checker: {e}")

    async def _inject_processing_message(self):
        """Inject 'please wait' message into TTS pipeline"""
        async with self._lock:
            if self._warning_spoken:
                return  # Already spoken, don't repeat

            # Problem 2 fix: Safety check - abort if timer was cancelled (race condition)
            if not self._timer_task or self._timer_task.cancelled():
                pass  # Timer cancelled before injection
                return

            # CRITICAL FIX: Check if bot is responding AFTER acquiring lock
            # This handles race condition where TTSSpeakFrame set flag while we were waiting for lock
            if self._bot_is_responding:
                pass  # Bot responding, skip injection
                return

            elapsed = time.time() - self._processing_start_time if self._processing_start_time else 0
            logger.info(f"🔔 Processing exceeded {self._threshold}s threshold (elapsed: {elapsed:.2f}s) - injecting message")

            # Get language-specific message
            language_instruction = settings.language_config
            message = "Attendi qualche secondo che sto cercando" if "Italian" in language_instruction else "Please wait a few seconds while I search"

            # Mark as spoken BEFORE injecting to prevent race conditions
            self._warning_spoken = True
            # Now we're waiting for the bot's real response (next TTSSpeakFrame will be the real one)
            self._waiting_for_real_response = True
            # Bot IS responding (processing in background even while we speak "Attendi...")
            self._bot_is_responding = True

            # Inject TTS message into pipeline
            await self.push_frame(TTSSpeakFrame(message))

            logger.success(f"✅ Processing message injected: '{message}', waiting for bot's real response")

            # Start 15s watchdog — if no real LLM response resets us, force-reset state
            # Prevents permanent death where _bot_is_responding=True never clears
            self._watchdog_task = asyncio.create_task(self._watchdog_reset())

    async def _watchdog_reset(self):
        """Force-reset all state after 15s if no real LLM response clears _bot_is_responding."""
        try:
            await asyncio.sleep(15)
            if self._bot_is_responding:
                logger.warning("⚠️ ProcessingTimeTracker watchdog: 15s elapsed with no reset — force-clearing state")
                self._processing_start_time = None
                self._warning_spoken = False
                self._waiting_for_real_response = False
                self._bot_is_responding = False
                self._timer_task = None
        except asyncio.CancelledError:
            pass

    async def cleanup(self):
        """Cleanup when processor is destroyed"""
        # Cancel any running timer
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass

        # Cancel watchdog
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
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
    # Read from environment variable if not explicitly provided
    if threshold_seconds is None:
        threshold_seconds = float(os.getenv("PROCESSING_TIME_THRESHOLD", "4.0"))

    logger.debug(f"Creating ProcessingTimeTracker: {threshold_seconds}s threshold")

    return ProcessingTimeTracker(threshold_seconds=threshold_seconds)
