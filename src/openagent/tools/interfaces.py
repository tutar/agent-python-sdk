"""Tool interface definitions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from openagent.object_model import RuntimeEvent, ToolResult
from openagent.tools.models import (
    ToolCall,
    ToolExecutionContext,
    ToolPolicyOutcome,
    ToolStreamItem,
)


class ToolDefinition(Protocol):
    name: str
    input_schema: dict[str, Any]

    def description(self) -> str:
        """Return a human-readable tool description."""

    def call(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the tool with validated arguments."""

    def check_permissions(self, arguments: dict[str, Any]) -> str:
        """Return allow, deny, or ask."""

    def is_concurrency_safe(self) -> bool:
        """Return whether this tool can run concurrently."""


class StreamingToolDefinition(ToolDefinition, Protocol):
    def stream_call(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        """Execute the tool with incremental progress and final result."""


class ToolRegistry(Protocol):
    def list_tools(self) -> list[ToolDefinition]:
        """List registered tools."""

    def resolve_tool(self, name: str) -> ToolDefinition:
        """Resolve a tool by name."""

    def filter_visible_tools(self, policy: Any, runtime: Any) -> list[ToolDefinition]:
        """Return visible tools for the current policy and runtime state."""


class ToolPolicyEngine(Protocol):
    def evaluate(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        """Return the effective policy outcome for the tool call."""


class ToolExecutor(Protocol):
    def run_tool_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        """Run tools and yield tool lifecycle events."""

    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """Run a batch of tool calls under the provided execution context."""
