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
        task_messages=[
            {
                "role": "system",
                "content": (
                    f"Say EXACTLY this message in Italian: "
                    f"'Attendi, ti sto trasferendo a un operatore umano.' "
                    f"{settings.language_config}"
                )
            }
        ],
        functions=[],
        post_actions=[
            {
                "type": "end_conversation",
                "text": "Attendi, ti sto trasferendo a un operatore umano."
            }
        ]
    )


async def create_transfer_node_with_escalation(flow_manager) -> NodeConfig:
    """
    Call Talkdesk escalation API then return the transfer node.
    This ensures every transfer actually triggers the real handoff.
    """
    try:
        from flows.handlers.global_handlers import _handle_transfer_escalation
        await _handle_transfer_escalation(flow_manager)
    except Exception as e:
        logger.error(f"❌ Escalation failed during transfer node creation: {e}")
    return create_transfer_node()
