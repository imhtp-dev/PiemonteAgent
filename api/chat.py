"""
Text chat endpoint for testing voice agent knowledge
Uses the SAME LLM and flows as WebSocket agent, based on chat_test.py
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal
from loguru import logger
from datetime import datetime
import asyncio

# Authentication removed - chat endpoint now open for testing

# Pipecat imports
from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TextFrame,
    LLMTextFrame,
    LLMFullResponseEndFrame,
    EndFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Import flow management (uses main flows, not info_agent)
from flows.manager import create_flow_manager, initialize_flow_manager

# Reuse components (SAME as WebSocket agent)
from pipeline.components import (
    create_llm_service,
    create_context_aggregator
)


router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    region: str = "Piemonte"


class ChatResponse(BaseModel):
    success: bool
    response: str
    function_called: Optional[Literal["RAG", "GRAPH"]] = None
    timestamp: datetime


class TextInputProcessor(FrameProcessor):
    """Convert text to transcription frames"""
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, TextFrame):
            transcription = TranscriptionFrame(
                text=frame.text,
                user_id="api_user",
                timestamp=asyncio.get_event_loop().time()
            )
            await self.push_frame(transcription, direction)
        else:
            await self.push_frame(frame, direction)


class TextOutputCapture(FrameProcessor):
    """Capture LLM output"""
    
    def __init__(self):
        super().__init__()
        self.response_text = ""
        self.got_response = asyncio.Event()
        self.response_count = 0
        self.function_called = False
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        from pipecat.frames.frames import FunctionCallResultFrame
        
        if isinstance(frame, FunctionCallResultFrame):
            self.function_called = True
            logger.debug("✅ Function result received, waiting for final LLM response...")
        elif isinstance(frame, LLMTextFrame):
            self.response_text += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.response_count += 1
            logger.debug(f"📝 LLM response #{self.response_count}, text so far: {len(self.response_text)} chars")
            
            # If function was called, wait for second response (after function result)
            # Otherwise, first response is the final one
            if (self.function_called and self.response_count >= 2) or (not self.function_called and self.response_count >= 1):
                if self.response_text:
                    logger.debug(f"✅ Got final response: {len(self.response_text)} chars")
                    self.got_response.set()
        
        await self.push_frame(frame, direction)


@router.post("/send", response_model=ChatResponse)
async def send_chat_message(
    request: ChatRequest,
    # current_user: dict = Depends(get_current_user_from_token)  # Auth removed
):
    """
    Send text to REAL agent (same LLM+flows as WebSocket)
    """
    try:
        logger.info(f"💬 Chat request: {request.message[:80]}...")
        
        # Create services
        llm = create_llm_service()
        context_aggregator, _node_mute = create_context_aggregator(llm)
        
        # Create processors
        text_input = TextInputProcessor()
        text_output = TextOutputCapture()
        
        # Pipeline
        pipeline = Pipeline([
            text_input,
            context_aggregator.user(),
            llm,
            text_output,
            context_aggregator.assistant()
        ])
        
        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=False)
        )
        
        # Create flow manager
        flow_manager = create_flow_manager(task, llm, context_aggregator, None)
        flow_manager.state["region"] = request.region
        flow_manager.state["session_id"] = f"chat-{datetime.now().timestamp()}"
        
        # Start pipeline FIRST
        runner = PipelineRunner()
        pipeline_task = asyncio.create_task(runner.run(task))
        
        # Wait for pipeline to start
        await asyncio.sleep(0.3)
        
        # Initialize flow (registers all tools) - REQUIRED!
        await initialize_flow_manager(flow_manager, "greeting")
        
        # Immediately send user message (before bot can greet)
        await text_input.queue_frame(TextFrame(text=request.message))
        
        # Wait for response (longer timeout for function calls)
        try:
            await asyncio.wait_for(text_output.got_response.wait(), timeout=45.0)
        except asyncio.TimeoutError:
            logger.error("⏱️ Response timeout after 45s")
        
        # Give function calls time to complete
        await asyncio.sleep(0.5)
        
        # Cancel pipeline
        await task.cancel()
        
        # Wait for clean shutdown
        try:
            await asyncio.wait_for(pipeline_task, timeout=5.0)
        except:
            pass
        
        # Get response
        response_text = text_output.response_text.strip() or "Nessuna risposta ricevuta."
        
        # Detect function
        function_called = None
        last_func = flow_manager.state.get("last_function_called", "")
        if "knowledge" in last_func or "clinic" in last_func:
            function_called = "RAG"
        elif "price" in last_func or "exam" in last_func:
            function_called = "GRAPH"
        
        logger.success(f"✅ Response: {response_text[:80]}... ({function_called})")
        
        return ChatResponse(
            success=True,
            response=response_text,
            function_called=function_called,
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"❌ Chat error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
