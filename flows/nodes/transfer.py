"""
Transfer Node
Handles transfer to human operator with escalation API call
"""

from pipecat_flows import NodeConfig
from config.settings import settings


def create_transfer_node() -> NodeConfig:
    """
    Create transfer node that handles escalation to human operator

    Flow:
    1. request_transfer handler calls escalation API (in global_handlers.py)
    2. Handler returns this transfer node
    3. Agent says transfer message (Italian)
    4. Post-action: Ends conversation
    5. WebSocket closes automatically (handled by bridge)
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
