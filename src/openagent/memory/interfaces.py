"""Memory store interfaces."""

from __future__ import annotations

from typing import Protocol

from openagent.memory.models import MemoryConsolidationResult, MemoryRecallResult, MemoryRecord
from openagent.session import SessionMessage


class MemoryStore(Protocol):
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        """Persist or update a durable memory record."""

    def recall(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> MemoryRecallResult:
        """Recall durable memories relevant to the current turn."""

    def consolidate(
        self,
        session_id: str,
        transcript_slice: list[SessionMessage],
    ) -> MemoryConsolidationResult:
        """Extract or merge durable memory from a transcript slice."""
