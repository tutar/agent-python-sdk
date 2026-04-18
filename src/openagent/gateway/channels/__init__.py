"""Channel-specific gateway integrations."""

from .feishu import FeishuChannelAdapter
from .local import TerminalChannelAdapter

__all__ = [
    "FeishuChannelAdapter",
    "TerminalChannelAdapter",
]
