"""
Transfer Node
Handles transfer to human operator with escalation API call
"""

from loguru import logger
from pipecat_flows import NodeConfig
from config.settings import settings


def create_transfer_node(include_end_conversation: bool = True) -> NodeConfig:
    """
    Create transfer node (NodeConfig only, no escalation).
    Use create_transfer_node_with_escalation() instead for most cases.

    Args:
        include_end_conversation: If True, add end_conversation post_action.
            Set False for direct Talkdesk mode — escalation handles ending.
    """
    post_actions = []
    if include_end_conversation:
        post_actions.append({
            "type": "end_conversation",
            "text": ""
        })

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
        post_actions=post_actions
    )


async def create_transfer_node_with_escalation(flow_manager) -> NodeConfig:
    """
    Fire off Talkdesk escalation in background then return transfer node.

    Direct Talkdesk mode: escalation sends stop frame then EndFrame (no end_conversation in node).
    Bridge mode: end_conversation in node closes pipeline, bridge handles Talkdesk stop separately.
    """
    import asyncio
    is_direct = flow_manager.state.get("is_talkdesk_direct", False)

    try:
        from flows.handlers.global_handlers import _handle_transfer_escalation
        asyncio.create_task(_handle_transfer_escalation(flow_manager))
    except Exception as e:
        logger.error(f"❌ Escalation failed during transfer node creation: {e}")

    # Direct mode: don't end_conversation — escalation will send stop + EndFrame
    # Bridge mode: end_conversation closes pipeline, bridge sends Talkdesk stop independently
    return create_transfer_node(include_end_conversation=not is_direct)
