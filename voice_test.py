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
import aiohttp
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from loguru import logger

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

# Daily transport imports
from pipecat.transports.daily.transport import DailyParams, DailyTransport

# OpenTelemetry & LangFuse tracing
from config.telemetry import setup_tracing, get_tracer, get_conversation_tokens, flush_traces
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
        context_aggregator = create_context_aggregator(llm)

        logger.info("✅ All services initialized")

        # CREATE USER IDLE PROCESSOR FOR HANDLING TRANSCRIPTION FAILURES
        from services.idle_handler import create_user_idle_processor
        user_idle_processor = create_user_idle_processor(timeout_seconds=20.0)

        # CREATE PROCESSING TIME TRACKER FOR SLOW RESPONSE DETECTION
        from services.processing_time_tracker import create_processing_time_tracker
        processing_tracker = create_processing_time_tracker()  # Reads from PROCESSING_TIME_THRESHOLD env var

        # CREATE TRANSCRIPT PROCESSOR FOR RECORDING CONVERSATIONS (IDENTICAL TO BOT.PY)
        transcript_processor = TranscriptProcessor()

        # CREATE PIPELINE WITH TRANSCRIPT PROCESSORS AND IDLE HANDLING
        pipeline = Pipeline([
            self.transport.input(),
            stt,
            user_idle_processor,              # Add idle detection after STT (20s complete silence)
            transcript_processor.user(),      # Capture user transcriptions
            context_aggregator.user(),
            llm,
            processing_tracker,               # MOVED HERE: After LLM, can see LLM output frames
            tts,
            self.transport.output(),
            transcript_processor.assistant(), # Capture assistant responses
            context_aggregator.assistant()
        ])

        logger.info("Healthcare Flow Pipeline structure:")
        logger.info("  1. Daily Input (WebRTC)")
        logger.info("  2. Deepgram STT")
        logger.info("  3. UserIdleProcessor - Handle transcription failures & 20s silence")
        logger.info("  4. ProcessingTimeTracker - Speak if processing >3s")
        logger.info("  5. TranscriptProcessor.user() - Capture user transcriptions")
        logger.info("  6. Context Aggregator (User)")
        logger.info("  7. OpenAI LLM (with flows + gender node termina→femmina correction)")
        logger.info("  8. ElevenLabs TTS")
        logger.info("  9. Daily Output (WebRTC)")
        logger.info("  10. TranscriptProcessor.assistant() - Capture assistant responses")
        logger.info("  11. Context Aggregator (Assistant)")

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
            enable_tracing=True,  # ✅ Enable OpenTelemetry tracing (LangFuse)
            conversation_id=self.session_id,  # Use session_id as conversation ID for trace correlation
            # ✅ Add langfuse.session.id to map our session_id to LangFuse sessions
            additional_span_attributes={
                "langfuse.session.id": self.session_id,
                "langfuse.user.id": self.caller_phone or "daily_test_user",
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

                # ALSO add to call_extractor if info agent is active
                current_agent = self.flow_manager.state.get("current_agent")
                if current_agent == "info":
                    call_extractor_instance = self.flow_manager.state.get("call_extractor")
                    if call_extractor_instance:
                        call_extractor_instance.add_transcript_entry(message.role, message.content)
                        logger.debug(f"📊 Added to info agent call_extractor: {message.role}")

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

            # Extract and store call data before cleanup
            # Route to appropriate storage based on which agent handled the call
            try:
                current_agent = self.flow_manager.state.get("current_agent", "unknown")
                logger.info(f"📊 Extracting call data for session: {self.session_id} | Agent: {current_agent}")

                if current_agent == "info":
                    # INFO AGENT: Use Supabase storage via call_data_extractor
                    logger.info("🟠 INFO AGENT call - routing to Supabase storage")

                    call_extractor = self.flow_manager.state.get("call_extractor")
                    if call_extractor:
                        # ✅ Query LangFuse for token usage before saving to Supabase
                        if os.getenv("ENABLE_TRACING", "false").lower() == "true":
                            logger.info("📊 Querying LangFuse for token usage...")
                            try:
                                # Wait briefly for Pipecat's BatchSpanProcessor to queue final spans
                                # The conversation tracing just ended, spans need time to be queued
                                logger.info("⏳ Waiting 1 second for spans to be queued...")
                                await asyncio.sleep(1)

                                # CRITICAL: Flush traces to LangFuse BEFORE querying
                                # Otherwise spans are still in BatchSpanProcessor queue
                                logger.info("🔄 Flushing traces to LangFuse before token query...")
                                flush_traces()

                                # Wait for LangFuse to index the traces
                                # Production needs more time due to cloud indexing latency
                                logger.info("⏳ Waiting 5 seconds for LangFuse to index traces...")
                                await asyncio.sleep(5)

                                # Get token usage from LangFuse
                                token_data = await get_conversation_tokens(self.session_id)

                                # Update call_extractor with token data
                                call_extractor.llm_token_count = token_data["total_tokens"]
                                logger.success(f"✅ Updated call_extractor with LangFuse tokens: {token_data['total_tokens']}")

                            except Exception as e:
                                logger.error(f"❌ Failed to retrieve tokens from LangFuse: {e}")
                                # Continue with save even if LangFuse query fails

                        # ✅ CRITICAL: Mark call end time before saving
                        call_extractor.end_call()
                        success = await call_extractor.save_to_database(self.flow_manager.state)
                        if success:
                            logger.success(f"✅ Info agent call data saved to Supabase for session: {self.session_id}")
                        else:
                            logger.error(f"❌ Failed to save info agent call data to Supabase: {self.session_id}")
                    else:
                        logger.error("❌ No call_extractor found in flow_manager.state for info agent")

                else:
                    # BOOKING AGENT (or unknown/router): Use Azure Blob Storage via transcript_manager
                    logger.info(f"🟢 BOOKING AGENT call - routing to Azure Blob Storage")

                    session_transcript_manager = get_transcript_manager(self.session_id)
                    success = await session_transcript_manager.extract_and_store_call_data(self.flow_manager)
                    if success:
                        logger.success(f"✅ Booking agent call data saved to Azure for session: {self.session_id}")
                    else:
                        logger.error(f"❌ Failed to save booking agent call data to Azure: {self.session_id}")

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

        # Flush OpenTelemetry traces to Langfuse before exit
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

    # Initialize OpenTelemetry tracing (LangFuse)
    tracer = setup_tracing(
        service_name="pipecat-healthcare-daily-test",
        enable_console=False
    )
    if tracer:
        logger.success("✅ LangFuse tracing initialized for Daily voice testing")
    else:
        logger.warning("⚠️ LangFuse tracing disabled (set ENABLE_TRACING=true in .env to enable)")

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