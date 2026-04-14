"""Durable memory models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class MemoryRecord(SerializableModel):
    memory_id: str
    session_id: str
    content: str
    summary: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class MemoryRecallResult(SerializableModel):
    query: str
    recalled: list[MemoryRecord] = field(default_factory=list)


@dataclass(slots=True)
class MemoryConsolidationResult(SerializableModel):
    session_id: str
    new_records: list[MemoryRecord] = field(default_factory=list)
    updated_records: list[MemoryRecord] = field(default_factory=list)
