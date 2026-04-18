"""Feishu channel integration surface."""

from .adapter import FeishuBotClient, FeishuChannelAdapter
from .client import OfficialFeishuBotClient
from .dedupe import FileFeishuInboundDedupeStore, InMemoryFeishuInboundDedupeStore
from .host import (
    FEISHU_REACTION_COMPLETED,
    FEISHU_REACTION_IN_PROGRESS,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
)

__all__ = [
    "FileFeishuInboundDedupeStore",
    "FEISHU_REACTION_COMPLETED",
    "FEISHU_REACTION_IN_PROGRESS",
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "FeishuHostRunLock",
    "FeishuLongConnectionHost",
    "InMemoryFeishuInboundDedupeStore",
    "OfficialFeishuBotClient",
]
