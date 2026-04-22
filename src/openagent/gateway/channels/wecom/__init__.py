"""WeCom AI Bot private-chat channel integration."""

from .adapter import WeComChannelAdapter, WeComRawEvent
from .assembly import (
    WeComAppConfig,
    create_wecom_gateway,
    create_wecom_host,
    create_wecom_host_from_env,
    create_wecom_runtime,
)
from .client import WeComAiBotClient, WeComBotClient
from .dedupe import FileWeComInboundDedupeStore, InMemoryWeComInboundDedupeStore
from .host import WeComPrivateChatHost

__all__ = [
    "FileWeComInboundDedupeStore",
    "InMemoryWeComInboundDedupeStore",
    "WeComAiBotClient",
    "WeComAppConfig",
    "WeComBotClient",
    "WeComChannelAdapter",
    "WeComPrivateChatHost",
    "WeComRawEvent",
    "create_wecom_gateway",
    "create_wecom_host",
    "create_wecom_host_from_env",
    "create_wecom_runtime",
]
