"""Helpers for resolving OpenAgent local state layout."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

DEFAULT_AGENT_DIRECTORY = "agent_default"


def normalize_openagent_root(root: str | None, *, default: str = ".openagent") -> str:
    candidate = root if root is not None else default
    expanded = os.path.expanduser(os.path.expandvars(candidate))
    return str(Path(expanded).resolve())


def resolve_agent_directory(role_id: str | None) -> str:
    if isinstance(role_id, str) and role_id.strip():
        return f"agent_{role_id.strip()}"
    return DEFAULT_AGENT_DIRECTORY


def resolve_agent_root(openagent_root: str, role_id: str | None = None) -> str:
    return str(Path(openagent_root).resolve() / resolve_agent_directory(role_id))


def resolve_agent_root_from_session_root(session_root: str) -> str:
    session_path = Path(session_root).resolve()
    if session_path.name == "sessions":
        return str(session_path.parent)
    return str(session_path)


def resolve_session_workspace(session_root: str, session_id: str) -> str:
    return str(Path(session_root).resolve() / session_id / "workspace")


def resolve_subagent_root(agent_root: str, subagent_id: str) -> Path:
    return Path(agent_root).resolve() / "subagents" / subagent_id


def resolve_subagent_workspace(agent_root: str, subagent_id: str) -> str:
    return str(resolve_subagent_root(agent_root, subagent_id) / "workspace")


def ensure_directory(path: str | Path) -> str:
    resolved = Path(path).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def ensure_session_workspace(session_root: str, session_id: str) -> str:
    return ensure_directory(resolve_session_workspace(session_root, session_id))


def ensure_subagent_workspace(
    agent_root: str,
    subagent_id: str,
    *,
    parent_workspace: str | None = None,
) -> str:
    workspace = Path(resolve_subagent_workspace(agent_root, subagent_id))
    if not workspace.exists():
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if parent_workspace is not None and Path(parent_workspace).exists():
            shutil.copytree(parent_workspace, workspace)
        else:
            workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace.resolve())


def write_subagent_ref(
    agent_root: str,
    subagent_id: str,
    *,
    parent_session_id: str | None,
    workspace: str,
    metadata: dict[str, object] | None = None,
) -> None:
    root = resolve_subagent_root(agent_root, subagent_id)
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "subagent_id": subagent_id,
        "parent_session_id": parent_session_id,
        "workspace": str(Path(workspace).resolve()),
        "metadata": metadata or {},
    }
    (root / "agent.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    index_path = Path(agent_root).resolve() / "subagents" / "children.json"
    children: dict[str, dict[str, object]] = {}
    if index_path.exists():
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            children = {
                str(key): value for key, value in raw.items() if isinstance(value, dict)
            }
    children[subagent_id] = payload
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(children, indent=2), encoding="utf-8")
