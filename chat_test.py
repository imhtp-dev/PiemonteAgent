"""
Text-Based Chat Testing Interface for Healthcare Flow Agent
============================================================

This script provides a text-only chat interface for rapid testing and development
of conversation flows WITHOUT requiring voice/audio processing.

Benefits:
- Instant testing (no STT/TTS delays)
- Lower API costs (no audio processing)
- Better debugging (see exact text exchanges)
- Faster iteration during development

Usage:
    python chat_test.py                              # Start with router (unified routing - default)
    python chat_test.py --start-node greeting        # Direct to booking agent (skip router)
    python chat_test.py --start-node email           # Start with email collection
    python chat_test.py --start-node booking         # Start with booking flow
    python chat_test.py --start-node orange_box      # Start from Orange Box flow (RX Caviglia Destra)
    python chat_test.py --start-node cerba_card      # Start from Cerba Card question (auto-filled data)
    python chat_test.py --port 8081                  # Use custom port

    # Test EXISTING patient flow (simulates Talkdesk caller ID + DOB from database)
python chat_test.py --start-node booking --caller-phone +393333319326 --patient-dob 1979-06-19

    # Test with Rudy's data from database (will skip phone confirmation, no birth city, etc.)
    python chat_test.py --caller-phone +393333319326 --patient-dob 1979-06-19 --start-node booking

Then open: http://localhost:8081 in your browser

Author: Healthcare Flow Bot - Text Testing Mode
"""

import os
import sys
import asyncio
import argparse
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from loguru import logger

# FastAPI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
    MetricsFrame
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

# OpenTelemetry for LangFuse tracing
from config.telemetry import setup_tracing, get_tracer, get_conversation_tokens, flush_traces
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Import your existing components and flows
from config.settings import settings
from services.config import config
from pipeline.components import create_llm_service, create_context_aggregator
from flows.manager import initialize_flow_manager
from services.transcript_manager import get_transcript_manager, cleanup_transcript_manager

# Load environment variables
load_dotenv(override=True)


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
        # CRITICAL: Call super() first to properly initialize the processor
        await super().process_frame(frame, direction)

        # Push all frames downstream
        await self.push_frame(frame, direction)


class TextOutputProcessor(FrameProcessor):
    """
    Processor that captures LLM text output and sends to WebSocket
    Also records transcript for both transcript_manager and call_extractor
    """

    def __init__(self, websocket: WebSocket, session_id: str, flow_manager=None):
        super().__init__()
        self.websocket = websocket
        self.session_id = session_id
        self.flow_manager = flow_manager  # Will be set later
        self._buffer = ""
        self._trace_id_captured = False  # Flag to capture trace ID once
        logger.info("💬 TextOutputProcessor initialized")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process outgoing frames and send text to WebSocket"""
        # CRITICAL: Call super() first to properly initialize the processor
        await super().process_frame(frame, direction)

        # Capture OpenTelemetry trace ID on first frame (get from conversation context)
        if not self._trace_id_captured and self.flow_manager and os.getenv("ENABLE_TRACING", "false").lower() == "true":
            try:
                # Import conversation context provider
                from pipecat.utils.tracing.conversation_context_provider import ConversationContextProvider
                from opentelemetry import trace

                # Get the conversation context (this has the conversation span)
                provider = ConversationContextProvider.get_instance()
                conv_context = provider.get_current_conversation_context()

                if conv_context:
                    # Extract the span from the context
                    current_span = trace.get_current_span(conv_context)
                    if current_span and current_span.get_span_context().is_valid:
                        # Get trace ID in hex format (without 0x prefix)
                        trace_id = format(current_span.get_span_context().trace_id, '032x')
                        self.flow_manager.state["otel_trace_id"] = trace_id
                        logger.success(f"🔍 Captured OpenTelemetry trace ID from conversation context: {trace_id}")
                        self._trace_id_captured = True
                else:
                    logger.debug("⏳ Conversation context not available yet, will retry on next frame")
            except Exception as e:
                logger.warning(f"⚠️ Failed to capture trace ID: {e}")

        # ONLY capture text going DOWNSTREAM (from LLM to output)
        # NOT upstream text (user input)
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text
            self._buffer += text

            # Send partial response to WebSocket for streaming effect
            try:
                await self.websocket.send_json({
                    "type": "assistant_message_chunk",
                    "text": text
                })
                logger.debug(f"📤 Sent text chunk to browser: {text[:50]}...")
            except Exception as e:
                logger.error(f"❌ Failed to send text chunk: {e}")

        # When LLM finishes, send complete message and record in transcript
        elif isinstance(frame, (LLMFullResponseEndFrame, EndFrame)) and self._buffer:
            try:
                await self.websocket.send_json({
                    "type": "assistant_message_complete",
                    "text": self._buffer
                })
                logger.info(f"✅ Complete message sent: {self._buffer[:100]}...")

                # Record assistant message in transcript_manager (for booking agent)
                from services.transcript_manager import get_transcript_manager
                session_transcript_manager = get_transcript_manager(self.session_id)
                session_transcript_manager.add_assistant_message(self._buffer)

                # ALSO add to call_extractor (ALWAYS - Lombardy mode uses info agent only)
                if self.flow_manager:
                    call_extractor_instance = self.flow_manager.state.get("call_extractor")
                    if call_extractor_instance:
                        call_extractor_instance.add_transcript_entry("assistant", self._buffer)
                        logger.debug(f"📊 Added to call_extractor: assistant")

                self._buffer = ""
            except Exception as e:
                logger.error(f"❌ Failed to send complete message: {e}")

        await self.push_frame(frame, direction)


class TextTransportSimulator(FrameProcessor):
    """
    Simulates a transport layer for text-only communication
    Acts as both input and output processor
    """

    def __init__(self, websocket: WebSocket):
        super().__init__()
        self.websocket = websocket
        self._running = True
        self._started = False
        self._message_queue = asyncio.Queue()
        logger.info("🔌 TextTransportSimulator initialized")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames in both directions"""
        # CRITICAL: Call super() first to properly initialize the processor
        await super().process_frame(frame, direction)

        # Mark as started when we receive StartFrame
        if isinstance(frame, StartFrame):
            self._started = True
            logger.info("✅ TextTransportSimulator received StartFrame - ready to process messages")

            # Start processing queued messages
            asyncio.create_task(self._process_message_queue())

        # Push frame downstream
        await self.push_frame(frame, direction)

    async def _process_message_queue(self):
        """Process messages from the queue after pipeline has started"""
        while self._running:
            try:
                text = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                if text:
                    logger.info(f"📥 Processing queued message: {text}")

                    # Use TranscriptionFrame (like STT does) instead of TextFrame
                    # This way the context aggregator knows it's user input
                    transcription_frame = TranscriptionFrame(text=text, user_id="user", timestamp=0)
                    await self.push_frame(transcription_frame)

                    # Also notify that user "started speaking" and "stopped speaking"
                    # This helps with conversation flow timing
                    await self.push_frame(UserStartedSpeakingFrame())
                    await self.push_frame(UserStoppedSpeakingFrame())

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ Error processing message from queue: {e}")

    async def receive_text_message(self, text: str):
        """
        Receive text message from WebSocket and queue it for processing
        """
        logger.info(f"📨 Queueing user message: {text}")
        await self._message_queue.put(text)

    def stop(self):
        """Stop the transport"""
        self._running = False


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

        # Build Talkdesk payload
        call_data = {
            "interaction_id": interaction_id,
            "sentiment": analysis.get("sentiment", "neutral"),
            "service": str(analysis.get("service", "5")),
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
        from talkdesk_hangup import send_to_talkdesk
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


# Lifespan context manager for startup and shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    from services.database import db
    logger.info("🚀 Initializing Supabase database connection pool...")
    await db.connect()
    logger.success("✅ Supabase database initialized for chat_test.py")

    # Initialize OpenTelemetry tracing (LangFuse)
    tracer = setup_tracing(
        service_name="pipecat-healthcare-chat-test",
        enable_console=False
    )

    yield

    # Shutdown
    logger.info("🛑 Closing Supabase database connection pool...")
    await db.close()
    logger.info("✅ Database connection closed")


# FastAPI app
app = FastAPI(
    title="Healthcare Flow Bot - Text Chat Testing",
    description="Text-only chat interface for rapid testing",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store for active sessions
active_sessions: Dict[str, Any] = {}

# Global config for start node and caller simulation
global_start_node = "router"  # Default to unified router
global_caller_phone = None
global_patient_dob = None


@app.get("/")
async def root():
    """Serve the chat interface HTML"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Healthcare Bot - Text Chat Testing</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 50%, #7e22ce 100%);
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            position: relative;
            overflow: hidden;
        }

        body::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px);
            background-size: 50px 50px;
            animation: moveBackground 20s linear infinite;
        }

        @keyframes moveBackground {
            0% { transform: translate(0, 0); }
            100% { transform: translate(50px, 50px); }
        }

        .chat-container {
            width: 100%;
            max-width: 900px;
            height: 90vh;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(20px);
            border-radius: 24px;
            box-shadow: 0 30px 80px rgba(0,0,0,0.4), 0 0 1px rgba(255,255,255,0.5) inset;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            position: relative;
            z-index: 1;
            border: 1px solid rgba(255,255,255,0.2);
        }

        .chat-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 24px;
            text-align: center;
            position: relative;
            overflow: hidden;
        }

        .chat-header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(45deg, transparent 30%, rgba(255,255,255,0.1) 50%, transparent 70%);
            animation: shimmer 3s infinite;
        }

        @keyframes shimmer {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
        }

        .chat-header h1 {
            font-size: 26px;
            margin-bottom: 8px;
            font-weight: 700;
            position: relative;
            z-index: 1;
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        .chat-header p {
            font-size: 14px;
            opacity: 0.95;
            position: relative;
            z-index: 1;
        }

        .status-bar {
            background: #f8f9fa;
            padding: 10px 20px;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #22c55e;
            animation: pulse 2s infinite;
        }

        .status-dot.disconnected {
            background: #ef4444;
            animation: none;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            background: linear-gradient(180deg, #f8f9fa 0%, #e9ecef 100%);
            scroll-behavior: smooth;
        }

        .messages-container::-webkit-scrollbar {
            width: 8px;
        }

        .messages-container::-webkit-scrollbar-track {
            background: rgba(0,0,0,0.05);
            border-radius: 10px;
        }

        .messages-container::-webkit-scrollbar-thumb {
            background: linear-gradient(180deg, #667eea, #764ba2);
            border-radius: 10px;
        }

        .messages-container::-webkit-scrollbar-thumb:hover {
            background: linear-gradient(180deg, #764ba2, #667eea);
        }

        .message {
            margin-bottom: 15px;
            display: flex;
            gap: 10px;
            animation: slideIn 0.3s ease-out;
        }

        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .message.user {
            flex-direction: row-reverse;
        }

        .message-avatar {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            flex-shrink: 0;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: transform 0.2s ease;
        }

        .message-avatar:hover {
            transform: scale(1.1);
        }

        .message.user .message-avatar {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }

        .message.assistant .message-avatar {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        }

        .message-content {
            max-width: 70%;
            padding: 14px 18px;
            border-radius: 20px;
            line-height: 1.5;
            font-size: 15px;
            position: relative;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }

        .message-content:hover {
            transform: translateY(-2px);
        }

        .message.user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 6px;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
        }

        .message.user .message-content:hover {
            box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
        }

        .message.assistant .message-content {
            background: white;
            color: #1f2937;
            border-bottom-left-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            border: 1px solid rgba(0,0,0,0.05);
        }

        .message.assistant .message-content:hover {
            box-shadow: 0 6px 16px rgba(0,0,0,0.12);
        }

        .typing-indicator {
            display: none;
            padding: 12px 16px;
            background: white;
            border-radius: 18px;
            width: fit-content;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }

        .typing-indicator.active {
            display: block;
        }

        .typing-dots {
            display: flex;
            gap: 4px;
        }

        .typing-dots span {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #667eea;
            animation: typing 1.4s infinite;
        }

        .typing-dots span:nth-child(2) {
            animation-delay: 0.2s;
        }

        .typing-dots span:nth-child(3) {
            animation-delay: 0.4s;
        }

        @keyframes typing {
            0%, 60%, 100% {
                transform: translateY(0);
            }
            30% {
                transform: translateY(-10px);
            }
        }

        .input-container {
            padding: 20px 24px;
            background: white;
            border-top: 1px solid rgba(0,0,0,0.08);
            display: flex;
            gap: 12px;
            box-shadow: 0 -4px 12px rgba(0,0,0,0.05);
        }

        .input-container input {
            flex: 1;
            padding: 14px 20px;
            border: 2px solid #e5e7eb;
            border-radius: 28px;
            font-size: 15px;
            outline: none;
            transition: all 0.3s ease;
            background: #f9fafb;
        }

        .input-container input:focus {
            border-color: #667eea;
            background: white;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
        }

        .input-container button {
            padding: 14px 32px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 28px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
            position: relative;
            overflow: hidden;
        }

        .input-container button::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
            transition: left 0.5s ease;
        }

        .input-container button:hover::before {
            left: 100%;
        }

        .input-container button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
        }

        .input-container button:active {
            transform: translateY(0);
        }

        .input-container button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .system-message {
            text-align: center;
            color: #6b7280;
            font-size: 13px;
            margin: 15px 0;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="chat-header">
            <h1>🏥 Healthcare Flow Bot</h1>
            <p>Text Chat Testing Interface - No Voice Required</p>
        </div>

        <div class="status-bar">
            <div class="status-indicator">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">Connecting...</span>
            </div>
            <div style="font-size: 12px; color: #6b7280;">
                <span id="nodeInfo">Loading...</span>
            </div>
        </div>

        <div class="messages-container" id="messagesContainer">
            <div class="system-message">🚀 Starting chat session...</div>
        </div>

        <div class="input-container">
            <input
                type="text"
                id="messageInput"
                placeholder="Type your message here..."
                disabled
            />
            <button id="sendButton" disabled>Send</button>
        </div>
    </div>

    <script>
        let ws = null;
        let isConnected = false;
        let currentAssistantMessage = '';

        const messagesContainer = document.getElementById('messagesContainer');
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendButton');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const nodeInfo = document.getElementById('nodeInfo');

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;

            console.log('Connecting to:', wsUrl);
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('WebSocket connected');
                isConnected = true;
                statusDot.classList.remove('disconnected');
                statusText.textContent = 'Connected';
                messageInput.disabled = false;
                sendButton.disabled = false;
                messageInput.focus();

                addSystemMessage('✅ Connected to healthcare bot');
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                console.log('Received:', data);

                if (data.type === 'system_ready') {
                    nodeInfo.textContent = `Start Node: ${data.start_node}`;
                    addSystemMessage(`Starting with: ${data.start_node} flow`);
                }
                else if (data.type === 'assistant_message_chunk') {
                    // Streaming chunks from LLM - accumulate
                    currentAssistantMessage += data.text;
                    updateAssistantMessage(currentAssistantMessage);
                }
                else if (data.type === 'assistant_message_complete') {
                    // Complete message - finalize and reset
                    finalizeAssistantMessage(currentAssistantMessage);
                    // IMPORTANT: Reset buffer for next message
                    currentAssistantMessage = '';
                }
                else if (data.type === 'assistant_message') {
                    // Single complete message (fallback)
                    // Make sure we reset first
                    currentAssistantMessage = '';
                    addMessage('assistant', data.text);
                }
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                isConnected = false;
                statusDot.classList.add('disconnected');
                statusText.textContent = 'Disconnected';
                messageInput.disabled = true;
                sendButton.disabled = true;

                addSystemMessage('❌ Disconnected from server');
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                addSystemMessage('⚠️ Connection error');
            };
        }

        function sendMessage() {
            const text = messageInput.value.trim();
            if (!text || !isConnected) return;

            addMessage('user', text);

            ws.send(JSON.stringify({
                type: 'user_message',
                text: text
            }));

            messageInput.value = '';
            showTypingIndicator();
        }

        function addMessage(role, text) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;

            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.textContent = role === 'user' ? '👤' : '🤖';

            const content = document.createElement('div');
            content.className = 'message-content';
            content.textContent = text;

            messageDiv.appendChild(avatar);
            messageDiv.appendChild(content);

            // Remove typing indicator if exists
            const typingIndicator = document.querySelector('.typing-indicator');
            if (typingIndicator) {
                typingIndicator.remove();
            }

            messagesContainer.appendChild(messageDiv);
            scrollToBottom();
        }

        function updateAssistantMessage(text) {
            let messageDiv = document.querySelector('.message.assistant.streaming');

            if (!messageDiv) {
                // Remove ALL typing indicators (including parent message divs)
                const typingIndicators = document.querySelectorAll('.message.assistant');
                typingIndicators.forEach(indicator => {
                    // Only remove if it contains typing-indicator (not a real message)
                    if (indicator.querySelector('.typing-indicator')) {
                        indicator.remove();
                    }
                });

                // Create new streaming message
                messageDiv = document.createElement('div');
                messageDiv.className = 'message assistant streaming';

                const avatar = document.createElement('div');
                avatar.className = 'message-avatar';
                avatar.textContent = '🤖';

                const content = document.createElement('div');
                content.className = 'message-content';

                messageDiv.appendChild(avatar);
                messageDiv.appendChild(content);

                messagesContainer.appendChild(messageDiv);
            }

            const content = messageDiv.querySelector('.message-content');
            content.textContent = text;
            scrollToBottom();
        }

        function finalizeAssistantMessage(text) {
            // Remove ALL streaming classes to ensure clean state
            const allStreamingMessages = document.querySelectorAll('.message.assistant.streaming');
            allStreamingMessages.forEach(msg => {
                msg.classList.remove('streaming');
            });

            // Reset the current message buffer
            currentAssistantMessage = '';

            // Ensure we scroll to bottom
            scrollToBottom();
        }

        function showTypingIndicator() {
            const indicator = document.createElement('div');
            indicator.className = 'message assistant';
            indicator.innerHTML = `
                <div class="message-avatar">🤖</div>
                <div class="typing-indicator active">
                    <div class="typing-dots">
                        <span></span>
                        <span></span>
                        <span></span>
                    </div>
                </div>
            `;
            messagesContainer.appendChild(indicator);
            scrollToBottom();
        }

        function addSystemMessage(text) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'system-message';
            messageDiv.textContent = text;
            messagesContainer.appendChild(messageDiv);
            scrollToBottom();
        }

        function scrollToBottom() {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        // Event listeners
        sendButton.addEventListener('click', sendMessage);

        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });

        // Connect on page load
        connectWebSocket();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "service": "healthcare-flow-bot-text-chat",
        "version": "1.0.0",
        "active_sessions": len(active_sessions),
        "mode": "text-only",
        "start_node": global_start_node
    })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Text-only WebSocket endpoint for chat testing
    """
    global global_start_node, global_caller_phone, global_patient_dob

    await websocket.accept()

    # ✅ Use existing Supabase UUID for testing (row already created with bridge data)
    session_id = "49b78a42-9024-4646-95e2-d2d6f4f8a17b"

    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"New Text Chat Session (using Supabase test UUID)")
    logger.info(f"Session ID: {session_id}")
    logger.info(f"Start Node: {global_start_node}")
    logger.info(f"Mode: Text-only (No STT/TTS)")
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Variables for pipeline
    runner = None
    task = None
    text_transport = None
    text_output = None

    try:
        # Check required API keys (only LLM needed for text mode)
        if not os.getenv("OPENAI_API_KEY"):
            raise Exception("OPENAI_API_KEY not found - required for LLM")

        # Validate health service configuration
        try:
            config.validate()
            logger.success("✅ Health services configuration validated")
        except Exception as e:
            logger.error(f"❌ Health services configuration error: {e}")
            raise

        # CREATE SERVICES (NO STT/TTS FOR TEXT MODE!)
        logger.info("Initializing services for TEXT mode...")
        llm = create_llm_service()
        context_aggregator = create_context_aggregator(llm)
        logger.info("✅ LLM and context aggregator initialized (no STT/TTS)")

        # CREATE TEXT TRANSPORT SIMULATOR
        text_transport = TextTransportSimulator(websocket)
        text_output = TextOutputProcessor(websocket, session_id)

        # CREATE PIPELINE (TEXT-ONLY - NO STT/TTS!)
        pipeline = Pipeline([
            text_transport,              # Text input from WebSocket
            context_aggregator.user(),   # Add user message to context
            llm,                         # LLM with flows
            text_output,                 # Capture and send text output
            context_aggregator.assistant()  # Add assistant response to context
        ])

        logger.info("Text Chat Pipeline structure:")
        logger.info("  1. TextTransportSimulator (WebSocket text input)")
        logger.info("  2. Context Aggregator (User)")
        logger.info("  3. OpenAI LLM (with flows)")
        logger.info("  4. TextOutputProcessor (WebSocket text output)")
        logger.info("  5. Context Aggregator (Assistant)")
        logger.info("✅ NO STT/TTS - Pure text mode for fast testing!")

        # START PER-CALL LOGGING
        from services.call_logger import CallLogger
        session_call_logger = CallLogger(session_id)
        log_file = session_call_logger.start_call_logging(session_id, "text_chat_test")
        logger.info(f"📁 Text chat logging started: {log_file}")

        # Create pipeline task with LangFuse tracing enabled
        # CRITICAL: Disable idle_timeout for text-only chat to prevent premature disconnections
        # In text mode, there are no BotSpeakingFrame events, so idle detection triggers incorrectly
        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=False,  # Not needed for text
                enable_transcriptions=False,  # No audio transcription
                enable_usage_metrics=True,  # Keep metrics enabled for performance monitoring
            ),
            enable_tracing=True,  # ✅ Enable OpenTelemetry tracing (LangFuse)
            conversation_id=session_id,  # Use session_id as conversation ID for trace correlation
            # ✅ Add langfuse.session.id to map our session_id to LangFuse sessions
            additional_span_attributes={
                "langfuse.session.id": session_id,
                "langfuse.user.id": "chat_test_user",
            },
            cancel_on_idle_timeout=False  # MUST be direct parameter to PipelineTask, not in params!
        )

        # NOW create the real FlowManager with all parameters including global functions
        from pipecat_flows import FlowManager
        from flows.global_functions import GLOBAL_FUNCTIONS
        flow_manager = FlowManager(
            task=task,
            llm=llm,
            context_aggregator=context_aggregator,
            transport=None,  # No transport for text mode
            global_functions=GLOBAL_FUNCTIONS,  # 8 global functions for info, transfer, booking
        )

        # Set flow_manager reference in text_output processor (for transcript recording)
        text_output.flow_manager = flow_manager

        # Store business_status, session_id, and stream_sid in flow manager state (required for info agent)
        flow_manager.state["business_status"] = "open"  # Always open for testing
        flow_manager.state["session_id"] = session_id
        flow_manager.state["stream_sid"] = ""  # Empty for text chat testing (no Talkdesk)
        flow_manager.state["interaction_id"] = "d2568ef3-b8c9-4cbc-ac90-6100d4c0e8c0"  # ✅ Simulated Talkdesk interaction ID
        flow_manager.state["caller_phone_from_talkdesk"] = "+393333319326"  # ✅ Default test phone number
        logger.info(f"✅ Business status stored in flow state: open (testing)")
        logger.info(f"✅ Session ID stored in flow state: {session_id}")
        logger.info(f"✅ Stream SID: Not applicable (text chat testing)")
        logger.info(f"✅ Interaction ID: d2568ef3-b8c9-4cbc-ac90-6100d4c0e8c0 (simulated)")
        logger.info(f"✅ Caller Phone: +393333319326 (default test number)")

        # PRE-POPULATE STATE WITH CALLER INFO (Simulate Talkdesk caller ID)
        if global_caller_phone:
            flow_manager.state["caller_phone_from_talkdesk"] = global_caller_phone
            logger.info(f"📞 Simulated caller phone from Talkdesk: {global_caller_phone}")

        if global_patient_dob:
            flow_manager.state["patient_dob"] = global_patient_dob
            logger.info(f"📅 Pre-populated patient DOB: {global_patient_dob}")

        # Initialize transcript manager for text conversations
        session_transcript_manager = get_transcript_manager(session_id)
        session_transcript_manager.start_session(session_id)
        logger.info(f"📝 Started transcript recording for session: {session_id}")

        # Initialize call_extractor for ALL calls (to capture ALL messages from start)
        from services.call_data_extractor import get_call_extractor
        call_extractor = get_call_extractor(session_id)
        call_extractor.call_id = session_id
        call_extractor.interaction_id = "d2568ef3-b8c9-4cbc-ac90-6100d4c0e8c0"
        flow_manager.state["call_extractor"] = call_extractor
        call_extractor.start_call(caller_phone=global_caller_phone or "+393333319326", interaction_id="d2568ef3-b8c9-4cbc-ac90-6100d4c0e8c0")
        logger.info(f"✅ Call extractor initialized for Supabase storage")
        logger.info(f"⏱️ Call start time recorded: {call_extractor.started_at}")

        # Store session
        active_sessions[session_id] = {
            "websocket": websocket,
            "connected_at": asyncio.get_event_loop().time(),
            "call_logger": session_call_logger,
            "mode": "text-only",
            "flow_manager": flow_manager,
            "text_transport": text_transport
        }

        # Initialize flow manager
        try:
            await initialize_flow_manager(flow_manager, global_start_node)
            logger.success(f"✅ Flow initialized with {global_start_node} node")

            # Notify client that system is ready
            await websocket.send_json({
                "type": "system_ready",
                "start_node": global_start_node
            })
        except Exception as e:
            logger.error(f"Error during flow initialization: {e}")

        # START PIPELINE
        runner = PipelineRunner()
        logger.info(f"🚀 Text Chat Pipeline started for session: {session_id}")

        # Run pipeline in background
        pipeline_task = asyncio.create_task(runner.run(task))

        # Note: OpenTelemetry trace ID will be captured by TextOutputProcessor
        # on the first frame processed (from inside the pipeline trace context)

        # Handle incoming WebSocket messages
        try:
            while True:
                # Receive message from WebSocket
                message = await websocket.receive_json()

                if message.get("type") == "user_message":
                    user_text = message.get("text", "").strip()
                    if user_text:
                        logger.info(f"💬 User: {user_text}")

                        # Record in transcript_manager (for booking agent)
                        session_transcript_manager.add_user_message(user_text)

                        # ALSO add to call_extractor (ALWAYS - Lombardy mode uses info agent only)
                        call_extractor_instance = flow_manager.state.get("call_extractor")
                        if call_extractor_instance:
                            call_extractor_instance.add_transcript_entry("user", user_text)
                            logger.debug(f"📊 Added to call_extractor: user")

                        # Send to pipeline
                        await text_transport.receive_text_message(user_text)

        except WebSocketDisconnect:
            logger.info(f"🔌 Text chat client disconnected: {session_id}")
        except Exception as e:
            logger.error(f"❌ Error in message loop: {e}")
        finally:
            # Cancel pipeline
            if pipeline_task:
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        logger.error(f"❌ Error in Text Chat WebSocket handler: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        if session_id in active_sessions:
            # Extract and store call data before cleanup
            # Save to BOTH Azure AND Supabase for ALL calls
            try:
                flow_manager = active_sessions[session_id].get("flow_manager")
                if flow_manager:
                    current_agent = flow_manager.state.get("current_agent", "unknown")
                    logger.info(f"📊 Extracting call data for session: {session_id} | Agent: {current_agent}")

                    # === STEP 1: Save to Supabase (ALL calls) ===
                    logger.info("🔵 Saving to Supabase...")
                    call_extractor = flow_manager.state.get("call_extractor")
                    if call_extractor:
                        # Query LangFuse for token usage before saving
                        if os.getenv("ENABLE_TRACING", "false").lower() == "true":
                            logger.info("📊 Querying LangFuse for token usage...")
                            try:
                                logger.info("⏳ Waiting 1 second for spans to be queued...")
                                await asyncio.sleep(1)
                                logger.info("🔄 Flushing traces to LangFuse...")
                                flush_traces()
                                logger.info("⏳ Waiting 5 seconds for LangFuse to index traces...")
                                await asyncio.sleep(5)
                                token_data = await get_conversation_tokens(session_id)
                                call_extractor.llm_token_count = token_data["total_tokens"]
                                logger.success(f"✅ Updated call_extractor with LangFuse tokens: {token_data['total_tokens']}")
                            except Exception as e:
                                logger.error(f"❌ Failed to retrieve tokens from LangFuse: {e}")

                        # Mark call end time and save to Supabase
                        call_extractor.end_call()
                        supabase_success = await call_extractor.save_to_database(flow_manager.state)
                        if supabase_success:
                            logger.success(f"✅ Call data saved to Supabase for session: {session_id}")
                            # Report to Talkdesk (only if not transferred to human operator)
                            await report_to_talkdesk(flow_manager, call_extractor)
                        else:
                            logger.error(f"❌ Failed to save call data to Supabase: {session_id}")
                    else:
                        logger.error("❌ No call_extractor found - Supabase save skipped")

                    # === STEP 2: Save to Azure Blob Storage (ALL calls) ===
                    logger.info("🟢 Saving to Azure Blob Storage...")
                    session_transcript_manager = get_transcript_manager(session_id)
                    azure_success = await session_transcript_manager.extract_and_store_call_data(flow_manager)
                    if azure_success:
                        logger.success(f"✅ Call data saved to Azure for session: {session_id}")
                    else:
                        logger.error(f"❌ Failed to save call data to Azure: {session_id}")
                else:
                    logger.warning(f"⚠️ No flow_manager found for session: {session_id}")

            except Exception as e:
                logger.error(f"❌ Error during call data extraction: {e}")
                import traceback
                traceback.print_exc()

            # Cleanup transcript
            cleanup_transcript_manager(session_id)

            del active_sessions[session_id]

        # Stop call logging
        try:
            if 'session_call_logger' in locals():
                saved_log_file = session_call_logger.stop_call_logging()
                if saved_log_file:
                    logger.info(f"📁 Call log saved: {saved_log_file}")
        except Exception as e:
            logger.error(f"❌ Error stopping call logging: {e}")

        # Cancel task
        if task:
            await task.cancel()

        logger.info(f"Text Chat Session ended: {session_id}")
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Text-Based Chat Testing for Healthcare Flow Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python chat_test.py                         # Unified router (default - detects intent)
  python chat_test.py --start-node greeting   # Direct to booking agent (skip router)
  python chat_test.py --start-node email      # Start with email collection
  python chat_test.py --start-node booking    # Start with booking flow
  python chat_test.py --start-node orange_box # Test Orange Box flow (RX Caviglia Destra)
  python chat_test.py --start-node cerba_card # Start from Cerba Card (auto-filled)
  python chat_test.py --port 8081             # Use custom port
        """
    )

    parser.add_argument(
        "--start-node",
        default="router",
        choices=["router", "greeting", "email", "name", "phone", "fiscal_code", "booking", "slot_selection", "cerba_card", "orange_box"],
        help="Starting flow node (default: router for unified routing, greeting for direct booking)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Port to run the server on (default: 8081)"
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
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


def main():
    """Main function"""
    global global_start_node, global_caller_phone, global_patient_dob

    args = parse_arguments()
    global_start_node = args.start_node
    global_caller_phone = args.caller_phone
    global_patient_dob = args.patient_dob

    # Check required environment variables
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("❌ Missing OPENAI_API_KEY environment variable")
        sys.exit(1)

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("🚀 HEALTHCARE FLOW BOT - TEXT CHAT TESTING MODE")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"📍 Start Node: {args.start_node}")
    logger.info(f"🌐 Server: http://{args.host}:{args.port}")
    logger.info(f"💬 Mode: Text-only (No STT/TTS)")
    logger.info(f"⚡ Benefits: Instant testing, lower costs, better debugging")

    if global_caller_phone or global_patient_dob:
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("🎭 SIMULATED CALLER DATA (like from Talkdesk):")
        if global_caller_phone:
            logger.info(f"   📞 Caller Phone: {global_caller_phone}")
        if global_patient_dob:
            logger.info(f"   📅 Patient DOB: {global_patient_dob}")
        logger.info("   ✅ This will test existing patient flow (database lookup)")

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("📖 INSTRUCTIONS:")
    logger.info(f"   1. Open http://localhost:{args.port} in your browser")
    logger.info("   2. Start typing to test your flows")
    logger.info("   3. All your existing flows work exactly the same")
    logger.info("   4. Press Ctrl+C to stop the server")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    import uvicorn
    uvicorn.run(app, host=args.host, port=8004, reload=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Text chat testing server stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
