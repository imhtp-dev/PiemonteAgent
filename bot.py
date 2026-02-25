# Piemonte Region Agent
import os
import re
import asyncio
import wave
import time
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv
from loguru import logger

# Enable Deepgram and WebSocket debuggings
logging.getLogger("deepgram").setLevel(logging.DEBUG)
logging.getLogger("websockets").setLevel(logging.DEBUG)

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Core Pipecat imports
from pipecat.frames.frames import (
    TranscriptionFrame,
    InterimTranscriptionFrame,
    Frame,
    TTSSpeakFrame,
    LLMMessagesFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    MetricsFrame
)
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# OpenTelemetry & Phoenix
from config.telemetry import (
    setup_tracing,
    get_tracer,
    get_conversation_usage,
    flush_traces,
    update_trace_metadata,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams

# Serializer imports
from pipecat.serializers.base_serializer import FrameSerializer

# Import flow management
from flows.manager import create_flow_manager, initialize_flow_manager

from config.settings import settings
from services.config import config
from pipeline.components import create_stt_service, create_tts_service, create_llm_service, create_context_aggregator

# Import transcript manager for conversation recording and call data extraction
from services.transcript_manager import get_transcript_manager, cleanup_transcript_manager

load_dotenv(override=True)

# SIMPLE PCM SERIALIZER
class RawPCMSerializer(FrameSerializer):
    """
    Simple serializer for PCM raw (EXACTLY LIKE APP.PY)
    """

    @property
    def type(self):
        return "binary"

    async def serialize(self, frame: Frame) -> bytes:
        """Serialize outgoing audio frames"""
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return b''

    async def deserialize(self, data) -> Frame:
        """Deserialize incoming PCM raw"""
        if isinstance(data, bytes) and len(data) > 0:
            return InputAudioRawFrame(
                audio=data,
                sample_rate=16000,
                num_channels=1
            )
        return None


async def report_to_talkdesk(flow_manager, call_extractor):
    """
    Report call completion to Talkdesk (ONLY if not transferred to human operator).

    Args:
        flow_manager: FlowManager instance with state
        call_extractor: CallDataExtractor instance with call data

    Returns:
        bool: True if successfully sent to Talkdesk, False otherwise
    """
    try:
        # Check 1: Was call transferred to human operator?
        if flow_manager.state.get("transfer_requested"):
            logger.info("⏭️ Skipping Talkdesk report - call was transferred to human operator")
            return False

        # Check 2: Do we have interaction_id?
        interaction_id = flow_manager.state.get("interaction_id")
        if not interaction_id:
            logger.warning("⚠️ No interaction_id - cannot report to Talkdesk")
            return False

        logger.info(f"📤 Preparing Talkdesk report for interaction: {interaction_id}")

        # Get analysis data (reuse if available from transfer preparation)
        analysis = flow_manager.state.get("transfer_analysis")

        if not analysis:
            logger.info("🔍 No pre-computed analysis, running LLM analysis for Talkdesk report")
            transcript_text = call_extractor._generate_transcript_text()
            analysis = await call_extractor._analyze_call_with_llm(
                transcript_text,
                flow_manager.state
            )
        else:
            logger.info("✅ Using pre-computed analysis from transfer preparation")

        # Determine sector and service code
        service_code = str(analysis.get("service", "5"))
        sector = analysis.get("sector", "info")
        # Also check flow state for booking context
        if not sector or sector == "info":
            if flow_manager.state.get("selected_services") or flow_manager.state.get("booking_in_progress"):
                sector = "booking"
        service_prefix = "1|1" if sector == "booking" else "2|2"

        # Build Talkdesk payload
        call_data = {
            "interaction_id": interaction_id,
            "sentiment": analysis.get("sentiment", "neutral"),
            "service": f"{service_prefix}|{service_code}",
            "summary": analysis.get("summary", "")[:250],  # Max 250 chars
            "duration_seconds": int(call_extractor._calculate_duration() or 0)
        }

        logger.info(f"📊 Talkdesk payload prepared:")
        logger.info(f"   Interaction ID: {call_data['interaction_id']}")
        logger.info(f"   Sentiment: {call_data['sentiment']}")
        logger.info(f"   Service: {call_data['service']}")
        logger.info(f"   Duration: {call_data['duration_seconds']}s")
        logger.info(f"   Summary: {call_data['summary'][:100]}...")

        # Send to Talkdesk
        from services.talkdesk_service import send_to_talkdesk
        success = send_to_talkdesk(call_data)

        if success:
            logger.success(f"✅ Talkdesk report sent successfully for interaction: {interaction_id}")
        else:
            logger.error(f"❌ Talkdesk report failed for interaction: {interaction_id}")

        return success

    except Exception as e:
        logger.error(f"❌ Error reporting to Talkdesk: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


# LIFESPAN CONTEXT MANAGER

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("🚀 Starting up Healthcare Flow Bot...")

    # Initialize Supabase database connection for info agent
    try:
        from services.database import db
        await db.connect()
        logger.success("✅ Info agent Supabase database initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Supabase database: {e}")
        logger.warning("⚠️ Info agent will use backup files for failed database saves")

    # Initialize Pinecone and OpenAI for Q&A management
    try:
        from api.qa import initialize_ai_services
        initialize_ai_services()
        logger.success("✅ AI services (Pinecone + OpenAI) initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI services: {e}")
        logger.warning("⚠️ Q&A management will not work without Pinecone")

    yield

    # Shutdown
    logger.info("🛑 Shutting down Healthcare Flow Bot...")

    # Close Supabase database connection
    try:
        from services.database import db
        await db.close()
        logger.success("✅ Info agent Supabase database closed")
    except Exception as e:
        logger.error(f"❌ Error closing Supabase database: {e}")


# FASTAPI APP

app = FastAPI(
    title="Healthcare Flow Bot with Working WebSocket",
    description="Healthcare flow bot using app.py WebSocket transport",
    version="5.0.0",
    lifespan=lifespan
)

# Initialize OpenTelemetry tracing (Phoenix)
tracer = setup_tracing(
    service_name="pipecat-healthcare-production",
    enable_console=False
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== REGISTER API ROUTERS ====================
# Import and register chat API router (other endpoints migrated to Supabase Edge Functions)
from api import chat

app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])

logger.info("✅ Chat API router registered")
logger.info("   - /api/chat/* - Chat endpoints (other APIs now in Supabase Edge Functions)")

# Store for active sessions
active_sessions: Dict[str, Any] = {}

# HOMEPAGE 

@app.get("/")
async def root():
    """Homepage with information about the server"""
    return HTMLResponse(f"""
    <html>
        <head>
            <title>Healthcare Flow Bot - Working WebSocket</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Arial, sans-serif;
                    margin: 40px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }}
                .container {{
                    background: rgba(255,255,255,0.95);
                    color: #333;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                    max-width: 800px;
                    margin: 0 auto;
                }}
                .status {{
                    color: #22c55e;
                    font-weight: bold;
                }}
                .service {{
                    display: inline-block;
                    padding: 5px 10px;
                    margin: 5px;
                    background: #667eea;
                    color: white;
                    border-radius: 5px;
                    font-size: 12px;
                }}
                h1 {{ color: #333; }}
                h2 {{ color: #667eea; margin-top: 30px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🏥 Healthcare Flow Bot - Working WebSocket</h1>
                <p class="status">✅ Server is running with app.py WebSocket transport</p>

                <h2>Active Services:</h2>
                <div>
                    <span class="service">Deepgram STT</span>
                    <span class="service">OpenAI GPT-4</span>
                    <span class="service">ElevenLabs TTS</span>
                    <span class="service">Pipecat Flows</span>
                </div>

                <h2>Endpoints:</h2>
                <ul>
                    <li><code>GET /</code> - This page</li>
                    <li><code>GET /health</code> - Health check</li>
                    <li><code>WS /ws</code> - WebSocket endpoint for bridge</li>
                </ul>

                <h2>Statistics:</h2>
                <p>Active sessions: <strong>{len(active_sessions)}</strong></p>
            </div>
        </body>
    </html>
    """)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "service": "healthcare-flow-bot-websocket",
        "version": "5.0.0",
        "active_sessions": len(active_sessions),
        "services": {
            "stt": "deepgram",
            "llm": "openai-gpt4",
            "tts": "elevenlabs",
            "flows": "pipecat-flows",
            "transport": "fastapi-websocket-from-app.py"
        }
    })

# MAIN WEBSOCKET ENDPOINT
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Healthcare Flow Bot WebSocket endpoint
    USES EXACT SAME STRUCTURE AS APP.PY BUT WITH BOT.PY FLOW INTELLIGENCE
    """
    await websocket.accept()

    # Extract parameters from query string
    query_params = dict(websocket.query_params)
    business_status = query_params.get("business_status")  # ✅ NO DEFAULT - Must come from TalkDesk
    import uuid
    session_id = query_params.get("session_id", f"session-{uuid.uuid4().hex[:8]}")
    start_node = query_params.get("start_node", "router")  # Default to unified router
    caller_phone = query_params.get("caller_phone", "")
    stream_sid = query_params.get("stream_sid", "")  # ✅ Talkdesk stream SID (for escalation stop message)
    interaction_id = query_params.get("interaction_id", "")  # ✅ Talkdesk interaction ID (for database tracking)

    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"New Healthcare Flow WebSocket Connection")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Business Status: {business_status or 'NOT PROVIDED - ERROR!'}")  # ✅ Log clearly if missing
    logger.info(f"Start Node: {start_node}")
    logger.info(f"Caller Phone: {caller_phone or 'Not provided'}")
    logger.info(f"Stream SID: {stream_sid or 'Not provided'}")  # ✅ Talkdesk stream SID (for escalation)
    logger.info(f"Interaction ID: {interaction_id or 'Not provided'}")  # ✅ Talkdesk interaction ID
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # ✅ Validate business_status is provided
    if not business_status:
        logger.error("❌ CRITICAL: business_status not provided by TalkDesk bridge!")
        logger.error("   This will cause incorrect transfer behavior")
        business_status = "close"  # Safe fallback - no transfers when unsure
        logger.warning(f"⚠️ Using fallback business_status: {business_status}")

    # Variables for pipeline
    runner = None
    task = None

    try:
        # Check required API keys
        required_keys = [
            ("DEEPGRAM_API_KEY", "Deepgram"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("OPENAI_API_KEY", "OpenAI")
        ]

        for key_name, service_name in required_keys:
            if not os.getenv(key_name):
                raise Exception(f"{key_name} not found - required for {service_name}")

        # Validate health service configuration
        try:
            config.validate()
            logger.success("✅ Health services configuration validated")
        except Exception as e:
            logger.error(f"❌ Health services configuration error: {e}")
            raise

        # CREATE TRANSPORT
        # When Smart Turn is active, VAD fires quickly (0.2s) and the ML model
        # decides if the turn is truly over. Without Smart Turn, use normal stop_secs.
        vad_stop_secs = 0.2 if settings.smart_turn_enabled else settings.vad_config["stop_secs"]
        logger.info(f"VAD stop_secs={vad_stop_secs} (Smart Turn {'active' if settings.smart_turn_enabled else 'off'})")

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        start_secs=settings.vad_config["start_secs"],
                        stop_secs=vad_stop_secs,
                        min_volume=settings.vad_config["min_volume"]
                    )
                ),
                serializer=RawPCMSerializer(),  # EXACT SAME AS APP.PY
                session_timeout=900,
                )
            )
        

        # CREATE SERVICES USING BOT.PY COMPONENTS
        logger.info("Initializing services...")
        stt = create_stt_service()

        tts = create_tts_service()
        llm = create_llm_service()
        context_aggregator, node_mute_strategy = create_context_aggregator(
            llm, smart_turn_enabled=settings.smart_turn_enabled
        )

        # CREATE TRANSCRIPT PROCESSOR FOR RECORDING CONVERSATIONS
        transcript_processor = TranscriptProcessor()

        logger.info("✅ All services initialized")

        # CREATE USER IDLE PROCESSOR FOR HANDLING TRANSCRIPTION FAILURES
        from services.idle_handler import create_user_idle_processor
        user_idle_processor = create_user_idle_processor(timeout_seconds=20.0)
        logger.info("🕐 UserIdleProcessor created (20s timeout - accounts for API processing delays)")

        # CREATE PROCESSING TIME TRACKER FOR SLOW RESPONSE DETECTION
        from services.processing_time_tracker import create_processing_time_tracker
        processing_tracker = create_processing_time_tracker()  # Reads from PROCESSING_TIME_THRESHOLD env var
        logger.info("🕐 ProcessingTimeTracker created (threshold from env - speaks if processing slow)")

        # CREATE AUDIO RECORDING (if enabled via RECORDING_ENABLED env var)
        recording_enabled = os.getenv("RECORDING_ENABLED", "false").lower() == "true"
        recording_manager = None
        audiobuffer = None
        audio_data_received = None  # Event to signal when audio data is received

        if recording_enabled:
            from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
            from services.recording_manager import RecordingManager

            recording_manager = RecordingManager(session_id)
            audio_data_received = asyncio.Event()  # Sync event for audio capture

            # Create audio buffer - buffer entire call (buffer_size=0)
            audiobuffer = AudioBufferProcessor(
                sample_rate=16000,
                num_channels=2,  # Stereo for separate user/bot tracks
                buffer_size=0,  # Buffer entire call
            )

            @audiobuffer.event_handler("on_track_audio_data")
            async def on_track_audio_data(buffer, user_audio, bot_audio, sample_rate, num_channels):
                """Capture separate user and bot audio tracks"""
                recording_manager.add_user_audio(user_audio)
                recording_manager.add_bot_audio(bot_audio)
                # Signal that audio data has been received
                audio_data_received.set()

            logger.info("🎙️ Audio recording ENABLED")
        else:
            logger.info("🎙️ Audio recording DISABLED")

        # CREATE PIPELINE WITH TRANSCRIPT PROCESSORS AND IDLE HANDLING
        pipeline_components = [
            transport.input(),
            stt,
            user_idle_processor,                      # Add idle detection after STT (20s complete silence)
            transcript_processor.user(),              # Capture user transcriptions
            context_aggregator.user(),
            llm,
            processing_tracker,                       # MOVED HERE: After LLM, can see LLM output frames
            tts,
            transport.output(),
        ]

        # AudioBufferProcessor MUST be AFTER transport.output() per official Pipecat docs
        # See: _refs/pipecat/scripts/evals/eval.py lines 315-326
        if audiobuffer:
            pipeline_components.append(audiobuffer)

        pipeline_components.extend([
            transcript_processor.assistant(),         # Capture assistant responses
            context_aggregator.assistant()
        ])

        pipeline = Pipeline(pipeline_components)

        logger.info("Healthcare Flow Pipeline structure:")
        logger.info("  1. Input (PCM from bridge)")
        logger.info("  2. Deepgram STT")
        logger.info("  3. UserIdleProcessor - Handle transcription failures & 20s silence")
        logger.info("  4. TranscriptProcessor.user() - Capture user transcriptions")
        logger.info("  5. Context Aggregator (User)")
        logger.info("  6. OpenAI LLM (with flows)")
        logger.info("  7. ProcessingTimeTracker - Speak if processing >3s")
        logger.info("  8. ElevenLabs TTS")
        logger.info("  9. Output (PCM to bridge)")
        if audiobuffer:
            logger.info("  10. AudioBufferProcessor - Capture user/bot audio")
        logger.info(f"  {'11' if audiobuffer else '10'}. TranscriptProcessor.assistant() - Capture assistant responses")
        logger.info(f"  {'12' if audiobuffer else '11'}. Context Aggregator (Assistant)")

        # START PER-CALL LOGGING (create individual logger instance)
        from services.call_logger import CallLogger
        session_call_logger = CallLogger(session_id)
        log_file = session_call_logger.start_call_logging(session_id, caller_phone)
        logger.info(f"📁 Call logging started: {log_file}")

        # Create pipeline task with extended idle timeout for API calls and OpenTelemetry tracing enabled
        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_transcriptions=True,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=16000,
                enable_usage_metrics=True,  # Keep metrics enabled for performance monitoring
                enable_metrics=True,
            ),
            enable_tracing=True,  # Enable OpenTelemetry tracing (Phoenix)
            conversation_id=session_id,
            additional_span_attributes={
                "session.id": session_id,
                "user.id": caller_phone or "unknown",
            },
            idle_timeout_secs=600  # 10 minutes - allows for long API calls (sorting, slot search)
        )

        # NOW create the real FlowManager with all parameters
        flow_manager = create_flow_manager(task, llm, context_aggregator, transport)

        # Store TTS service ref for direct queue_frame in handlers (bypasses pipeline source)
        flow_manager.state["tts_service"] = tts

        # Link node-aware mute strategy to flow state (must be after flow_manager creation)
        node_mute_strategy.set_flow_state(flow_manager.state)

        # ✅ Store business_status, session_id, and stream_sid in flow manager state
        flow_manager.state["business_status"] = business_status
        flow_manager.state["session_id"] = session_id
        flow_manager.state["stream_sid"] = stream_sid  # ✅ Talkdesk stream SID for escalation
        flow_manager.state["interaction_id"] = interaction_id  # ✅ Talkdesk interaction ID for database
        logger.info(f"✅ Business status stored in flow state: {business_status}")
        logger.info(f"✅ Session ID stored in flow state: {session_id}")
        logger.info(f"✅ Stream SID stored in flow state: {stream_sid or 'Not provided'}")
        logger.info(f"✅ Interaction ID stored in flow state: {interaction_id or 'Not provided'}")

        # Store caller phone number in flow manager state
        if caller_phone:
            flow_manager.state["caller_phone_from_talkdesk"] = caller_phone
            session_call_logger.log_phone_debug("PHONE_STORED_IN_FLOW_STATE", {
                "caller_phone": caller_phone,
                "session_id": session_id,
                "business_status": business_status,
                "flow_state_keys": list(flow_manager.state.keys())
            })

            # Also store in Azure storage for persistence
            try:
                from services.call_storage import CallDataStorage
                storage = CallDataStorage()
                await storage.store_caller_phone(session_id, caller_phone)
                logger.success(f"✅ Caller phone stored in Azure: {caller_phone}")
            except Exception as e:
                logger.error(f"❌ Failed to store caller phone in Azure: {e}")

        # Initialize STT switcher for dynamic transcription
        from utils.stt_switcher import initialize_stt_switcher
        initialize_stt_switcher(stt, flow_manager)

        # Setup transcript recording event handler (must be AFTER flow_manager creation)
        @transcript_processor.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            """Handle transcript updates from TranscriptProcessor"""
            logger.info(f"📝 Transcript update received with {len(frame.messages)} messages")

            # Get session-specific transcript manager (for booking agent)
            session_transcript_manager = get_transcript_manager(session_id)

            for message in frame.messages:
                logger.info(f"📝 Recording {message.role} message: '{message.content[:50]}{'...' if len(message.content) > 50 else ''}'")

                # Always add to transcript_manager (needed for both agents)
                if message.role == "user":
                    session_transcript_manager.add_user_message(message.content)
                elif message.role == "assistant":
                    session_transcript_manager.add_assistant_message(message.content)

                # ALSO add to call_extractor (ALWAYS - Lombardy mode uses info agent only)
                call_extractor_instance = flow_manager.state.get("call_extractor")
                if call_extractor_instance:
                    call_extractor_instance.add_transcript_entry(message.role, message.content)
                    logger.debug(f"📊 Added to call_extractor: {message.role}")

            logger.info(f"📊 Transcript now has {len(session_transcript_manager.conversation_log)} messages")

        # EVENT HANDLERS
        # Transport event handlers
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport_obj, ws):
            logger.info(f"✅ Healthcare Flow Client connected: {session_id}")
            active_sessions[session_id] = {
                "websocket": ws,
                "business_status": business_status,
                "connected_at": asyncio.get_event_loop().time(),
                "call_logger": session_call_logger,  # Store per-session logger
                "services": {
                    "stt": "deepgram",
                    "llm": "openai-gpt4-flows",
                    "tts": "elevenlabs",
                    "flows": "pipecat-flows"
                }
            }

            # Start transcript recording session
            session_transcript_manager = get_transcript_manager(session_id)
            session_transcript_manager.start_session(session_id)
            logger.info(f"📝 Started transcript recording for session: {session_id}")
            logger.info(f"📊 Transcript manager initialized with {len(session_transcript_manager.conversation_log)} messages")

            # Initialize call_extractor for ALL calls (EARLY - to capture ALL messages including router)
            from services.call_data_extractor import get_call_extractor
            call_extractor = get_call_extractor(session_id)
            call_extractor.call_id = session_id  # Override with session_id from bridge
            call_extractor.interaction_id = interaction_id  # Store Talkdesk interaction ID
            flow_manager.state["call_extractor"] = call_extractor

            # ✅ CRITICAL: Start call recording NOW to capture started_at timestamp
            call_extractor.start_call(caller_phone=caller_phone, interaction_id=interaction_id)
            logger.info(f"📊 Call extractor initialized and started (capturing all messages)")
            logger.info(f"⏱️ Call start time recorded: {call_extractor.started_at}")

            # Start audio recording if enabled
            if audiobuffer:
                await audiobuffer.start_recording()
                logger.info("🎙️ Audio recording started")

            # Initialize flow manager
            try:
                await initialize_flow_manager(flow_manager, start_node)
                logger.success(f"✅ Flow initialized with {start_node} node")
            except Exception as e:
                logger.error(f"Error during flow initialization: {e}")

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport_obj, ws):
            logger.info(f"🔌 Healthcare Flow Client disconnected: {session_id}")

            # Extract and store ALL call data to Supabase (unified storage)
            try:
                current_agent = flow_manager.state.get("current_agent", "unknown")
                logger.info(f"📊 Extracting call data for session: {session_id} | Agent: {current_agent}")

                # ✅ UNIFIED: All calls go to Supabase via call_data_extractor
                logger.info("💾 Saving call data to Supabase (unified storage)")

                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor:
                    # Save recordings if enabled (BEFORE call_extractor.save_to_database)
                    if recording_manager and audiobuffer:
                        try:
                            # Reset event before stopping
                            if audio_data_received:
                                audio_data_received.clear()

                            # Stop recording - triggers on_track_audio_data event
                            await audiobuffer.stop_recording()

                            # CRITICAL: Wait for async event handler to complete
                            if audio_data_received:
                                try:
                                    await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    logger.warning("🎙️ Timeout waiting for audio data (no audio captured?)")

                            recording_urls = await recording_manager.save_recordings()
                            if recording_urls:
                                call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                                call_extractor.recording_url_user = recording_urls.get("user_url")
                                call_extractor.recording_url_bot = recording_urls.get("bot_url")
                                call_extractor.recording_duration = recording_manager.get_duration_seconds()
                                logger.success(f"🎙️ Recordings saved ({call_extractor.recording_duration:.1f}s)")
                        except Exception as e:
                            logger.error(f"❌ Failed to save recordings: {e}")

                    # ✅ CRITICAL: Mark call end time before saving
                    call_extractor.end_call()
                    success = await call_extractor.save_to_database(flow_manager.state)
                    if success:
                        logger.success(f"✅ Call data saved to Supabase for session: {session_id}")

                        # Report to Talkdesk (only if not transferred to human operator)
                        await report_to_talkdesk(flow_manager, call_extractor)
                    else:
                        logger.error(f"❌ Failed to save call data to Supabase: {session_id}")
                else:
                    logger.error("❌ No call_extractor found in flow_manager.state")

            except Exception as e:
                logger.error(f"❌ Error during call data extraction: {e}")
                import traceback
                traceback.print_exc()

            # Set input/output on conversation span BEFORE task.cancel() closes it
            try:
                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor and os.getenv("ENABLE_TRACING", "false").lower() == "true":
                    transcript = call_extractor.transcript or []
                    first_user_msg = None
                    last_assistant_msg = None
                    for entry in transcript:
                        if entry.get("role") == "user" and first_user_msg is None:
                            first_user_msg = entry.get("content", "")
                        if entry.get("role") == "assistant":
                            last_assistant_msg = entry.get("content", "")

                    conv_span = getattr(getattr(task, '_turn_trace_observer', None), '_conversation_span', None)
                    if conv_span and hasattr(conv_span, 'set_attribute'):
                        if first_user_msg:
                            conv_span.set_attribute("input.value", first_user_msg[:1000])
                        if last_assistant_msg:
                            conv_span.set_attribute("output.value", last_assistant_msg[:1000])
                        logger.info("Set input/output on conversation span")
            except Exception as e:
                logger.warning(f"Could not set conversation span attrs: {e}")

            # Clear transcript session and cleanup
            cleanup_transcript_manager(session_id)

            # Cleanup
            if session_id in active_sessions:
                del active_sessions[session_id]

            await task.cancel()

        @transport.event_handler("on_session_timeout")
        async def on_session_timeout(transport_obj, ws):
            logger.warning(f"Session timeout: {session_id}")

            # Extract and store ALL call data to Supabase (unified storage)
            try:
                current_agent = flow_manager.state.get("current_agent", "unknown")
                logger.info(f"Extracting call data for timed-out session: {session_id} | Agent: {current_agent}")

                logger.info("Saving call data to Supabase (unified storage - timeout)")

                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor:
                    # Save recordings if enabled (BEFORE call_extractor.save_to_database)
                    if recording_manager and audiobuffer:
                        try:
                            if audio_data_received:
                                audio_data_received.clear()

                            await audiobuffer.stop_recording()

                            if audio_data_received:
                                try:
                                    await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    logger.warning("Timeout waiting for audio data (timeout handler)")

                            recording_urls = await recording_manager.save_recordings()
                            if recording_urls:
                                call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                                call_extractor.recording_url_user = recording_urls.get("user_url")
                                call_extractor.recording_url_bot = recording_urls.get("bot_url")
                                call_extractor.recording_duration = recording_manager.get_duration_seconds()
                                logger.success(f"Recordings saved (timeout) ({call_extractor.recording_duration:.1f}s)")
                        except Exception as e:
                            logger.error(f"Failed to save recordings (timeout): {e}")

                    call_extractor.end_call()
                    success = await call_extractor.save_to_database(flow_manager.state)
                    if success:
                        logger.success(f"Call data saved to Supabase (timeout): {session_id}")
                        await report_to_talkdesk(flow_manager, call_extractor)
                    else:
                        logger.error(f"Failed to save call data to Supabase (timeout): {session_id}")
                else:
                    logger.error("No call_extractor found in flow_manager.state (timeout)")

            except Exception as e:
                logger.error(f"Error during timeout call data extraction: {e}")
                import traceback
                traceback.print_exc()

            # Set input/output on conversation span BEFORE task.cancel()
            try:
                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor and os.getenv("ENABLE_TRACING", "false").lower() == "true":
                    transcript = call_extractor.transcript or []
                    first_user_msg = None
                    last_assistant_msg = None
                    for entry in transcript:
                        if entry.get("role") == "user" and first_user_msg is None:
                            first_user_msg = entry.get("content", "")
                        if entry.get("role") == "assistant":
                            last_assistant_msg = entry.get("content", "")

                    conv_span = getattr(getattr(task, '_turn_trace_observer', None), '_conversation_span', None)
                    if conv_span and hasattr(conv_span, 'set_attribute'):
                        if first_user_msg:
                            conv_span.set_attribute("input.value", first_user_msg[:1000])
                        if last_assistant_msg:
                            conv_span.set_attribute("output.value", last_assistant_msg[:1000])
                        logger.info("Set input/output on conversation span (timeout)")
            except Exception as e:
                logger.warning(f"Could not set conversation span attrs (timeout): {e}")

            # Clear transcript session and cleanup
            cleanup_transcript_manager(session_id)

            # Cleanup
            if session_id in active_sessions:
                del active_sessions[session_id]

            await task.cancel()

        # START PIPELINE
        runner = PipelineRunner()

        logger.info(f"🚀 Healthcare Flow Pipeline started for session: {session_id}")
        logger.info(f"🏥 Intelligent conversation flows ACTIVE")

        # Run pipeline (blocks until disconnection)
        await runner.run(task)

    except WebSocketDisconnect:
        logger.info(f"Healthcare Flow WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"❌ Error in Healthcare Flow WebSocket handler: {e}")
        import traceback
        traceback.print_exc()

        # Record error to trace for debugging visibility
        from utils.tracing import trace_error
        trace_error(
            error=e,
            context="websocket_handler_error",
            extra_attrs={
                "session_id": session_id,
                "business_status": business_status,
                "start_node": start_node
            }
        )
    finally:
        # Extract and store ALL call data to Supabase (unified storage)
        # DUPLICATE LOGIC: Also in event handlers, but MUST be in finally block too
        # because event handlers don't always fire (e.g., escalation transfers)
        try:
            current_agent = flow_manager.state.get("current_agent", "unknown")
            logger.info(f"📊 [FINALLY BLOCK] Extracting call data for session: {session_id} | Agent: {current_agent}")

            # ✅ UNIFIED: All calls go to Supabase via call_data_extractor
            logger.info("💾 [FINALLY BLOCK] Saving call data to Supabase (unified storage)")

            call_extractor = flow_manager.state.get("call_extractor")
            if call_extractor:
                # Query Phoenix for token usage + set call metadata as span attributes
                usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tts_characters": 0}
                if os.getenv("ENABLE_TRACING", "false").lower() == "true":
                    # STEP 1: Flush and get usage metrics (LLM tokens + TTS chars)
                    try:
                        await asyncio.sleep(2)
                        flush_traces()
                        await asyncio.sleep(2)  # Phoenix local — fast indexing
                        usage_data = await get_conversation_usage(session_id)
                        call_extractor.llm_token_count = usage_data["total_tokens"]
                        logger.success(f"Phoenix usage: LLM={usage_data['total_tokens']} tokens, TTS={usage_data['tts_characters']} chars")
                    except Exception as e:
                        logger.error(f"Failed to retrieve usage from Phoenix: {e}")

                    # STEP 2: Set call metadata as span attributes + log
                    try:
                        transcript = call_extractor.transcript or []
                        first_user_msg = None
                        last_assistant_msg = None
                        for entry in transcript:
                            if entry.get("role") == "user" and first_user_msg is None:
                                first_user_msg = entry.get("content", "")
                            if entry.get("role") == "assistant":
                                last_assistant_msg = entry.get("content", "")

                        flow_state = flow_manager.state or {}
                        if flow_state.get("transfer_requested"):
                            call_type = "transfer"
                        elif flow_state.get("booking_code"):
                            call_type = "booking"
                        elif flow_state.get("selected_services"):
                            call_type = "booking_started"
                        else:
                            call_type = "info"

                        caller_phone = flow_state.get("caller_phone_from_talkdesk", "")
                        duration = round(call_extractor._calculate_duration() or 0, 1)

                        # Set call metadata on conversation span (visible in Phoenix)
                        conv_span = getattr(getattr(task, '_turn_trace_observer', None), '_conversation_span', None)
                        if conv_span and hasattr(conv_span, 'set_attribute'):
                            conv_span.set_attribute("call.type", call_type)
                            conv_span.set_attribute("call.outcome", call_type)
                            conv_span.set_attribute("call.last_node", flow_state.get("current_node", "unknown"))
                            conv_span.set_attribute("call.duration_seconds", duration)
                            conv_span.set_attribute("call.total_tokens", usage_data.get("total_tokens", 0))
                            conv_span.set_attribute("call.tts_characters", usage_data.get("tts_characters", 0))

                        trace_metadata = {
                            "outcome": call_type,
                            "last_node": flow_state.get("current_node", "unknown"),
                            "node_history": flow_state.get("node_history", []),
                            "failure_count": flow_state.get("failure_tracker", {}).get("count", 0),
                            "duration_seconds": duration,
                            "stt_provider": settings.stt_provider,
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
                            session_id,
                            first_user_msg or "",
                            last_assistant_msg or "",
                            call_type=call_type,
                            caller_phone=caller_phone,
                            metadata=trace_metadata
                        )
                    except Exception as io_err:
                        logger.error(f"Failed to update trace metadata: {io_err}")

                # Save recordings if enabled (BEFORE call_extractor.save_to_database)
                if recording_manager and audiobuffer:
                    try:
                        # Reset event before stopping
                        if audio_data_received:
                            audio_data_received.clear()

                        # Stop recording - triggers on_track_audio_data event
                        await audiobuffer.stop_recording()

                        # CRITICAL: Wait for async event handler to complete
                        if audio_data_received:
                            try:
                                await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                            except asyncio.TimeoutError:
                                logger.warning("🎙️ Timeout waiting for audio data (finally handler)")

                        recording_urls = await recording_manager.save_recordings()
                        if recording_urls:
                            call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                            call_extractor.recording_url_user = recording_urls.get("user_url")
                            call_extractor.recording_url_bot = recording_urls.get("bot_url")
                            call_extractor.recording_duration = recording_manager.get_duration_seconds()
                            logger.success(f"🎙️ [FINALLY] Recordings saved ({call_extractor.recording_duration:.1f}s)")
                    except Exception as e:
                        logger.error(f"❌ [FINALLY] Failed to save recordings: {e}")

                # ✅ CRITICAL: Mark call end time before saving
                call_extractor.end_call()
                success = await call_extractor.save_to_database(flow_manager.state)
                if success:
                    logger.success(f"✅ [FINALLY BLOCK] Call data saved to Supabase for session: {session_id}")

                    # Report to Talkdesk (only if not transferred to human operator)
                    await report_to_talkdesk(flow_manager, call_extractor)
                else:
                    logger.error(f"❌ [FINALLY BLOCK] Failed to save call data to Supabase: {session_id}")
            else:
                logger.error("❌ [FINALLY BLOCK] No call_extractor found in flow_manager.state")

        except Exception as e:
            logger.error(f"❌ [FINALLY BLOCK] Error during call data extraction: {e}")
            import traceback
            traceback.print_exc()

        # Clear transcript session and cleanup
        cleanup_transcript_manager(session_id)

        # Cleanup sessions (COPIED FROM APP.PY)
        if session_id in active_sessions:
            del active_sessions[session_id]

        # STOP PER-CALL LOGGING (use session-specific logger)
        try:
            saved_log_file = session_call_logger.stop_call_logging()
            if saved_log_file:
                logger.info(f"📁 Call log saved: {saved_log_file}")
        except NameError:
            # Fallback: try to get logger from active_sessions
            try:
                if session_id in active_sessions and "call_logger" in active_sessions[session_id]:
                    saved_log_file = active_sessions[session_id]["call_logger"].stop_call_logging()
                    if saved_log_file:
                        logger.info(f"📁 Call log saved: {saved_log_file}")
            except Exception as fallback_error:
                logger.error(f"❌ Error in fallback call logging cleanup: {fallback_error}")
        except Exception as e:
            logger.error(f"❌ Error stopping call logging: {e}")

        # Flush OpenTelemetry traces to Phoenix before exit
        try:
            flush_traces()
        except Exception as e:
            logger.error(f"❌ Error flushing traces: {e}")

        logger.info(f"Healthcare Flow Session ended: {session_id}")
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    import uvicorn
    # EXACT SAME CONFIGURATION AS APP.PY
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("bot:app", host=host, port=port, reload=False)