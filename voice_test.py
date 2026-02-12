"""
Daily Transport Testing for Healthcare Flow Agent
================================================

This script creates a Daily room and connects your existing Pipecat flows agent
for local testing before pushing to production.

Usage:
    python test.py                              # Start with greeting (full flow)
    python test.py --caller-phone +393333319326 --patient-dob 1979-06-19
    python test.py --start-node email           # Start with email collection
    python test.py --start-node booking         # Start with booking flow
    python test.py --room-url <url> --token <token>  # Use existing room

Author: Healthcare Flow Bot Testing
"""

import os
import sys
import asyncio
import argparse
import time
import aiohttp
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from loguru import logger


# ============================================================================
# LATENCY TRACKER - For comparing with Gemini Live
# ============================================================================

class LatencyTracker:
    """Track latency metrics for comparison with voice_test2.py (Gemini Live)."""

    def __init__(self):
        self.ttfb_values: list[float] = []
        self.function_calls: list[dict] = []
        self.session_start = time.time()

    def add_ttfb(self, ttfb_seconds: float):
        """Add a TTFB value (in seconds)"""
        ttfb_ms = ttfb_seconds * 1000
        self.ttfb_values.append(ttfb_ms)
        logger.success(f"📊 TTFB #{len(self.ttfb_values)}: {ttfb_ms:.0f}ms")

    def get_stats(self) -> Dict[str, Any]:
        """Get latency statistics"""
        if not self.ttfb_values:
            return {"count": 0, "avg": 0, "min": 0, "max": 0, "session_duration": 0}

        return {
            "count": len(self.ttfb_values),
            "avg": sum(self.ttfb_values) / len(self.ttfb_values),
            "min": min(self.ttfb_values),
            "max": max(self.ttfb_values),
            "session_duration": time.time() - self.session_start,
            "function_calls": len(self.function_calls)
        }

    def print_summary(self):
        """Print final summary for comparison with Gemini Live"""
        stats = self.get_stats()
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("📊 LATENCY SUMMARY (Deepgram + OpenAI + ElevenLabs)")
        logger.info(f"   Session Duration: {stats['session_duration']:.1f}s")
        logger.info(f"   Total Responses: {stats['count']}")
        if stats['count'] > 0:
            logger.info(f"   Avg TTFB: {stats['avg']:.0f}ms")
            logger.info(f"   Min TTFB: {stats['min']:.0f}ms")
            logger.info(f"   Max TTFB: {stats['max']:.0f}ms")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# Core Pipecat imports
from pipecat.frames.frames import (
    TranscriptionFrame,
    InterimTranscriptionFrame,
    Frame,
    TTSSpeakFrame,
    LLMMessagesFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame
)
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams
from pipecat.processors.frame_processor import FrameDirection


# ============================================================================
# DEBUG AUDIO BUFFER PROCESSOR - Logs frame reception for debugging
# ============================================================================

class DebugAudioBufferProcessor:
    """Wrapper around AudioBufferProcessor that logs frame reception for debugging."""

    def __init__(self, processor):
        self._processor = processor
        self._input_frame_count = 0
        self._output_frame_count = 0
        # Store original process_frame
        self._original_process_frame = processor.process_frame

        # Monkey-patch process_frame to add logging
        async def debug_process_frame(frame: Frame, direction: FrameDirection):
            if isinstance(frame, InputAudioRawFrame):
                self._input_frame_count += 1
                if self._input_frame_count <= 5 or self._input_frame_count % 100 == 0:
                    logger.debug(f"🎙️ [DEBUG] AudioBuffer received InputAudioRawFrame #{self._input_frame_count}: {len(frame.audio)} bytes")
            elif isinstance(frame, OutputAudioRawFrame):
                self._output_frame_count += 1
                if self._output_frame_count <= 5 or self._output_frame_count % 100 == 0:
                    logger.debug(f"🔊 [DEBUG] AudioBuffer received OutputAudioRawFrame #{self._output_frame_count}: {len(frame.audio)} bytes")
            return await self._original_process_frame(frame, direction)

        processor.process_frame = debug_process_frame

    def log_buffer_status(self):
        """Log current buffer sizes"""
        user_size = len(self._processor._user_audio_buffer)
        bot_size = len(self._processor._bot_audio_buffer)
        logger.info(f"🎙️ [DEBUG] Buffer status - User: {user_size} bytes, Bot: {bot_size} bytes")
        logger.info(f"🎙️ [DEBUG] Frame counts - Input: {self._input_frame_count}, Output: {self._output_frame_count}")

    @property
    def processor(self):
        return self._processor

# Daily transport imports
from pipecat.transports.daily.transport import DailyParams, DailyTransport

# OpenTelemetry & Phoenix tracing
from config.telemetry import setup_tracing, get_tracer, get_conversation_usage, flush_traces, update_trace_metadata
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import your existing components and flows
from config.settings import settings
from services.config import config
from pipeline.components import create_stt_service, create_tts_service, create_llm_service, create_context_aggregator
from flows.manager import create_flow_manager, initialize_flow_manager
from services.transcript_manager import get_transcript_manager, cleanup_transcript_manager
from utils.stt_switcher import initialize_stt_switcher

# Load environment variables
load_dotenv(override=True)


class DailyTestConfig:
    """Configuration for Daily testing (separate from production settings)"""

    def __init__(self):
        self.daily_api_key = os.getenv("DAILY_API_KEY")
        self.daily_api_url = os.getenv("DAILY_API_URL", "https://api.daily.co/v1")

        if not self.daily_api_key:
            raise Exception("DAILY_API_KEY environment variable is required for testing")

    @property
    def daily_room_config(self) -> Dict[str, Any]:
        """Daily room configuration optimized for testing"""
        return {
            "privacy": "private",
            "properties": {
                "max_participants": 2,  # Bot + 1 tester
                "enable_chat": False,
                "enable_screenshare": False,
                "enable_recording": "local",  # For testing transcripts
                "eject_at_room_exp": True,
                "exp": None,  # Will be set to 2 hours from creation
            }
        }

    @property
    def daily_transport_params(self) -> Dict[str, Any]:
        """Daily transport parameters for testing"""
        return {
            "audio_in_enabled": True,
            "audio_out_enabled": True,
            "transcription_enabled": False,
            "audio_in_sample_rate": 16000,
            "audio_out_sample_rate": 16000,
            "camera_enabled": False,
            "mic_enabled": True,
            "dial_in_timeout": 30,
            "connection_timeout": 30,
            "vad_analyzer": SileroVADAnalyzer(
                params=VADParams(
                    start_secs=0.1,    # Faster detection for testing
                    stop_secs=0.3,     # Quicker stop for testing
                    min_volume=0.2     # More sensitive for testing
                )
            )
        }


class DailyHealthcareFlowTester:
    """Daily transport tester for healthcare flow agent"""

    def __init__(self, start_node: str = "router", caller_phone: str = None, patient_dob: str = None):
        self.config = DailyTestConfig()
        self.start_node = start_node
        self.caller_phone = caller_phone  # NEW: Simulate caller phone from Talkdesk
        self.patient_dob = patient_dob    # NEW: Simulate patient date of birth
        # Use hardcoded session ID for Supabase (same as chat_test.py)
        self.session_id = "49b78a42-9024-4646-95e2-d2d6f4f8a17b"
        self.room_url: Optional[str] = None
        self.token: Optional[str] = None
        self.daily_helper: Optional[DailyRESTHelper] = None

        # Runtime components
        self.transport: Optional[DailyTransport] = None
        self.task: Optional[PipelineTask] = None
        self.runner: Optional[PipelineRunner] = None
        self.flow_manager = None
        self.call_logger = None
        self.recording_manager = None  # Audio recording manager (if enabled)
        self.audiobuffer = None  # Audio buffer processor (if recording enabled)
        self.latency_tracker = LatencyTracker()  # For comparison with Gemini Live

        # Session info will be saved to the main log file created above
        logger.info(f"🎯 Starting Daily test session: {self.session_id} with node: {start_node}")

    async def create_daily_room(self) -> tuple[str, str]:
        """Create a new Daily room for testing using Daily API directly"""
        logger.info("🏠 Creating Daily room for testing...")

        import aiohttp
        import time

        # Create room using Daily REST API directly
        room_config = {
            "privacy": "public",  # Changed to public for easier testing
            "properties": {
                "max_participants": 10,  # Increased for flexibility
                "enable_chat": True,
                "enable_screenshare": False,
                "enable_recording": "local",
                "eject_at_room_exp": True,
                "exp": int(time.time()) + 7200,  # 2 hours from now
                "enable_knocking": False,  # Disable knocking
                "enable_prejoin_ui": False,  # Skip prejoin UI
            }
        }

        headers = {
            "Authorization": f"Bearer {self.config.daily_api_key}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Create room
            async with session.post(
                f"{self.config.daily_api_url}/rooms",
                json=room_config,
                headers=headers
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to create Daily room: {response.status} - {error_text}")

                room_data = await response.json()
                room_url = room_data.get("url")

                if not room_url:
                    raise Exception(f"No room URL in response: {room_data}")

            logger.success(f"✅ Daily room created: {room_url}")

            # Generate token for the bot
            token_config = {
                "properties": {
                    "room_name": room_data.get("name"),
                    "is_owner": True,
                    "user_name": "Ualà Healthcare Bot",
                    "enable_screenshare": False,
                    "start_audio_off": False,
                    "start_video_off": True
                }
            }

            async with session.post(
                f"{self.config.daily_api_url}/meeting-tokens",
                json=token_config,
                headers=headers
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to create Daily token: {response.status} - {error_text}")

                token_data = await response.json()
                token = token_data.get("token")

                if not token:
                    raise Exception(f"No token in response: {token_data}")

            logger.success(f"🎟️ Daily token generated for bot")

            # Generate a user token for easier testing
            user_token_config = {
                "properties": {
                    "room_name": room_data.get("name"),
                    "user_name": "Tester",
                    "enable_screenshare": False,
                    "start_audio_off": False,
                    "start_video_off": True
                }
            }

            async with session.post(
                f"{self.config.daily_api_url}/meeting-tokens",
                json=user_token_config,
                headers=headers
            ) as response:
                if response.status == 200:
                    user_token_data = await response.json()
                    user_token = user_token_data.get("token")
                    if user_token:
                        logger.info(f"👤 User token also generated: {room_url}?t={user_token}")

        return room_url, token

    async def setup_transport_and_pipeline(self, room_url: str, token: str):
        """Setup Daily transport and pipeline with your existing flow system"""
        logger.info("🔧 Setting up Daily transport and pipeline...")

        # Initialize Supabase database connection (for info agent calls)
        try:
            from services.database import db
            logger.info("🚀 Initializing Supabase database connection pool...")
            await db.connect()
            logger.success("✅ Supabase database initialized for test.py")
        except Exception as e:
            logger.warning(f"⚠️ Could not initialize Supabase database: {e}")
            logger.warning("⚠️ Info agent call data will be saved to backup files only")

        # Create Daily transport
        self.transport = DailyTransport(
            room_url=room_url,
            token=token,
            bot_name="Ualà - Healthcare Assistant (Testing)",
            params=DailyParams(**self.config.daily_transport_params)
        )

        logger.info("✅ Daily transport created")

        # CREATE SERVICES USING BOT.PY COMPONENTS (IDENTICAL TO BOT.PY)
        logger.info("Initializing services...")

        # Validate health service configuration
        try:
            config.validate()
            logger.success("✅ Health services configuration validated")
        except Exception as e:
            logger.error(f"❌ Health services configuration error: {e}")
            raise

        stt = create_stt_service()
        tts = create_tts_service()
        llm = create_llm_service()
        context_aggregator, node_mute_strategy = create_context_aggregator(llm)

        logger.info("✅ All services initialized")

        # CREATE USER IDLE PROCESSOR FOR HANDLING TRANSCRIPTION FAILURES
        from services.idle_handler import create_user_idle_processor
        user_idle_processor = create_user_idle_processor(timeout_seconds=20.0)

        # CREATE PROCESSING TIME TRACKER FOR SLOW RESPONSE DETECTION
        from services.processing_time_tracker import create_processing_time_tracker
        processing_tracker = create_processing_time_tracker()  # Reads from PROCESSING_TIME_THRESHOLD env var

        # CREATE TRANSCRIPT PROCESSOR FOR RECORDING CONVERSATIONS (IDENTICAL TO BOT.PY)
        transcript_processor = TranscriptProcessor()

        # CREATE AUDIO RECORDING (if enabled via RECORDING_ENABLED env var)
        recording_enabled = os.getenv("RECORDING_ENABLED", "false").lower() == "true"
        self.recording_manager = None
        self.audiobuffer = None
        self.debug_audiobuffer = None  # Debug wrapper
        self.audio_data_received = None  # Event to signal when audio data is received

        if recording_enabled:
            from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
            from services.recording_manager import RecordingManager

            self.recording_manager = RecordingManager(self.session_id)
            self.audio_data_received = asyncio.Event()  # Sync event for audio capture

            # Create audio buffer - buffer entire call (buffer_size=0)
            audiobuffer_processor = AudioBufferProcessor(
                sample_rate=16000,
                num_channels=2,  # Stereo for separate user/bot tracks
                buffer_size=0,  # Buffer entire call
            )

            # Wrap with debug logging
            self.debug_audiobuffer = DebugAudioBufferProcessor(audiobuffer_processor)
            self.audiobuffer = audiobuffer_processor

            @self.audiobuffer.event_handler("on_track_audio_data")
            async def on_track_audio_data(buffer, user_audio, bot_audio, sample_rate, num_channels):
                """Capture separate user and bot audio tracks"""
                logger.info(f"🎙️ [DEBUG] on_track_audio_data triggered! User: {len(user_audio)} bytes, Bot: {len(bot_audio)} bytes")
                self.recording_manager.add_user_audio(user_audio)
                self.recording_manager.add_bot_audio(bot_audio)
                # Signal that audio data has been received
                self.audio_data_received.set()
                logger.debug("🎙️ [DEBUG] audio_data_received event SET")

            logger.info("🎙️ Audio recording ENABLED with debug logging (Daily test)")
        else:
            logger.info("🎙️ Audio recording DISABLED")

        # CREATE PIPELINE WITH TRANSCRIPT PROCESSORS AND IDLE HANDLING
        pipeline_components = [
            self.transport.input(),
            stt,
            user_idle_processor,              # Add idle detection after STT (20s complete silence)
            transcript_processor.user(),      # Capture user transcriptions
            context_aggregator.user(),
            llm,
            processing_tracker,               # MOVED HERE: After LLM, can see LLM output frames
            tts,
            self.transport.output(),
        ]

        # AudioBufferProcessor MUST be AFTER transport.output() per official Pipecat docs
        # See: _refs/pipecat/scripts/evals/eval.py lines 315-326
        if self.audiobuffer:
            pipeline_components.append(self.audiobuffer)

        pipeline_components.extend([
            transcript_processor.assistant(), # Capture assistant responses
            context_aggregator.assistant()
        ])

        pipeline = Pipeline(pipeline_components)

        logger.info("Healthcare Flow Pipeline structure:")
        logger.info("  1. Daily Input (WebRTC)")
        logger.info("  2. Deepgram STT")
        logger.info("  3. UserIdleProcessor - Handle transcription failures & 20s silence")
        logger.info("  4. TranscriptProcessor.user() - Capture user transcriptions")
        logger.info("  5. Context Aggregator (User)")
        logger.info("  6. OpenAI LLM (with flows)")
        logger.info("  7. ProcessingTimeTracker - Speak if processing >3s")
        logger.info("  8. ElevenLabs TTS")
        logger.info("  9. Daily Output (WebRTC)")
        if self.audiobuffer:
            logger.info("  10. AudioBufferProcessor - Capture user/bot audio")
        logger.info(f"  {'11' if self.audiobuffer else '10'}. TranscriptProcessor.assistant() - Capture assistant responses")
        logger.info(f"  {'12' if self.audiobuffer else '11'}. Context Aggregator (Assistant)")

        # Create pipeline task with extended idle timeout for API calls and OpenTelemetry tracing enabled
        self.task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_transcriptions=False,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=16000,
                enable_usage_metrics=True,  # Enable metrics for performance monitoring
                enable_metrics=True,
            ),
            enable_tracing=True,  # Enable OpenTelemetry tracing (Phoenix)
            conversation_id=self.session_id,
            additional_span_attributes={
                "session.id": self.session_id,
                "user.id": self.caller_phone or "daily_test_user",
            },
            idle_timeout_secs=600  # 10 minutes - allows for long API calls (sorting, slot search)
        )

        # START PER-CALL LOGGING (create individual logger instance)
        from services.call_logger import CallLogger
        self.call_logger = CallLogger(self.session_id)
        log_file = self.call_logger.start_call_logging(self.session_id, "daily_test")  # Mark as Daily test
        logger.info(f"📁 Daily test call logging started: {log_file}")

        # CREATE FLOW MANAGER (IDENTICAL TO BOT.PY)
        self.flow_manager = create_flow_manager(self.task, llm, context_aggregator, self.transport)

        # Link node-aware mute strategy to flow state (must be after flow_manager creation)
        node_mute_strategy.set_flow_state(self.flow_manager.state)

        # Store business_status, session_id, and stream_sid in flow manager state (required for info agent)
        self.flow_manager.state["business_status"] = "open"  # Always open for testing
        self.flow_manager.state["session_id"] = self.session_id
        self.flow_manager.state["stream_sid"] = ""  # Empty for Daily testing (no Talkdesk)
        logger.info(f"✅ Business status stored in flow state: open (testing)")
        logger.info(f"✅ Session ID stored in flow state: {self.session_id}")
        logger.info(f"✅ Stream SID: Not applicable (Daily room testing)")

        # PRE-POPULATE STATE WITH CALLER INFO (Simulate Talkdesk caller ID)
        if self.caller_phone:
            self.flow_manager.state["caller_phone_from_talkdesk"] = self.caller_phone
            logger.info(f"📞 Simulated caller phone from Talkdesk: {self.caller_phone}")

        if self.patient_dob:
            self.flow_manager.state["patient_dob"] = self.patient_dob
            logger.info(f"📅 Pre-populated patient DOB: {self.patient_dob}")

        # Initialize STT switcher for dynamic transcription (IDENTICAL TO BOT.PY)
        initialize_stt_switcher(stt, self.flow_manager)

        # Setup transcript recording event handler (must be AFTER flow_manager creation)
        @transcript_processor.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            """Handle transcript updates from TranscriptProcessor"""
            logger.info(f"📝 Transcript update received with {len(frame.messages)} messages")

            # Get session-specific transcript manager (for booking agent)
            session_transcript_manager = get_transcript_manager(self.session_id)

            for message in frame.messages:
                logger.info(f"📝 Recording {message.role} message: '{message.content[:50]}{'...' if len(message.content) > 50 else ''}'")

                # Always add to transcript_manager (needed for both agents)
                if message.role == "user":
                    session_transcript_manager.add_user_message(message.content)
                elif message.role == "assistant":
                    session_transcript_manager.add_assistant_message(message.content)

                # ALSO add to call_extractor (ALL calls - for Supabase storage)
                call_extractor_instance = self.flow_manager.state.get("call_extractor")
                if call_extractor_instance:
                    call_extractor_instance.add_transcript_entry(message.role, message.content)
                    logger.debug(f"📊 Added to call_extractor: {message.role}")

            logger.info(f"📊 Transcript now has {len(session_transcript_manager.conversation_log)} messages")

        logger.success("✅ Pipeline and flow manager created")

        return stt

    async def setup_event_handlers(self):
        """Setup Daily transport event handlers"""
        logger.info("🔧 Setting up Daily transport event handlers...")

        @self.transport.event_handler("on_participant_joined")
        async def on_participant_joined(transport, participant):
            logger.info(f"✅ Healthcare Flow Client connected: {self.session_id}")
            logger.info(f"👤 Participant joined: {participant.get('user_name', 'Unknown')} (ID: {participant.get('id', 'N/A')})")

            # Start transcript recording session (IDENTICAL TO BOT.PY)
            session_transcript_manager = get_transcript_manager(self.session_id)
            session_transcript_manager.start_session(self.session_id)
            logger.info(f"📝 Started transcript recording for session: {self.session_id}")
            logger.info(f"📊 Transcript manager initialized with {len(session_transcript_manager.conversation_log)} messages")

            # Initialize call_extractor for ALL calls (SAME AS BOT.PY - saves to Supabase)
            from services.call_data_extractor import get_call_extractor
            call_extractor = get_call_extractor(self.session_id)
            call_extractor.call_id = self.session_id
            self.flow_manager.state["call_extractor"] = call_extractor
            call_extractor.start_call(caller_phone=self.caller_phone or "", interaction_id="")
            logger.info(f"✅ Call extractor initialized for Supabase storage")
            logger.info(f"⏱️ Call start time recorded: {call_extractor.started_at}")

            # Start audio recording if enabled
            if self.audiobuffer:
                await self.audiobuffer.start_recording()
                logger.info("🎙️ Audio recording started (Daily test)")

            # Initialize flow manager (IDENTICAL TO BOT.PY)
            try:
                await initialize_flow_manager(self.flow_manager, self.start_node)
                logger.success(f"✅ Flow initialized with {self.start_node} node")
            except Exception as e:
                logger.error(f"Error during flow initialization: {e}")

        @self.transport.event_handler("on_audio_track_started")
        async def on_audio_track_started(transport, participant_id):
            logger.info(f"🎤 Audio track started for participant: {participant_id}")

        @self.transport.event_handler("on_audio_track_stopped")
        async def on_audio_track_stopped(transport, participant_id):
            logger.info(f"🔇 Audio track stopped for participant: {participant_id}")

        @self.transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, reason):
            logger.info(f"🔌 Healthcare Flow Client disconnected: {self.session_id}")
            logger.info(f"👋 Participant left: {participant.get('user_name', 'Unknown')} (Reason: {reason})")

            # Print latency summary for comparison with Gemini Live
            self.latency_tracker.print_summary()

            # Extract and store call data before cleanup
            # Save to BOTH Azure AND Supabase for ALL calls
            try:
                current_agent = self.flow_manager.state.get("current_agent", "unknown")
                logger.info(f"📊 Extracting call data for session: {self.session_id} | Agent: {current_agent}")

                # === STEP 1: Save to Supabase (ALL calls) ===
                logger.info("🔵 Saving to Supabase...")
                call_extractor = self.flow_manager.state.get("call_extractor")
                if call_extractor:
                    # Query Phoenix for token usage + set trace metadata
                    usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tts_characters": 0}
                    if os.getenv("ENABLE_TRACING", "false").lower() == "true":
                        transcript = call_extractor.transcript or []
                        first_user_msg = None
                        last_assistant_msg = None
                        for entry in transcript:
                            if entry.get("role") == "user" and first_user_msg is None:
                                first_user_msg = entry.get("content", "")
                            if entry.get("role") == "assistant":
                                last_assistant_msg = entry.get("content", "")

                        # STEP 1: Set input/output on conversation span BEFORE it closes
                        try:
                            conv_span = getattr(getattr(self.task, '_turn_trace_observer', None), '_conversation_span', None)
                            if conv_span and hasattr(conv_span, 'set_attribute'):
                                if first_user_msg:
                                    conv_span.set_attribute("input.value", first_user_msg[:1000])
                                if last_assistant_msg:
                                    conv_span.set_attribute("output.value", last_assistant_msg[:1000])
                                logger.info("Set input/output on conversation span")
                            else:
                                logger.warning("Conversation span not accessible")
                        except Exception as span_err:
                            logger.warning(f"Could not set conversation span attrs: {span_err}")

                        # STEP 2: Flush traces and get usage metrics from Phoenix
                        try:
                            await asyncio.sleep(1)
                            flush_traces()
                            await asyncio.sleep(2)  # Phoenix local — fast indexing
                            usage_data = await get_conversation_usage(self.session_id)
                            call_extractor.llm_token_count = usage_data["total_tokens"]
                            logger.success(f"Phoenix usage: LLM={usage_data['total_tokens']} tokens, TTS={usage_data['tts_characters']} chars")
                        except Exception as e:
                            logger.error(f"Failed to retrieve usage from Phoenix: {e}")

                        # STEP 3: Set call metadata as span attributes + log
                        try:
                            flow_state = self.flow_manager.state or {}
                            if flow_state.get("transfer_requested"):
                                call_type = "transfer"
                            elif flow_state.get("booking_code"):
                                call_type = "booking"
                            elif flow_state.get("selected_services"):
                                call_type = "booking_started"
                            else:
                                call_type = "info"

                            caller_phone = flow_state.get("caller_phone_from_talkdesk", "") or self.caller_phone
                            duration = round(call_extractor._calculate_duration() or 0, 1)

                            # Set call metadata on conversation span
                            conv_span = getattr(getattr(self.task, '_turn_trace_observer', None), '_conversation_span', None)
                            if conv_span and hasattr(conv_span, 'set_attribute'):
                                conv_span.set_attribute("call.type", call_type)
                                conv_span.set_attribute("call.outcome", call_type)
                                conv_span.set_attribute("call.last_node", flow_state.get("current_node", "unknown"))
                                conv_span.set_attribute("call.duration_seconds", duration)
                                conv_span.set_attribute("call.total_tokens", usage_data.get("total_tokens", 0))
                                conv_span.set_attribute("call.tts_characters", usage_data.get("tts_characters", 0))

                            if first_user_msg or last_assistant_msg:
                                trace_metadata = {
                                    "outcome": call_type,
                                    "last_node": flow_state.get("current_node", "unknown"),
                                    "node_history": flow_state.get("node_history", []),
                                    "failure_count": flow_state.get("failure_tracker", {}).get("count", 0),
                                    "duration_seconds": duration,
                                    "llm_total_tokens": usage_data.get("total_tokens", 0),
                                    "tts_characters": usage_data.get("tts_characters", 0),
                                }

                                try:
                                    from utils.cost_tracker import calculate_call_cost
                                    cost = calculate_call_cost(
                                        llm_input_tokens=usage_data.get("prompt_tokens", 0),
                                        llm_output_tokens=usage_data.get("completion_tokens", 0),
                                        tts_characters=usage_data.get("tts_characters", 0),
                                        call_duration_seconds=duration,
                                        stt_provider=settings.stt_provider,
                                    )
                                    trace_metadata.update(cost.to_dict())
                                except Exception as cost_err:
                                    logger.warning(f"Cost calculation failed: {cost_err}")

                                await update_trace_metadata(
                                    self.session_id,
                                    first_user_msg or "",
                                    last_assistant_msg or "",
                                    call_type=call_type,
                                    caller_phone=caller_phone,
                                    metadata=trace_metadata
                                )
                        except Exception as io_err:
                            logger.error(f"Failed to update trace metadata: {io_err}")

                    # Save recordings if enabled (BEFORE call_extractor.save_to_database)
                    if self.recording_manager and self.audiobuffer:
                        try:
                            # Log buffer status BEFORE stop_recording
                            if self.debug_audiobuffer:
                                logger.info("🎙️ [DEBUG] Buffer status BEFORE stop_recording:")
                                self.debug_audiobuffer.log_buffer_status()

                            # Reset the event before stopping
                            if self.audio_data_received:
                                self.audio_data_received.clear()

                            # Stop recording - triggers on_track_audio_data event
                            await self.audiobuffer.stop_recording()

                            # CRITICAL: Wait for the async event handler to complete
                            # Pipecat's event dispatch is async and doesn't block stop_recording()
                            if self.audio_data_received:
                                logger.info("🎙️ [DEBUG] Waiting for on_track_audio_data event...")
                                try:
                                    await asyncio.wait_for(self.audio_data_received.wait(), timeout=2.0)
                                    logger.info("🎙️ [DEBUG] on_track_audio_data event received!")
                                except asyncio.TimeoutError:
                                    logger.warning("🎙️ [DEBUG] Timeout waiting for on_track_audio_data (no audio captured?)")

                            # Log buffer status AFTER stop_recording
                            if self.debug_audiobuffer:
                                logger.info("🎙️ [DEBUG] Buffer status AFTER stop_recording:")
                                self.debug_audiobuffer.log_buffer_status()

                            recording_urls = await self.recording_manager.save_recordings()
                            if recording_urls:
                                call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                                call_extractor.recording_url_user = recording_urls.get("user_url")
                                call_extractor.recording_url_bot = recording_urls.get("bot_url")
                                call_extractor.recording_duration = self.recording_manager.get_duration_seconds()
                                logger.success(f"🎙️ Recordings saved (Daily test) ({call_extractor.recording_duration:.1f}s)")
                        except Exception as e:
                            logger.error(f"❌ Failed to save recordings (Daily test): {e}")

                    # Mark call end time and save to Supabase
                    call_extractor.end_call()
                    supabase_success = await call_extractor.save_to_database(self.flow_manager.state)
                    if supabase_success:
                        logger.success(f"✅ Call data saved to Supabase for session: {self.session_id}")
                    else:
                        logger.error(f"❌ Failed to save call data to Supabase: {self.session_id}")
                else:
                    logger.error("❌ No call_extractor found - Supabase save skipped")

                # === STEP 2: Save to Azure Blob Storage (ALL calls) ===
                logger.info("🟢 Saving to Azure Blob Storage...")
                session_transcript_manager = get_transcript_manager(self.session_id)
                azure_success = await session_transcript_manager.extract_and_store_call_data(self.flow_manager)
                if azure_success:
                    logger.success(f"✅ Call data saved to Azure for session: {self.session_id}")
                else:
                    logger.error(f"❌ Failed to save call data to Azure: {self.session_id}")

            except Exception as e:
                logger.error(f"❌ Error during call data extraction: {e}")
                import traceback
                traceback.print_exc()

            # Clear transcript session and cleanup (IDENTICAL TO BOT.PY)
            cleanup_transcript_manager(self.session_id)

            # STOP PER-CALL LOGGING (same as bot.py)
            try:
                if self.call_logger:
                    saved_log_file = self.call_logger.stop_call_logging()
                    if saved_log_file:
                        logger.info(f"📁 Call log saved: {saved_log_file}")
            except Exception as e:
                logger.error(f"❌ Error stopping call logging: {e}")

            # Stop the test session
            if self.task:
                await self.task.cancel()
                logger.info("🛑 Daily test session ended")

        @self.transport.event_handler("on_call_state_updated")
        async def on_call_state_updated(transport, state):
            logger.info(f"📞 Call state updated: {state}")

        @self.transport.event_handler("on_error")
        async def on_error(transport, error):
            logger.error(f"❌ Daily transport error: {error}")

        logger.success("✅ Event handlers configured")

    async def run_test_session(self, room_url: Optional[str] = None, token: Optional[str] = None):
        """Run the Daily test session"""
        try:
            # Create room if not provided
            if not room_url or not token:
                room_url, token = await self.create_daily_room()

            self.room_url = room_url
            self.token = token

            # Setup transport and pipeline
            stt = await self.setup_transport_and_pipeline(room_url, token)

            # Setup event handlers
            await self.setup_event_handlers()

            # Display connection info
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info("🚀 DAILY HEALTHCARE FLOW TESTING SESSION")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"🏠 Room URL: {room_url}")
            logger.info(f"🎯 Starting Node: {self.start_node}")
            logger.info(f"🔧 Session ID: {self.session_id}")
            logger.info(f"🎤 STT: {settings.azure_stt_config['region']} {settings.azure_stt_config['language']}")
            logger.info(f"🗣️  TTS: {settings.elevenlabs_config['model']} (Voice: {settings.elevenlabs_config['voice_id']})")
            logger.info(f"🧠 LLM: {settings.openai_config['model']}")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info("🎯 TESTING INSTRUCTIONS:")
            logger.info("   1. Open the room URL above in your browser")
            logger.info("   2. Allow microphone access when prompted")
            logger.info("   3. Start speaking to test your healthcare agent")
            logger.info("   4. The bot will join automatically when you connect")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info("💡 TIPS:")
            logger.info("   • Your existing flows will work exactly as in production")
            logger.info("   • Any changes to flows/* will be reflected immediately")
            logger.info("   • Check the logs below for real-time debugging")
            logger.info("   • Call logs are automatically saved to call_logs/ directory")
            logger.info("   • Press Ctrl+C to stop the testing session")
            logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # Start pipeline
            self.runner = PipelineRunner()
            logger.info("🚀 Starting Daily pipeline...")

            # Run pipeline (blocks until session ends)
            await self.runner.run(self.task)

        except KeyboardInterrupt:
            logger.info("⌨️ Test session interrupted by user")
        except Exception as e:
            logger.error(f"❌ Error in Daily test session: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Cleanup resources"""
        logger.info("🧹 Cleaning up Daily test session...")

        # Cancel task
        if self.task:
            try:
                await self.task.cancel()
            except:
                pass

        # Cleanup transcript session
        try:
            cleanup_transcript_manager(self.session_id)
        except:
            pass

        # STOP PER-CALL LOGGING (use session-specific logger)
        try:
            if self.call_logger:
                saved_log_file = self.call_logger.stop_call_logging()
                if saved_log_file:
                    logger.info(f"📁 Call log saved: {saved_log_file}")
        except Exception as e:
            logger.error(f"❌ Error stopping call logging: {e}")

        # Optionally delete the room (uncomment if you want auto-cleanup)
        # if self.config.daily_api_key and self.room_url:
        #     try:
        #         import aiohttp
        #         headers = {"Authorization": f"Bearer {self.config.daily_api_key}"}
        #         room_name = self.room_url.split("/")[-1]
        #         async with aiohttp.ClientSession() as session:
        #             async with session.delete(f"{self.config.daily_api_url}/rooms/{room_name}", headers=headers) as response:
        #                 if response.status == 200:
        #                     logger.info("🗑️ Daily room deleted")
        #     except Exception as e:
        #         logger.warning(f"⚠️ Could not delete room: {e}")

        # Flush OpenTelemetry traces to Phoenix before exit
        try:
            flush_traces()
        except Exception as e:
            logger.error(f"❌ Error flushing traces: {e}")

        logger.success("✅ Daily test session cleanup completed")


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Daily Transport Testing for Healthcare Flow Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test.py                                    # Full flow (greeting)
  python test.py --start-node email                 # Start with email collection
  python test.py --start-node booking               # Start with booking flow
  python test.py --start-node name                  # Start with name collection
  python test.py --start-node 
                  # Start with date selection (pre-filled data)
  python test.py --room-url <url> --token <token>   # Use existing room
        """
    )

    parser.add_argument(
        "--start-node",
        default="router",
        choices=["router", "greeting", "email", "name", "phone", "fiscal_code", "booking", "slot_selection"],
        help="Starting flow node (default: router for unified routing, greeting for direct booking)"
    )

    parser.add_argument(
        "--room-url",
        help="Existing Daily room URL (optional, will create new room if not provided)"
    )

    parser.add_argument(
        "--token",
        help="Daily room token (required if --room-url is provided)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    parser.add_argument(
        "--caller-phone",
        default=None,
        help="Simulate caller phone number from Talkdesk (e.g., +393333319326 for testing existing patient)"
    )

    parser.add_argument(
        "--patient-dob",
        default=None,
        help="Simulate patient date of birth (YYYY-MM-DD format, e.g., 1979-06-19 for testing existing patient)"
    )

    return parser.parse_args()


async def main():
    """Main function"""
    args = parse_arguments()

    # Configure logging level
    if args.debug:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>DAILY-TEST</cyan> | {message}")

    # Validate arguments
    if args.room_url and not args.token:
        logger.error("❌ --token is required when --room-url is provided")
        sys.exit(1)

    # Check required environment variables
    required_env_vars = [
        "DAILY_API_KEY",
        "DEEPGRAM_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY"
    ]

    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    logger.info("🎯 Starting Daily Healthcare Flow Testing...")
    logger.info(f"📍 Start Node: {args.start_node}")

    # Initialize OpenTelemetry tracing (Phoenix)
    tracer = setup_tracing(
        service_name="pipecat-healthcare-daily-test",
        enable_console=False
    )
    if tracer:
        logger.success("Phoenix tracing initialized for Daily voice testing")
    else:
        logger.warning("Tracing disabled (set ENABLE_TRACING=true in .env to enable)")

    # Log simulated caller data if provided
    if args.caller_phone or args.patient_dob:
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("🎭 SIMULATED CALLER DATA (like from Talkdesk):")
        if args.caller_phone:
            logger.info(f"   📞 Caller Phone: {args.caller_phone}")
        if args.patient_dob:
            logger.info(f"   📅 Patient DOB: {args.patient_dob}")
        logger.info("   ✅ This will test existing patient flow (database lookup)")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Create and run tester
    tester = DailyHealthcareFlowTester(
        start_node=args.start_node,
        caller_phone=args.caller_phone,
        patient_dob=args.patient_dob
    )
    await tester.run_test_session(
        room_url=args.room_url,
        token=args.token
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Daily test session ended by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)