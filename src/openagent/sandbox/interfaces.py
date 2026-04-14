"""Sandbox interface definitions."""

from __future__ import annotations

from typing import Protocol

from openagent.sandbox.models import (
    SandboxCapabilityView,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxNegotiationResult,
)


class Sandbox(Protocol):
    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        """Execute a sandboxed request."""

    def describe_capabilities(self) -> SandboxCapabilityView:
        """Return the current sandbox capability view."""

    def negotiate(self, request: SandboxExecutionRequest) -> SandboxNegotiationResult:
        """Return whether the sandbox can execute the requested command and capabilities."""
