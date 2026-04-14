"""Tool execution models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, RuntimeEvent, SerializableModel
from openagent.object_model.models import ToolResult


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(slots=True)
class ToolPolicyOutcome(SerializableModel):
    decision: PermissionDecision
    reason: str | None = None


@dataclass(slots=True)
class ToolCall(SerializableModel):
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    call_id: str | None = None


@dataclass(slots=True)
class ToolExecutionContext(SerializableModel):
    session_id: str
    approved_tool_names: list[str] = field(default_factory=list)
    cancellation_check: Callable[[], bool] | None = None


@dataclass(slots=True)
class ToolProgressUpdate(SerializableModel):
    tool_name: str
    message: str
    progress: float | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ToolStreamItem(SerializableModel):
    progress: ToolProgressUpdate | None = None
    result: ToolResult | None = None


@dataclass(slots=True)
class ToolStreamResult(SerializableModel):
    events: list[RuntimeEvent] = field(default_factory=list)
