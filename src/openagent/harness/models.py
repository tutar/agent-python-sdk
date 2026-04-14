"""Harness-local response and adapter models."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from openagent.object_model import JsonObject, SerializableModel
from openagent.tools import ToolCall


@dataclass(slots=True)
class ModelTurnRequest(SerializableModel):
    session_id: str
    messages: list[JsonObject]
    available_tools: list[str] = field(default_factory=list)
    memory_context: list[JsonObject] = field(default_factory=list)


@dataclass(slots=True)
class ModelTurnResponse(SerializableModel):
    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(slots=True)
class ModelStreamEvent(SerializableModel):
    assistant_delta: str | None = None
    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelAdapter(Protocol):
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        """Produce the next model response for the current turn."""


class StreamingModelAdapter(ModelAdapter, Protocol):
    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        """Produce streamed model events for the current turn."""


@dataclass(slots=True)
class TurnControl:
    timeout_seconds: float | None = None
    max_retries: int = 0
    cancellation_check: Callable[[], bool] | None = None


@dataclass(slots=True)
class TurnStreamResult(SerializableModel):
    events: list[JsonObject]
    terminal_state: JsonObject
