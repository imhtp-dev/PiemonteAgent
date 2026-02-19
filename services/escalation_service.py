"""
Escalation Service for transferring calls to human operators
Calls the bridge escalation API which handles WebSocket closure and Talkdesk transfer
"""
import os
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
    service: str,
    call_id: str = None,
    stream_sid: str = None
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
        service: Service code 1-5 (as string)
        call_id: Session/call ID from flow_manager.state (for database row matching)
        stream_sid: Talkdesk stream SID (for direct escalation, eliminates Redis dependency)

    Returns:
        bool: True if escalation API call succeeded, False otherwise

    Note: WebSocket closes automatically regardless of return value
    """
    try:
        if not call_id:
            logger.error("❌ call_id is required for escalation API")
            return False

        # Prepare payload for bridge escalation endpoint
        # Pass both call_id (for DB matching) and stream_sid (for direct Talkdesk escalation)
        payload = {
            "message": {
                "call": {
                    "id": call_id  # Session ID for database row matching
                },
                "stream_sid": stream_sid,  # ✅ Talkdesk stream SID for direct escalation
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
                                "service": service
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
            "escalation.service_code": service,
            "escalation.summary_length": len(summary),
            "escalation.has_stream_sid": bool(stream_sid)
        })

        logger.info(f"Calling escalation API: {ESCALATION_API_URL}")
        logger.info(f"Escalation data: call_id={call_id}, stream_sid={stream_sid or 'Not provided'}, "
                   f"summary_len={len(summary)}, sentiment={sentiment}, action={action}, "
                   f"duration={duration}s, service={service}")

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
