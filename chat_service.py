"""
Chat Service for Dashboard Integration
========================================

WebSocket-based chat service that replicates the full voice agent behavior
in text mode. Uses hardcoded test data and the router node for full agent functionality.

Features:
- Full Pipecat pipeline (same as voice agent)
- Router node (booking OR info agent behavior)
- Database saving (updates existing row by session_id)
- Transcript tracking
- Token usage metrics
- Shared state for concurrent users
- Real-time function call broadcasting

Usage:
    Local:      python chat_service.py
    Production: docker-compose up -d chat-service
    Test:       wscat -c ws://localhost:8002/ws

Configuration (Hardcoded for Testing):
- Session ID: 49b78a42-9024-4646-95e2-d2d6f4f8a17b
- Phone: +39 333 331 9326
- DOB: 1979-06-19
- Start Node: router (full agent)
- Port: 8002
"""

import os
import sys
import asyncio
import uuid
from typing import Optional, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from loguru import logger

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Core Pipecat imports
from pipecat.frames.frames import (
    Frame,
    TextFrame,
    TranscriptionFrame,
    LLMMessagesFrame,
    LLMFullResponseEndFrame,
    EndFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    MetricsFrame,
    FunctionCallResultFrame
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.observers.base_observer import BaseObserver
from pipecat.metrics.metrics import LLMUsageMetricsData

# Import existing components and flows
from config.settings import settings
from services.config import config
from pipeline.components import create_llm_service, create_context_aggregator
from flows.manager import initialize_flow_manager
from services.transcript_manager import get_transcript_manager, cleanup_transcript_manager

# Load environment variables
load_dotenv(override=True)

# ============================================================================
# HARDCODED CONFIGURATION (For Testing)
# ============================================================================
FIXED_SESSION_ID = "49b78a42-9024-4646-95e2-d2d6f4f8a17b"
FIXED_CALLER_PHONE = "+39 333 331 9326"
FIXED_PATIENT_DOB = "1979-06-19"
FIXED_START_NODE = "router"  # Full agent behavior


# ============================================================================
# CUSTOM PROCESSORS (Copied from chat_test.py)
# ============================================================================

class TextInputProcessor(FrameProcessor):
    """
    Processor that converts incoming text messages to TextFrame
    and adds them to the conversation context
    """

    def __init__(self):
        super().__init__()
        logger.info("💬 TextInputProcessor initialized")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames"""
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class TextOutputProcessor(FrameProcessor):
    """
    Processor that captures LLM text output and sends to WebSocket
    Also records transcript for both transcript_manager and call_extractor
    """

    def __init__(self, websockets: list, session_id: str, flow_manager=None):
        super().__init__()
        self.websockets = websockets  # List of connected websockets
        self.session_id = session_id
        self.flow_manager = flow_manager
        self._buffer = ""
        logger.info("💬 TextOutputProcessor initialized")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process outgoing frames and send text to WebSocket"""
        await super().process_frame(frame, direction)

        # Intercept function calls and broadcast to WebSocket clients
        if isinstance(frame, FunctionCallResultFrame):
            function_name = frame.function_name if hasattr(frame, 'function_name') else "unknown"

            function_data = {
                "type": "function_called",
                "function_name": function_name,
                "timestamp": datetime.now().isoformat()
            }

            # Broadcast to all connected websockets
            for ws in self.websockets[:]:
                try:
                    await ws.send_json(function_data)
                    logger.info(f"📤 Sent function call: {function_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to send function call: {e}")
                    if ws in self.websockets:
                        self.websockets.remove(ws)

        # ONLY capture text going DOWNSTREAM (from LLM to output)
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text
            self._buffer += text

            # Send partial response to ALL connected WebSockets (broadcast)
            for ws in self.websockets[:]:  # Copy list to avoid modification during iteration
                try:
                    await ws.send_json({
                        "type": "assistant_message_chunk",
                        "text": text
                    })
                    logger.debug(f"📤 Broadcast text chunk: {text[:50]}...")
                except Exception as e:
                    logger.error(f"❌ Failed to send chunk to websocket: {e}")
                    # Remove dead websocket
                    if ws in self.websockets:
                        self.websockets.remove(ws)

        # When LLM finishes, send complete message and record in transcript
        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)) and self._buffer:
            # Broadcast complete message to all websockets
            for ws in self.websockets[:]:
                try:
                    await ws.send_json({
                        "type": "assistant_message_complete",
                        "text": self._buffer
                    })
                    logger.info(f"✅ Broadcast complete message: {self._buffer[:100]}...")
                except Exception as e:
                    logger.error(f"❌ Failed to send complete message: {e}")
                    if ws in self.websockets:
                        self.websockets.remove(ws)

            # Record assistant message in transcript_manager
            session_transcript_manager = get_transcript_manager(self.session_id)
            session_transcript_manager.add_assistant_message(self._buffer)

            # ALSO add to call_extractor
            if self.flow_manager:
                call_extractor_instance = self.flow_manager.state.get("call_extractor")
                if call_extractor_instance:
                    call_extractor_instance.add_transcript_entry("assistant", self._buffer)
                    logger.debug(f"📊 Added to call_extractor: assistant")

            self._buffer = ""

        await self.push_frame(frame, direction)


class TextTransportSimulator(FrameProcessor):
    """
    Simulates a transport layer for text-only communication
    Acts as both input and output processor
    """

    def __init__(self):
        super().__init__()
        self._running = True
        self._started = False
        self._message_queue = asyncio.Queue()
        logger.info("🔌 TextTransportSimulator initialized")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames in both directions"""
        await super().process_frame(frame, direction)

        # Mark as started when we receive StartFrame
        if isinstance(frame, StartFrame):
            self._started = True
            logger.info("✅ TextTransportSimulator received StartFrame")
            asyncio.create_task(self._process_message_queue())

        await self.push_frame(frame, direction)

    async def _process_message_queue(self):
        """Process messages from the queue after pipeline has started"""
        while self._running:
            try:
                text = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                if text:
                    logger.info(f"📥 Processing queued message: {text}")

                    # Use TranscriptionFrame (like STT does)
                    transcription_frame = TranscriptionFrame(text=text, user_id="user", timestamp=0)
                    await self.push_frame(transcription_frame)

                    # Notify user started/stopped speaking
                    await self.push_frame(UserStartedSpeakingFrame())
                    await self.push_frame(UserStoppedSpeakingFrame())

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ Error processing message: {e}")

    async def receive_text_message(self, text: str):
        """Receive text message from WebSocket and queue it"""
        logger.info(f"📨 Queueing user message: {text}")
        await self._message_queue.put(text)

    def stop(self):
        """Stop the transport"""
        self._running = False


class LLMUsageMetricsObserver(BaseObserver):
    """Observer to capture LLM token usage metrics"""

    def __init__(self, flow_manager):
        super().__init__()
        self.flow_manager = flow_manager
        logger.info("🔢 LLMUsageMetricsObserver initialized")

    async def on_push_frame(
        self,
        src: FrameProcessor,
        dst: FrameProcessor,
        frame: Frame,
        direction: FrameDirection,
        timestamp: int,
    ):
        """Capture LLM usage metrics"""
        if isinstance(frame, MetricsFrame):
            for metric in frame.data:
                if isinstance(metric, LLMUsageMetricsData):
                    usage = metric.value
                    total_tokens = usage.total_tokens

                    logger.info(f"🔢 LLM Token Usage:")
                    logger.info(f"   Prompt: {usage.prompt_tokens}")
                    logger.info(f"   Completion: {usage.completion_tokens}")
                    logger.info(f"   Total: {total_tokens}")

                    # Increment call_extractor token count
                    call_extractor = self.flow_manager.state.get("call_extractor")
                    if call_extractor:
                        call_extractor.increment_tokens(total_tokens)
                        logger.info(f"✅ Updated call_extractor tokens: {call_extractor.llm_token_count}")


# ============================================================================
# FASTAPI APP SETUP
# ============================================================================

# Store for active sessions (shared state model)
active_sessions: Dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    logger.info("🚀 Starting Chat Service for Dashboard Integration...")
    logger.info(f"   Session ID (fixed): {FIXED_SESSION_ID}")
    logger.info(f"   Phone (fixed): {FIXED_CALLER_PHONE}")
    logger.info(f"   DOB (fixed): {FIXED_PATIENT_DOB}")
    logger.info(f"   Start Node: {FIXED_START_NODE}")

    # Initialize database connection
    try:
        from services.database import db
        await db.connect()
        logger.success("✅ Supabase database initialized")
    except Exception as e:
        logger.warning(f"⚠️ Database initialization failed: {e}")

    yield

    # Shutdown
    logger.info("🛑 Shutting down Chat Service...")
    try:
        from services.database import db
        await db.close()
        logger.info("✅ Database connection closed")
    except Exception as e:
        logger.error(f"❌ Database shutdown error: {e}")


# Create FastAPI app
app = FastAPI(
    title="Chat Service - Dashboard Integration",
    description="WebSocket-based chat service with full agent behavior",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Service info page"""
    return HTMLResponse(f"""
    <html>
        <head>
            <title>Chat Service - Dashboard Integration</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }}
                .container {{
                    background: rgba(255,255,255,0.95);
                    color: #333;
                    padding: 30px;
                    border-radius: 10px;
                    max-width: 800px;
                    margin: 0 auto;
                }}
                .status {{ color: #22c55e; font-weight: bold; }}
                code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>💬 Chat Service</h1>
                <p class="status">✅ WebSocket chat service is running</p>

                <h2>Configuration:</h2>
                <ul>
                    <li><strong>Session ID:</strong> <code>{FIXED_SESSION_ID}</code></li>
                    <li><strong>Phone:</strong> <code>{FIXED_CALLER_PHONE}</code></li>
                    <li><strong>DOB:</strong> <code>{FIXED_PATIENT_DOB}</code></li>
                    <li><strong>Start Node:</strong> <code>{FIXED_START_NODE}</code></li>
                    <li><strong>Port:</strong> <code>8002</code></li>
                </ul>

                <h2>API Endpoints:</h2>
                <ul>
                    <li><code>GET /</code> - This page</li>
                    <li><code>GET /health</code> - Health check</li>
                    <li><code>POST /api/create-session</code> - Get session info</li>
                    <li><code>WS /ws</code> - WebSocket chat endpoint</li>
                </ul>

                <h2>Active Sessions:</h2>
                <p>{len(active_sessions)} session(s) active</p>

                <h2>Test Connection:</h2>
                <p><code>wscat -c ws://localhost:8002/ws</code></p>
            </div>
        </body>
    </html>
    """)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "service": "chat-service",
        "version": "1.0.0",
        "active_sessions": len(active_sessions),
        "active_connections": sum(len(s.get("websockets", [])) for s in active_sessions.values()),
        "fixed_session_id": FIXED_SESSION_ID,
        "start_node": FIXED_START_NODE
    })


@app.post("/api/create-session")
async def create_session():
    """Create a new unique session for each connection"""
    port = os.getenv("CHAT_SERVICE_PORT", "8002")
    host = os.getenv("CHAT_SERVICE_HOST", "localhost")

    # Generate a unique session ID for each new connection
    new_session_id = str(uuid.uuid4())

    logger.info(f"🆕 Creating new session: {new_session_id}")

    return {
        "session_id": new_session_id,
        "websocket_url": f"ws://{host}:{port}/ws",
        "phone": FIXED_CALLER_PHONE,
        "dob": FIXED_PATIENT_DOB,
        "start_node": FIXED_START_NODE,
        "created_at": datetime.now().isoformat(),
        "status": "ready"
    }


@app.get("/api/session/info")
async def get_session_info():
    """Get active session information"""
    if FIXED_SESSION_ID not in active_sessions:
        return {"status": "no_active_session"}

    session = active_sessions[FIXED_SESSION_ID]
    return {
        "session_id": FIXED_SESSION_ID,
        "active_connections": len(session.get("websockets", [])),
        "created_at": session.get("created_at"),
        "message_count": session.get("message_count", 0),
        "status": "active"
    }


# ============================================================================
# WEBSOCKET ENDPOINT (Main Chat Logic)
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for chat
    Replicates full voice agent behavior in text mode
    """
    await websocket.accept()

    session_id = FIXED_SESSION_ID
    caller_phone = FIXED_CALLER_PHONE
    patient_dob = FIXED_PATIENT_DOB
    start_node = FIXED_START_NODE

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("🎯 New Chat Service Connection")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Caller Phone: {caller_phone}")
    logger.info(f"Patient DOB: {patient_dob}")
    logger.info(f"Start Node: {start_node}")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    runner = None
    task = None
    is_first_connection = False

    try:
        # Check if session already exists (shared state model)
        if session_id not in active_sessions:
            is_first_connection = True
            logger.info("📝 First connection - creating shared session")

            # ===== CREATE SHARED SESSION =====

            # Create AI services
            llm = create_llm_service()
            context_aggregator = create_context_aggregator(llm)

            # Initialize transcript manager
            transcript_manager = get_transcript_manager(session_id)

            # Initialize call_extractor (info_agent version for Lombardy)
            from services.call_data_extractor import get_call_extractor
            call_extractor = get_call_extractor(session_id)
            call_extractor.start_call(
                caller_phone=caller_phone,
                interaction_id=f"dashboard-chat-{uuid.uuid4().hex[:8]}"
            )
            call_extractor.call_id = session_id  # Use fixed session ID

            # Create processors (text_output will be updated with flow_manager later)
            text_output = TextOutputProcessor([], session_id, None)  # flow_manager set later
            transport = TextTransportSimulator()

            # Create pipeline (match chat_test.py structure exactly)
            pipeline = Pipeline([
                transport,
                context_aggregator.user(),
                llm,
                text_output,
                context_aggregator.assistant()
            ])

            logger.info("Text Chat Pipeline structure (matches chat_test.py):")
            logger.info("  1. TextTransportSimulator (WebSocket text input)")
            logger.info("  2. Context Aggregator (User)")
            logger.info("  3. OpenAI LLM (with flows)")
            logger.info("  4. TextOutputProcessor (WebSocket text output)")
            logger.info("  5. Context Aggregator (Assistant)")

            # Create pipeline task with metrics
            task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    allow_interruptions=True,
                    enable_metrics=True,
                    enable_usage_metrics=True
                )
            )

            # Create flow manager AFTER task with global functions
            from pipecat_flows import FlowManager
            from flows.global_functions import GLOBAL_FUNCTIONS
            flow_manager = FlowManager(
                task=task,
                llm=llm,
                context_aggregator=context_aggregator,
                transport=None,  # No transport for text-only
                global_functions=GLOBAL_FUNCTIONS,  # 8 global functions
            )

            # Set caller phone and DOB in flow state
            flow_manager.state["caller_phone_from_talkdesk"] = caller_phone
            flow_manager.state["patient_dob"] = patient_dob
            flow_manager.state["call_extractor"] = call_extractor
            flow_manager.state["business_status"] = "close"  # Set to close for testing
            flow_manager.state["session_id"] = session_id
            flow_manager.state["stream_sid"] = ""  # Empty for chat (no Talkdesk)

            # Update text_output with flow_manager
            text_output.flow_manager = flow_manager

            # Add LLM usage metrics observer
            metrics_observer = LLMUsageMetricsObserver(flow_manager)
            task.add_observer(metrics_observer)

            logger.info("✅ Pipeline task created with metrics observer")

            # Initialize flow manager with router node
            await initialize_flow_manager(flow_manager, start_node)
            logger.success(f"✅ Flow initialized with {start_node} node")

            # Run pipeline
            runner = PipelineRunner()

            # Start pipeline in background
            pipeline_task = asyncio.create_task(runner.run(task))

            # Store shared session
            active_sessions[session_id] = {
                "flow_manager": flow_manager,
                "call_extractor": call_extractor,
                "transcript_manager": transcript_manager,
                "runner": runner,
                "task": task,
                "transport": transport,
                "text_output": text_output,
                "websockets": [],  # List of connected websockets
                "created_at": datetime.now().isoformat(),
                "message_count": 0,
                "pipeline_task": pipeline_task
            }

            logger.success(f"✅ Shared session created: {session_id}")

        else:
            logger.info("🔄 Joining existing shared session")

        # Add this websocket to shared session
        session = active_sessions[session_id]
        session["websockets"].append(websocket)
        session["text_output"].websockets = session["websockets"]  # Update text_output with new list

        transport = session["transport"]
        flow_manager = session["flow_manager"]

        logger.info(f"🔌 WebSocket connected ({len(session['websockets'])} total connections)")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
            "connections": len(session["websockets"]),
            "timestamp": datetime.now().isoformat()
        })

        # Listen for messages
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            user_message = data.get("message", "").strip()

            if not user_message:
                continue

            logger.info(f"💬 User: {user_message}")
            session["message_count"] += 1

            # Record user message in transcript_manager
            transcript_manager = session["transcript_manager"]
            transcript_manager.add_user_message(user_message)

            # Record in call_extractor
            call_extractor = session["call_extractor"]
            call_extractor.add_transcript_entry("user", user_message)

            # Send to pipeline via transport
            await transport.receive_text_message(user_message)

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected")

    except Exception as e:
        logger.error(f"❌ Error in WebSocket handler: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Cleanup: Remove this websocket from session
        if session_id in active_sessions:
            session = active_sessions[session_id]

            # Remove this websocket
            if websocket in session.get("websockets", []):
                session["websockets"].remove(websocket)

            remaining_connections = len(session["websockets"])
            logger.info(f"🔌 WebSocket removed ({remaining_connections} remaining)")

            # If last connection, save to database and cleanup
            if remaining_connections == 0:
                logger.info("📊 Last connection - saving to database and cleaning up")

                try:
                    # End call timing
                    call_extractor = session["call_extractor"]
                    flow_manager = session["flow_manager"]
                    call_extractor.end_call()

                    # Update flow state with functions called
                    flow_manager.state["functions_called"] = [
                        f["function_name"] for f in call_extractor.functions_called
                    ]

                    # Save to database (UPDATES existing row by session_id)
                    await call_extractor.save_to_database(flow_manager.state)
                    logger.success("✅ Call data saved to database")

                except Exception as e:
                    logger.error(f"❌ Error saving call data: {e}")

                # Cleanup
                try:
                    cleanup_transcript_manager(session_id)
                    from services.call_data_extractor import cleanup_call_extractor
                    cleanup_call_extractor(session_id)

                    # Stop pipeline
                    if "transport" in session:
                        session["transport"].stop()
                    if "task" in session:
                        await session["task"].cancel()

                    logger.info("✅ Session cleaned up")
                except Exception as e:
                    logger.error(f"❌ Cleanup error: {e}")

                # Remove session
                del active_sessions[session_id]
                logger.success(f"✅ Session deleted: {session_id}")

        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("CHAT_SERVICE_PORT", "8002"))
    host = os.getenv("CHAT_SERVICE_HOST", "0.0.0.0")

    logger.info(f"🚀 Starting Chat Service on {host}:{port}")
    logger.info(f"📞 Fixed Phone: {FIXED_CALLER_PHONE}")
    logger.info(f"📅 Fixed DOB: {FIXED_PATIENT_DOB}")
    logger.info(f"🎯 Start Node: {FIXED_START_NODE}")
    logger.info(f"📝 WebSocket: ws://{host}:{port}/ws")

    uvicorn.run(
        "chat_service:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
