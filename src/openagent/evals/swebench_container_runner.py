"""Container-side worker for a single SWE-bench inference instance."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Sequence

from openagent.evals.swebench import (
    build_agent_prompt,
    copy_runtime_artifacts,
    prime_runtime_session,
    resolve_session_artifact_paths,
    write_json,
)
from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime.core.terminal import TurnControl
from openagent.local import create_file_runtime
from openagent.tools import create_local_code_edit_toolset


def ensure_workspace_compat_links(repo_dir: Path) -> None:
    compat_root = repo_dir / repo_dir.name
    compat_root.mkdir(exist_ok=True)
    for child in repo_dir.iterdir():
        if child.name == compat_root.name:
            continue
        destination = compat_root / child.name
        if destination.exists() or destination.is_symlink():
            continue
        destination.symlink_to(child, target_is_directory=child.is_dir())


def git_capture(repo_dir: Path, *args: str) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=str(repo_dir),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def run_instance(
    *,
    instance: dict[str, Any],
    repo_dir: Path,
    output_dir: Path,
    max_iterations: int,
    turn_timeout_sec: float | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_workspace_compat_links(repo_dir)
    openagent_root = output_dir / "openagent"
    session_root = openagent_root / "sessions"
    model = load_model_from_env()
    runtime = create_file_runtime(
        model=model,
        session_root=str(session_root),
        tools=create_local_code_edit_toolset(root=str(repo_dir)),
        include_agent_tool=False,
        include_skill_tool=False,
        openagent_root=str(openagent_root),
        model_io_root=str(openagent_root / "agent_default" / "local-agent" / "model-io"),
    )
    runtime.max_iterations = max_iterations
    session_id = f"swebench-{instance['instance_id']}"
    prime_runtime_session(runtime, session_id=session_id, repo_dir=repo_dir)
    prompt = build_agent_prompt(instance)
    control = TurnControl(timeout_seconds=turn_timeout_sec)
    events, terminal = runtime.run_turn(prompt, session_id, control=control)
    patch_text = git_capture(repo_dir, "-c", "core.fileMode=false", "diff")
    patch_path = output_dir / "patch.diff"
    patch_path.write_text(patch_text, encoding="utf-8")
    git_status = git_capture(repo_dir, "status", "--short")
    (output_dir / "git_status.txt").write_text(git_status, encoding="utf-8")
    artifact_paths = resolve_session_artifact_paths(runtime, session_id)
    copied = copy_runtime_artifacts(artifact_paths, output_dir)
    result = {
        "instance_id": instance["instance_id"],
        "repo": instance.get("repo"),
        "base_commit": instance.get("base_commit"),
        "terminal_status": str(terminal.status.value if hasattr(terminal.status, "value") else terminal.status),
        "terminal_reason": terminal.reason,
        "event_count": len(events),
        "patch_path": str(patch_path),
        "artifacts": copied,
    }
    write_json(output_dir / "agent_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m openagent.evals.swebench_container_runner")
    parser.add_argument("--instance-json", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-iterations", type=int, default=24)
    parser.add_argument("--turn-timeout-sec", type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    instance = json.loads(Path(args.instance_json).read_text(encoding="utf-8"))
    run_instance(
        instance=instance,
        repo_dir=Path(args.repo_dir).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        max_iterations=args.max_iterations,
        turn_timeout_sec=args.turn_timeout_sec,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
