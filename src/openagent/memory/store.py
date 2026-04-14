"""In-memory and file-backed durable memory baselines."""

from __future__ import annotations

import json
from pathlib import Path

from openagent.memory.models import MemoryConsolidationResult, MemoryRecallResult, MemoryRecord
from openagent.session import SessionMessage


class InMemoryMemoryStore:
    """Persist durable memory records in memory for tests and local runtime use."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._counter = 0

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.memory_id] = record
        return record

    def recall(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> MemoryRecallResult:
        del session_id
        tokens = {token for token in query.lower().split() if token}
        scored: list[tuple[int, MemoryRecord]] = []
        for record in self._records.values():
            haystack = f"{record.content} {record.summary}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score > 0 or not tokens:
                scored.append((score, record))
        recalled = [record for _, record in sorted(scored, key=lambda item: item[0], reverse=True)]
        return MemoryRecallResult(query=query, recalled=recalled[:limit])

    def consolidate(
        self,
        session_id: str,
        transcript_slice: list[SessionMessage],
    ) -> MemoryConsolidationResult:
        if not transcript_slice:
            return MemoryConsolidationResult(session_id=session_id)
        self._counter += 1
        content = "\n".join(f"{message.role}: {message.content}" for message in transcript_slice)
        summary = transcript_slice[-1].content[:120]
        record = MemoryRecord(
            memory_id=f"memory_{self._counter}",
            session_id=session_id,
            content=content,
            summary=summary,
            metadata={"message_count": len(transcript_slice)},
        )
        self._records[record.memory_id] = record
        return MemoryConsolidationResult(session_id=session_id, new_records=[record])


class FileMemoryStore(InMemoryMemoryStore):
    """Persist durable memory records to disk for restart-safe recall."""

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        stored = super().upsert_memory(record)
        self._write_record(stored)
        return stored

    def consolidate(
        self,
        session_id: str,
        transcript_slice: list[SessionMessage],
    ) -> MemoryConsolidationResult:
        result = super().consolidate(session_id, transcript_slice)
        for record in result.new_records:
            self._write_record(record)
        return result

    def _record_path(self, memory_id: str) -> Path:
        return self._root / f"{memory_id}.json"

    def _write_record(self, record: MemoryRecord) -> None:
        self._record_path(record.memory_id).write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _load_existing(self) -> None:
        max_counter = 0
        for path in sorted(self._root.glob("memory_*.json")):
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._records[record.memory_id] = record
            try:
                max_counter = max(max_counter, int(record.memory_id.removeprefix("memory_")))
            except ValueError:
                continue
        self._counter = max_counter
