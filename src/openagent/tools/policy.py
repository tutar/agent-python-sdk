"""Rule-based policy engine baseline for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.tools.interfaces import ToolDefinition
from openagent.tools.models import (
    PermissionDecision,
    ToolCall,
    ToolExecutionContext,
    ToolPolicyOutcome,
)


@dataclass(slots=True)
class ToolPolicyRule:
    decision: PermissionDecision
    tool_name: str | None = None
    session_id_prefix: str | None = None
    reason: str | None = None

    def matches(self, tool: ToolDefinition, context: ToolExecutionContext) -> bool:
        if self.tool_name is not None and tool.name != self.tool_name:
            return False
        if self.session_id_prefix is not None and not context.session_id.startswith(
            self.session_id_prefix
        ):
            return False
        return True


@dataclass(slots=True)
class RuleBasedToolPolicyEngine:
    rules: list[ToolPolicyRule] = field(default_factory=list)
    fallback_to_tool_policy: bool = True

    def evaluate(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        for rule in self.rules:
            if rule.matches(tool, context):
                return ToolPolicyOutcome(decision=rule.decision, reason=rule.reason)

        if self.fallback_to_tool_policy:
            return ToolPolicyOutcome(
                decision=PermissionDecision(tool.check_permissions(tool_call.arguments)),
            )

        return ToolPolicyOutcome(decision=PermissionDecision.ASK, reason="No matching policy rule")
