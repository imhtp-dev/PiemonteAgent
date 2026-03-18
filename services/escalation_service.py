"""
Escalation Service for transferring calls to human operators

Two modes:
- Bridge mode: HTTP POST to bridge /escalation endpoint (legacy)
- Direct mode: Push TalkdeskControlFrame through pipeline (no bridge)
"""
import os
import asyncio
import aiohttp
from loguru import logger
from typing import Optional
from utils.tracing import trace_api_call, add_span_attributes

# Bridge escalation endpoint
ESCALATION_API_URL = os.getenv("BRIDGE_ESCALATION_URL", "https://bridgepiemonte-efhqhzdjdyb6a0g6.francecentral-01.azurewebsites.net/escalation")


@trace_api_call("api.escalation_transfer")
async def call_escalation_api(
    summary: str,
    sentiment: str,
    action: str,
    duration: str,
    queue_code: str,
    call_id: str = None,
    stream_sid: str = None,
) -> bool:
    """
    Call bridge escalation API to transfer call to human operator

    The bridge will:
    1. Close Pipecat WebSocket automatically
    2. Send transfer message to Talkdesk with provided data

    Args:
        summary: Call summary (max 250 chars)
        sentiment: positive|neutral|negative
        action: transfer
        duration: Duration in seconds (as string)
        queue_code: Full Talkdesk queue code (e.g. "1|3|2", "2|2|5")
        call_id: Session/call ID from flow_manager.state (for database row matching)
        stream_sid: Talkdesk stream SID (for direct escalation)

    Returns:
        bool: True if escalation API call succeeded, False otherwise

    Note: WebSocket closes automatically regardless of return value
    """
    try:
        if not call_id:
            logger.error("❌ call_id is required for escalation API")
            return False

        # Backward compat: derive old service + sector from queue_code
        # so old bridge still works until it's updated
        if queue_code.startswith("1|"):
            compat_sector = "booking"
            compat_service = queue_code.split("|")[-1]
        else:
            compat_sector = "info"
            compat_service = queue_code.split("|")[-1]

        # Prepare payload for bridge escalation endpoint
        payload = {
            "message": {
                "call": {
                    "id": call_id
                },
                "stream_sid": stream_sid,
                "toolCallList": [
                    {
                        "id": "transfer_tool_call",
                        "type": "function",
                        "function": {
                            "name": "request_transfer",
                            "arguments": {
                                "summary": summary[:250],
                                "sentiment": sentiment,
                                "action": action,
                                "duration": duration,
                                "queue_code": queue_code,
                                # Backward compat for old bridge
                                "service": compat_service,
                                "sector": compat_sector,
                            }
                        }
                    }
                ]
            }
        }

        # Add span attributes for tracking
        add_span_attributes({
            "escalation.call_id": call_id,
            "escalation.sentiment": sentiment,
            "escalation.action": action,
            "escalation.duration_seconds": duration,
            "escalation.queue_code": queue_code,
            "escalation.summary_length": len(summary),
            "escalation.has_stream_sid": bool(stream_sid),
        })

        logger.info(f"Calling escalation API: {ESCALATION_API_URL}")
        logger.info(f"Escalation data: call_id={call_id}, stream_sid={stream_sid or 'N/A'}, "
                   f"queue_code={queue_code}, sentiment={sentiment}, duration={duration}s")

        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                ESCALATION_API_URL,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                status = response.status
                response_text = await response.text()

                if status == 200:
                    logger.info(f"✅ Escalation API success: {response_text}")
                    return True
                else:
                    logger.error(f"❌ Escalation API failed: status={status}, response={response_text}")
                    return False

    except aiohttp.ClientError as e:
        logger.error(f"❌ Escalation API network error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Escalation API unexpected error: {e}")
        return False


async def send_escalation_direct(
    flow_manager,
    summary: str,
    sentiment: str,
    action: str,
    duration: str,
    queue_code: str,
) -> bool:
    """
    Direct escalation via TalkdeskControlFrame — no bridge HTTP call.
    Pushes stop event with ringGroup through the pipeline serializer.

    The TalkdeskControlFrame is an UninterruptibleFrame, so it queues
    after any pending TTS frames. Sleep(2) before pushing ensures the
    farewell TTS has time to flush.

    Args:
        flow_manager: FlowManager with state containing transport
        summary: Call summary (max 240 chars for ringGroup)
        sentiment: positive|neutral|negative
        action: transfer
        duration: Duration in seconds (as string)
        queue_code: Talkdesk queue code (e.g. "1|3|2")

    Returns:
        True if frame was pushed successfully
    """
    try:
        from serializers.talkdesk import TalkdeskControlFrame, TalkdeskControlAction

        transport = flow_manager.state.get("transport")
        if not transport:
            logger.error("❌ No transport in flow_manager.state — cannot send escalation")
            return False

        # Build ringGroup in same format as bridge: summary::sentiment::action::duration::queue_code
        ring_group = f"{summary[:240]}::{sentiment}::{action}::{duration}::{queue_code}"

        add_span_attributes({
            "escalation.mode": "direct",
            "escalation.queue_code": queue_code,
            "escalation.sentiment": sentiment,
            "escalation.ring_group_length": len(ring_group),
        })

        logger.info(f"🚀 Direct escalation: queue_code={queue_code}, sentiment={sentiment}")
        logger.info(f"   ringGroup: {ring_group[:100]}...")

        # Wait for TTS farewell to flush before sending stop
        await asyncio.sleep(2)

        frame = TalkdeskControlFrame(
            action=TalkdeskControlAction.ESCALATE,
            ring_group=ring_group
        )

        # Push through output transport — serializer converts to Talkdesk stop event
        from pipecat.processors.frame_processor import FrameDirection
        await transport.output().queue_frame(frame, FrameDirection.DOWNSTREAM)

        logger.info(f"✅ Direct escalation frame sent to Talkdesk")
        return True

    except Exception as e:
        logger.error(f"❌ Direct escalation error: {e}")
        return False
