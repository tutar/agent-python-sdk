"""Host configuration models."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from openagent.shared import (
    normalize_openagent_root,
    normalize_workspace_root,
    resolve_agent_root,
    resolve_path_env,
)


@dataclass(slots=True)
class OpenAgentHostConfig:
    openagent_root: str = str(Path(".openagent"))
    agent_root: str = str(Path(".openagent") / "agent_default")
    session_root: str = str(Path(".openagent") / "agent_default" / "sessions")
    binding_root: str = str(Path(".openagent") / "agent_default" / "bindings")
    terminal_host: str = "127.0.0.1"
    terminal_port: int = 8765
    data_root: str = field(default_factory=lambda: str(Path(".openagent") / "data"))
    model_io_root: str = field(
        default_factory=lambda: str(Path(".openagent") / "data" / "model-io")
    )
    workspace_root: str = field(default_factory=os.getcwd)
    preload_channels: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls,
        preload_channels: Iterable[str] = (),
    ) -> OpenAgentHostConfig:
        openagent_root = normalize_openagent_root(os.getenv("OPENAGENT_ROOT"))
        role_id = os.getenv("OPENAGENT_ROLE_ID")
        agent_root = resolve_agent_root(openagent_root, role_id)
        host_root = resolve_path_env("OPENAGENT_HOST_ROOT", agent_root) or agent_root
        data_root = resolve_path_env("OPENAGENT_DATA_ROOT", str(Path(agent_root) / "data"))
        model_io_root = resolve_path_env(
            "OPENAGENT_MODEL_IO_ROOT",
            str(Path(agent_root) / "model-io"),
        )
        session_root = resolve_path_env(
            "OPENAGENT_SESSION_ROOT",
            str(Path(agent_root) / "sessions"),
        )
        binding_root = resolve_path_env(
            "OPENAGENT_BINDING_ROOT",
            str(Path(agent_root) / "bindings"),
        )
        workspace_root = normalize_workspace_root(
            os.getenv("OPENAGENT_WORKSPACE_ROOT"),
            default=os.getcwd(),
        )
        terminal_host = os.getenv("OPENAGENT_TERMINAL_HOST", "127.0.0.1")
        terminal_port = int(os.getenv("OPENAGENT_TERMINAL_PORT", "8765"))
        return cls(
            openagent_root=openagent_root,
            agent_root=str(host_root),
            session_root=str(session_root),
            binding_root=str(binding_root),
            data_root=str(data_root),
            model_io_root=str(model_io_root),
            workspace_root=workspace_root,
            terminal_host=terminal_host,
            terminal_port=terminal_port,
            preload_channels=tuple(preload_channels),
        )
