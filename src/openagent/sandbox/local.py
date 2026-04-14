"""Local sandbox baseline with configurable allowed command prefixes."""

from __future__ import annotations

from dataclasses import dataclass, field
from subprocess import CompletedProcess, run

from openagent.sandbox.models import (
    SandboxCapabilityView,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxNegotiationResult,
)


@dataclass(slots=True)
class LocalSandbox:
    """Minimal sandbox adapter for trusted local execution."""

    allowed_command_prefixes: list[str] = field(default_factory=list)
    supports_network: bool = False
    supports_filesystem_write: bool = False
    available_credentials: list[str] = field(default_factory=list)

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        negotiation = self.negotiate(request)
        if not negotiation.allowed:
            reason = "; ".join(negotiation.reasons) or "Sandbox denied request"
            raise PermissionError(reason)
        completed: CompletedProcess[str] = run(  # noqa: S603
            request.command,
            capture_output=True,
            cwd=request.cwd,
            env=(
                None if not request.env else {key: str(value) for key, value in request.env.items()}
            ),
            text=True,
            check=False,
        )
        return SandboxExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def describe_capabilities(self) -> SandboxCapabilityView:
        return SandboxCapabilityView(
            supports_network=self.supports_network,
            supports_filesystem_write=self.supports_filesystem_write,
            allowed_command_prefixes=self.allowed_command_prefixes,
            available_credentials=self.available_credentials,
        )

    def negotiate(self, request: SandboxExecutionRequest) -> SandboxNegotiationResult:
        reasons: list[str] = []
        if not request.command:
            reasons.append("Sandbox command cannot be empty")
        elif (
            self.allowed_command_prefixes
            and request.command[0] not in self.allowed_command_prefixes
        ):
            reasons.append(f"Command is not allowed by sandbox policy: {request.command[0]}")

        if request.requires_network and not self.supports_network:
            reasons.append("Network access is not available in this sandbox")

        if request.requires_filesystem_write and not self.supports_filesystem_write:
            reasons.append("Filesystem write access is not available in this sandbox")

        missing_credentials = [
            credential
            for credential in request.required_credentials
            if credential not in self.available_credentials
        ]
        if missing_credentials:
            reasons.append(
                "Missing sandbox credentials: " + ", ".join(sorted(missing_credentials))
            )

        return SandboxNegotiationResult(
            allowed=not reasons,
            reasons=reasons,
            granted_network=request.requires_network and self.supports_network,
            granted_filesystem_write=(
                request.requires_filesystem_write and self.supports_filesystem_write
            ),
            granted_credentials=[
                credential
                for credential in request.required_credentials
                if credential in self.available_credentials
            ],
        )
