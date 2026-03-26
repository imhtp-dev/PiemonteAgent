# Piemonte Region Agent — Direct Talkdesk + Bridge support
import os
import re
import json
import asyncio
import wave
import time
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv
from loguru import logger

# Suppress noisy framework internals
logging.getLogger("deepgram").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("pipecat.processors.frame_processor").setLevel(logging.WARNING)
logging.getLogger("pipecat.adapters").setLevel(logging.WARNING)
logging.getLogger("pipecat.processors.aggregators").setLevel(logging.WARNING)
logging.getLogger("pipecat.processors.metrics").setLevel(logging.WARNING)
logging.getLogger("pipecat.services.openai.base_llm").setLevel(logging.WARNING)
logging.getLogger("pipecat.services.llm_service").setLevel(logging.INFO)
logging.getLogger("pipecat.pipeline").setLevel(logging.WARNING)
logging.getLogger("pipecat.utils.tracing").setLevel(logging.WARNING)
logging.getLogger("pipecat_flows.manager").setLevel(logging.INFO)
logging.getLogger("pipecat_flows.actions").setLevel(logging.WARNING)
logging.getLogger("pipecat_flows.adapters").setLevel(logging.WARNING)

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Core Pipecat imports
from pipecat.frames.frames import (
    TranscriptionFrame,
    InterimTranscriptionFrame,
    TTSSpeakFrame,
    LLMMessagesFrame,
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
from serializers.talkdesk import TalkdeskFrameSerializer, TalkdeskControlFrame, TalkdeskControlAction

# Import flow management
from flows.manager import create_flow_manager, initialize_flow_manager

from config.settings import settings
from services.config import config
from pipeline.components import create_stt_service, create_tts_service, create_llm_service, create_context_aggregator

# Import transcript manager for conversation recording and call data extraction
from services.transcript_manager import get_transcript_manager, cleanup_transcript_manager

# Import idle handler (replaces deprecated UserIdleProcessor)
from services.idle_handler import IdleHandler, DEFAULT_IDLE_TIMEOUT

load_dotenv(override=True)

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

        # Get queue_code from analysis (new IVR routing)
        queue_code = analysis.get("queue_code", analysis.get("service", "2|2|5"))

        # Build Talkdesk payload
        call_data = {
            "interaction_id": interaction_id,
            "sentiment": analysis.get("sentiment", "neutral"),
            "service": queue_code,
            "summary": analysis.get("summary", "")[:250],  # Max 250 chars
            "duration_seconds": int(call_extractor._calculate_duration() or 0)
        }

        logger.info("=" * 60)
        logger.info("📊 TALKDESK REPORT PAYLOAD:")
        logger.info(f"   Interaction ID: {call_data['interaction_id']}")
        logger.info(f"   Sentiment: {call_data['sentiment']}")
        logger.info(f"   Service (queue): {call_data['service']}")
        logger.info(f"   Duration: {call_data['duration_seconds']}s")
        logger.info(f"   Summary: {call_data['summary']}")
        logger.info(f"   Full payload: {call_data}")
        logger.info("=" * 60)

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


# HEARTBEAT — reports active_sessions count to Supabase every 10s

_heartbeat_task: Optional[asyncio.Task] = None
_HEARTBEAT_INTERVAL = 10  # seconds


async def _heartbeat_loop():
    """Background loop: upserts container status to tb_agent_status every 10s."""
    from services.database import db

    region = os.getenv("INFO_AGENT_REGION", "Lombardia")
    instance_id = int(os.getenv("INSTANCE_ID", "1"))
    max_capacity = settings.max_concurrent_calls

    logger.info(f"💓 Heartbeat started: region={region} instance={instance_id} capacity={max_capacity}")

    while True:
        try:
            current_calls = len(active_sessions)
            await db.upsert_agent_status(region, instance_id, current_calls, max_capacity)
            await db.update_daily_peak(region, current_calls)
        except Exception as e:
            logger.warning(f"⚠️ Heartbeat error: {e}")
        await asyncio.sleep(_HEARTBEAT_INTERVAL)


async def _set_offline():
    """Mark this container as offline on shutdown."""
    try:
        from services.database import db
        region = os.getenv("INFO_AGENT_REGION", "Lombardia")
        instance_id = int(os.getenv("INSTANCE_ID", "1"))
        await db.upsert_agent_status(region, instance_id, 0, settings.max_concurrent_calls, "offline")
        logger.info("💔 Heartbeat: marked offline")
    except Exception:
        pass


# LIFESPAN CONTEXT MANAGER

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    global _heartbeat_task

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

    # Start heartbeat background task
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())

    yield

    # Shutdown
    logger.info("🛑 Shutting down Healthcare Flow Bot...")

    # Stop heartbeat and mark offline
    if _heartbeat_task:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass
    await _set_offline()

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
                    <li><code>WS /talkdesk</code> - Talkdesk direct WebSocket</li>
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
            "transport": "talkdesk-direct"
        }
    })


# ============================================================================
# TALKDESK DIRECT ENDPOINT — No bridge needed
# ============================================================================

def extract_business_status_and_ivr(business_hours_string: str) -> tuple:
    """Parse Talkdesk business_hours string: 'uuid::region::ivr_path::status'
    Example: 'ea40a17d...::Piemonte::1|3|2::Open' → ('open', '1|3|2')
    Fallback: ('close', '') if unparseable.
    """
    try:
        if business_hours_string and '::' in business_hours_string:
            parts = business_hours_string.split('::')
            if len(parts) >= 4:
                raw_status = parts[-1].strip().lower()
                status = "after_hours" if raw_status == "afterhours" else raw_status
                ivr_path = parts[2].strip() if len(parts) > 2 else ""
                return status, ivr_path
    except Exception as e:
        logger.error(f"Error parsing business_hours: {e}")
    return "close", ""


@app.websocket("/talkdesk")
async def talkdesk_endpoint(websocket: WebSocket):
    """
    Direct Talkdesk WebSocket endpoint — no bridge needed.
    Talkdesk connects directly with mu-law 8kHz JSON protocol.
    TalkdeskFrameSerializer handles audio conversion + protocol.
    """
    await websocket.accept()

    # ── 1. Wait for Talkdesk start event ──
    # Talkdesk may send a "connected" event before "start" — keep reading until we get "start"
    start_data = None
    try:
        for _ in range(10):  # Max 10 messages before giving up
            msg = await websocket.receive_text()
            data = json.loads(msg)
            logger.info(f"📦 Talkdesk message: event={data.get('event')} keys={list(data.keys())}")

            if data.get("event") == "start":
                start_data = data
                logger.info(f"📦 Talkdesk START event received: {json.dumps(data)[:2000]}")
                break
            else:
                logger.info(f"⏭️ Skipping non-start event: {data.get('event')}")
    except Exception as e:
        logger.error(f"❌ Failed to read Talkdesk start event: {e}")
        return

    if not start_data:
        logger.error("❌ Never received start event from Talkdesk — rejecting")
        await websocket.close()
        return

    # ── 2. Extract metadata from start event ──
    stream_sid = start_data.get("streamSid") or start_data.get("start", {}).get("streamSid")
    if not stream_sid:
        logger.error(f"❌ No streamSid in start event — rejecting. Full event: {json.dumps(start_data)[:500]}")
        await websocket.close()
        return

    custom_params = start_data.get("start", {}).get("customParameters", {})
    business_hours = custom_params.get("business_hours", "")
    caller_phone = custom_params.get("caller_id", "")
    interaction_id = custom_params.get("interaction_id", "")

    # ── 3. Parse business status + IVR path ──
    business_status, ivr_path = extract_business_status_and_ivr(business_hours)

    import uuid
    session_id = str(uuid.uuid4())  # Full UUID — required for Supabase tb_stat.call_id

    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"New DIRECT Talkdesk Connection (no bridge)")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Stream SID: {stream_sid}")
    logger.info(f"Business Status: {business_status}")
    logger.info(f"Caller Phone: {caller_phone or 'Not provided'}")
    logger.info(f"Interaction ID: {interaction_id or 'Not provided'}")
    logger.info(f"IVR Path: {ivr_path or 'Not provided'}")
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Variables for pipeline (pre-declare to avoid NameError in finally block)
    runner = None
    task = None
    flow_manager = None
    session_call_logger = None
    start_node = "router"

    try:
        # Check required API keys
        for key_name, service_name in [("DEEPGRAM_API_KEY", "Deepgram"), ("ELEVENLABS_API_KEY", "ElevenLabs"), ("OPENAI_API_KEY", "OpenAI")]:
            if not os.getenv(key_name):
                raise Exception(f"{key_name} not found - required for {service_name}")

        config.validate()

        # ── 4. Create transport with TalkdeskFrameSerializer ──
        vad_stop_secs = 0.2 if settings.smart_turn_enabled else settings.vad_config["stop_secs"]

        serializer = TalkdeskFrameSerializer(stream_sid=stream_sid)

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
                serializer=serializer,
                session_timeout=900,
            )
        )

        # ── 5. Create services (identical to /ws) ──
        stt = create_stt_service()
        tts = create_tts_service()
        llm = create_llm_service()
        context_aggregator, node_mute_strategy = create_context_aggregator(
            llm,
            smart_turn_enabled=settings.smart_turn_enabled,
            user_idle_timeout=DEFAULT_IDLE_TIMEOUT,
        )
        transcript_processor = TranscriptProcessor()

        from services.processing_time_tracker import create_processing_time_tracker
        processing_tracker = create_processing_time_tracker()

        # Audio recording
        recording_enabled = os.getenv("RECORDING_ENABLED", "false").lower() == "true"
        recording_manager = None
        audiobuffer = None
        audio_data_received = None

        if recording_enabled:
            from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
            from services.recording_manager import RecordingManager

            recording_manager = RecordingManager(session_id)
            audio_data_received = asyncio.Event()

            audiobuffer = AudioBufferProcessor(
                sample_rate=16000,
                num_channels=2,
                buffer_size=0,
            )

            @audiobuffer.event_handler("on_track_audio_data")
            async def on_track_audio_data(buffer, user_audio, bot_audio, sample_rate, num_channels):
                recording_manager.add_user_audio(user_audio)
                recording_manager.add_bot_audio(bot_audio)
                audio_data_received.set()

        # ── 6. Create pipeline (idle detection built into aggregator) ──
        pipeline_components = [
            transport.input(),
            stt,
            transcript_processor.user(),
            context_aggregator.user(),
            llm,
            processing_tracker,
            tts,
            transport.output(),
        ]

        if audiobuffer:
            pipeline_components.append(audiobuffer)

        pipeline_components.extend([
            transcript_processor.assistant(),
            context_aggregator.assistant()
        ])

        pipeline = Pipeline(pipeline_components)

        # Call logging
        from services.call_logger import CallLogger
        session_call_logger = CallLogger(session_id)
        log_file = session_call_logger.start_call_logging(session_id, caller_phone)

        # Pipeline task
        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_transcriptions=True,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=16000,
                enable_usage_metrics=True,
                enable_metrics=True,
            ),
            enable_tracing=True,
            conversation_id=session_id,
            additional_span_attributes={
                "session.id": session_id,
                "user.id": caller_phone or "unknown",
            },
            idle_timeout_secs=600
        )

        # ── 7. Flow manager setup ──
        flow_manager = create_flow_manager(task, llm, context_aggregator, transport)

        flow_manager.state["tts_service"] = tts
        node_mute_strategy.set_flow_state(flow_manager.state)
        node_mute_strategy.set_flow_manager(flow_manager)

        # Setup idle detection (built into aggregator)
        td_idle_handler = IdleHandler()
        td_user_aggregator = context_aggregator.user()

        @td_user_aggregator.event_handler("on_user_turn_idle")
        async def on_td_user_turn_idle(aggregator):
            await td_idle_handler.handle_idle(aggregator)

        @td_user_aggregator.event_handler("on_user_turn_started")
        async def on_td_user_turn_started(aggregator, strategy):
            td_idle_handler.reset()

        # Store ALL metadata in state (same keys as /ws)
        flow_manager.state["business_status"] = business_status
        flow_manager.state["session_id"] = session_id
        flow_manager.state["stream_sid"] = stream_sid
        flow_manager.state["interaction_id"] = interaction_id
        flow_manager.state["ivr_path"] = ivr_path
        flow_manager.state["is_talkdesk_direct"] = True  # NEW — for escalation routing
        flow_manager.state["transport"] = transport  # NEW — for sending TalkdeskControlFrame

        if caller_phone:
            flow_manager.state["caller_phone_from_talkdesk"] = caller_phone
            try:
                from services.call_storage import CallDataStorage
                storage = CallDataStorage()
                await storage.store_caller_phone(session_id, caller_phone)
            except Exception as e:
                logger.error(f"❌ Failed to store caller phone in Azure: {e}")

        # Transcript recording handler
        @transcript_processor.event_handler("on_transcript_update")
        async def on_transcript_update(processor, frame):
            session_transcript_manager = get_transcript_manager(session_id)
            for message in frame.messages:
                if message.role == "user":
                    session_transcript_manager.add_user_message(message.content)
                elif message.role == "assistant":
                    session_transcript_manager.add_assistant_message(message.content)
                call_extractor_instance = flow_manager.state.get("call_extractor")
                if call_extractor_instance:
                    call_extractor_instance.add_transcript_entry(message.role, message.content)

        # ── 8. Event handlers ──
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport_obj, ws):
            logger.info(f"✅ Talkdesk Direct Client connected: {session_id}")
            active_sessions[session_id] = {
                "websocket": ws,
                "business_status": business_status,
                "connected_at": asyncio.get_event_loop().time(),
                "call_logger": session_call_logger,
                "transport": "talkdesk_direct",
            }

            session_transcript_manager = get_transcript_manager(session_id)
            session_transcript_manager.start_session(session_id)

            from services.call_data_extractor import get_call_extractor
            call_extractor = get_call_extractor(session_id)
            call_extractor.call_id = session_id
            call_extractor.interaction_id = interaction_id
            flow_manager.state["call_extractor"] = call_extractor

            call_extractor.start_call(caller_phone=caller_phone, interaction_id=interaction_id)

            # INSERT initial Supabase row (replaces bridge's save_call_to_supabase)
            await call_extractor.insert_initial_row()

            # Increment daily total calls for monitoring dashboard
            try:
                from services.database import db
                region = os.getenv("INFO_AGENT_REGION", "Lombardia")
                await db.increment_daily_total_calls(region)
            except Exception:
                pass

            if audiobuffer:
                await audiobuffer.start_recording()

            try:
                await initialize_flow_manager(flow_manager, start_node)
                logger.success(f"✅ Flow initialized with {start_node} node")
            except Exception as e:
                logger.error(f"Error during flow initialization: {e}")

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport_obj, ws):
            logger.info(f"🔌 Talkdesk Direct Client disconnected: {session_id}")

            try:
                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor:
                    if recording_manager and audiobuffer:
                        try:
                            if audio_data_received:
                                audio_data_received.clear()
                            await audiobuffer.stop_recording()
                            if audio_data_received:
                                try:
                                    await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    pass
                            recording_urls = await recording_manager.save_recordings()
                            if recording_urls:
                                call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                                call_extractor.recording_url_user = recording_urls.get("user_url")
                                call_extractor.recording_url_bot = recording_urls.get("bot_url")
                                call_extractor.recording_duration = recording_manager.get_duration_seconds()
                        except Exception as e:
                            logger.error(f"❌ Failed to save recordings: {e}")

                    call_extractor.end_call()
                    success = await call_extractor.save_to_database(flow_manager.state)
                    if success:
                        logger.success(f"✅ Call data saved to Supabase: {session_id}")
                        await report_to_talkdesk(flow_manager, call_extractor)
                    else:
                        logger.error(f"❌ Failed to save call data: {session_id}")

            except Exception as e:
                logger.error(f"❌ Error during disconnect cleanup: {e}")
                import traceback
                traceback.print_exc()

            # Set trace input/output
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
            except Exception:
                pass

            cleanup_transcript_manager(session_id)
            if session_id in active_sessions:
                del active_sessions[session_id]
            await task.cancel()

        @transport.event_handler("on_session_timeout")
        async def on_session_timeout(transport_obj, ws):
            logger.warning(f"Session timeout (Talkdesk direct): {session_id}")

            try:
                call_extractor = flow_manager.state.get("call_extractor")
                if call_extractor:
                    if recording_manager and audiobuffer:
                        try:
                            if audio_data_received:
                                audio_data_received.clear()
                            await audiobuffer.stop_recording()
                            if audio_data_received:
                                try:
                                    await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    pass
                            recording_urls = await recording_manager.save_recordings()
                            if recording_urls:
                                call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                                call_extractor.recording_url_user = recording_urls.get("user_url")
                                call_extractor.recording_url_bot = recording_urls.get("bot_url")
                                call_extractor.recording_duration = recording_manager.get_duration_seconds()
                        except Exception as e:
                            logger.error(f"❌ Failed to save recordings (timeout): {e}")

                    call_extractor.end_call()
                    success = await call_extractor.save_to_database(flow_manager.state)
                    if success:
                        await report_to_talkdesk(flow_manager, call_extractor)

            except Exception as e:
                logger.error(f"❌ Error during timeout cleanup: {e}")

            cleanup_transcript_manager(session_id)
            if session_id in active_sessions:
                del active_sessions[session_id]
            await task.cancel()

        # ── 9. Run pipeline ──
        runner = PipelineRunner()
        logger.info(f"🚀 Talkdesk Direct Pipeline started: {session_id}")
        await runner.run(task)

    except WebSocketDisconnect:
        logger.info(f"Talkdesk Direct WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"❌ Error in Talkdesk Direct handler: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Finally block — third save layer (safety net)
        try:
            if not flow_manager:
                logger.warning("⚠️ flow_manager not initialized — skipping finally cleanup")
                return
            call_extractor = flow_manager.state.get("call_extractor")
            if call_extractor:
                # Phoenix usage metrics
                usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tts_characters": 0}
                if os.getenv("ENABLE_TRACING", "false").lower() == "true":
                    try:
                        await asyncio.sleep(2)
                        flush_traces()
                        await asyncio.sleep(2)
                        usage_data = await get_conversation_usage(session_id)
                        call_extractor.llm_token_count = usage_data["total_tokens"]
                    except Exception as e:
                        logger.error(f"Failed to retrieve usage from Phoenix: {e}")

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

                        caller_phone_final = flow_state.get("caller_phone_from_talkdesk", "")
                        duration = round(call_extractor._calculate_duration() or 0, 1)

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
                        except Exception:
                            pass

                        await update_trace_metadata(
                            session_id,
                            first_user_msg or "",
                            last_assistant_msg or "",
                            call_type=call_type,
                            caller_phone=caller_phone_final,
                            metadata=trace_metadata
                        )
                    except Exception:
                        pass

                # Save recordings
                if recording_manager and audiobuffer:
                    try:
                        if audio_data_received:
                            audio_data_received.clear()
                        await audiobuffer.stop_recording()
                        if audio_data_received:
                            try:
                                await asyncio.wait_for(audio_data_received.wait(), timeout=2.0)
                            except asyncio.TimeoutError:
                                pass
                        recording_urls = await recording_manager.save_recordings()
                        if recording_urls:
                            call_extractor.recording_url_stereo = recording_urls.get("stereo_url")
                            call_extractor.recording_url_user = recording_urls.get("user_url")
                            call_extractor.recording_url_bot = recording_urls.get("bot_url")
                            call_extractor.recording_duration = recording_manager.get_duration_seconds()
                    except Exception:
                        pass

                call_extractor.end_call()
                success = await call_extractor.save_to_database(flow_manager.state)
                if success:
                    logger.success(f"✅ [FINALLY] Call data saved: {session_id}")
                    await report_to_talkdesk(flow_manager, call_extractor)

        except Exception as e:
            logger.error(f"❌ [FINALLY] Error: {e}")

        cleanup_transcript_manager(session_id)
        if session_id in active_sessions:
            del active_sessions[session_id]

        try:
            saved_log_file = session_call_logger.stop_call_logging()
            if saved_log_file:
                logger.info(f"📁 Call log saved: {saved_log_file}")
        except Exception:
            pass

        try:
            flush_traces()
        except Exception:
            pass

        logger.info(f"Talkdesk Direct Session ended: {session_id}")
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    import uvicorn
    # EXACT SAME CONFIGURATION AS APP.PY
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("bot:app", host=host, port=port, reload=False)