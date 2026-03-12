"""
Transfer Node
Handles transfer to human operator with escalation API call
"""

from loguru import logger
from pipecat_flows import NodeConfig
from config.settings import settings


def create_transfer_node() -> NodeConfig:
    """
    Create transfer node (NodeConfig only, no escalation).
    Use create_transfer_node_with_escalation() instead for most cases.
    """

    return NodeConfig(
        name="transfer",
        pre_actions=[
            {
                "type": "tts_say",
                "text": "Attendi, ti sto trasferendo a un operatore umano."
            }
        ],
        task_messages=[
            {
                "role": "system",
                "content": (
                    f"The patient is being transferred. The transfer message has already been spoken. "
                    f"Do not say anything else. {settings.language_config}"
                )
            }
        ],
        functions=[],
        post_actions=[
            {
                "type": "end_conversation",
                "text": ""
            }
        ]
    )


async def create_transfer_node_with_escalation(flow_manager) -> NodeConfig:
    """
    Fire off Talkdesk escalation in background then return transfer node.
    The node's pre_actions TTS plays immediately while escalation runs.
    """
    import asyncio
    try:
        from flows.handlers.global_handlers import _handle_transfer_escalation
        asyncio.create_task(_handle_transfer_escalation(flow_manager))
    except Exception as e:
        logger.error(f"❌ Escalation failed during transfer node creation: {e}")
    return create_transfer_node()
