"""Gateway package exports."""

from .adapters import DesktopChannelAdapter, TerminalChannelAdapter
from .binding_store import FileSessionBindingStore
from .core import Gateway
from .feishu import (
    FeishuAppConfig,
    FeishuBotClient,
    FeishuChannelAdapter,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
    OfficialFeishuBotClient,
    create_feishu_gateway,
    create_feishu_host_from_env,
    create_feishu_runtime,
)
from .interfaces import ChannelAdapter, SessionAdapter, SessionBindingStore
from .models import (
    ChannelIdentity,
    EgressEnvelope,
    InboundEnvelope,
    LocalSessionHandle,
    NormalizedInboundMessage,
    SessionBinding,
)
from .session_adapter import InProcessSessionAdapter

__all__ = [
    "ChannelAdapter",
    "ChannelIdentity",
    "DesktopChannelAdapter",
    "EgressEnvelope",
    "FileSessionBindingStore",
    "FeishuAppConfig",
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "FeishuHostRunLock",
    "FeishuLongConnectionHost",
    "Gateway",
    "InboundEnvelope",
    "InProcessSessionAdapter",
    "LocalSessionHandle",
    "NormalizedInboundMessage",
    "OfficialFeishuBotClient",
    "SessionAdapter",
    "SessionBinding",
    "SessionBindingStore",
    "TerminalChannelAdapter",
    "create_feishu_gateway",
    "create_feishu_host_from_env",
    "create_feishu_runtime",
]
