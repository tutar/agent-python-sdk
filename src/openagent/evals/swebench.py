"""SWE-bench Verified inference driver for OpenAgent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime.core.terminal import TurnControl
from openagent.local import create_file_runtime
from openagent.object_model import TerminalState
from openagent.session import SessionRecord
from openagent.shared import DEFAULT_RUNTIME_AGENT_ID, resolve_agent_transcript_path
from openagent.tools import create_local_code_edit_toolset

REPO_DIR_IN_CONTAINER = "/testbed"
RUN_ROOT_ENV = "OPENAGENT_SWEBENCH_RUN_ROOT"


class SwebenchDatasetLoader(Protocol):
    def __call__(
        self,
        name: str,
        split: str,
        instance_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class DriverConfig:
    dataset_name: str
    split: str
    run_id: str
    output_root: Path
    swebench_root: Path
    openagent_source_root: Path
    max_workers: int
    max_iterations: int
    turn_timeout_sec: float | None
    container_timeout_sec: float | None
    model_name_for_report: str
    instance_ids: list[str] | None = None
    prepare_images: bool = False
    docker_binary: str = "docker"
    python_executable: str = sys.executable


@dataclass(slots=True)
class InstancePaths:
    root: Path
    input_dir: Path
    output_dir: Path
    openagent_root: Path
    patch_path: Path
    result_path: Path
    log_path: Path
    instance_json: Path


@dataclass(slots=True)
class InferenceOutcome:
    instance_id: str
    model_patch: str
    result: dict[str, Any]

    def prediction_record(self, model_name_or_path: str) -> dict[str, str]:
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": model_name_or_path,
            "model_patch": self.model_patch,
        }


def build_agent_prompt(instance: dict[str, Any]) -> str:
    problem_statement = str(instance.get("problem_statement", "")).strip()
    repo = str(instance.get("repo", "")).strip()
    base_commit = str(instance.get("base_commit", "")).strip()
    hints_text = str(instance.get("hints_text", "") or "").strip()
    prompt = [
        "You are working on a SWE-bench task inside the repository checkout at the target base commit.",
        f"Repository: {repo}" if repo else "Repository: unknown",
        f"Base commit: {base_commit}" if base_commit else "Base commit: unknown",
        "",
        "Issue:",
        problem_statement or "(missing problem statement)",
        "",
        "Requirements:",
        "- Modify the repository directly to fix the issue.",
        "- Run focused validation commands when needed to increase confidence.",
        "- All file tool paths are relative to the workspace root. Do not prefix paths with 'testbed/' or '/testbed/'.",
        "- After identifying the likely fix location, prefer editing and validating over repeated repository exploration.",
        "- Do not ask for clarification, approval, or additional input.",
        "- Do not commit changes or create branches.",
        "- Stop once the fix is implemented and validated as far as possible in this environment.",
        "- Your final response should be short and summarize the change and validation.",
    ]
    if "DefaultPrinting" in hints_text:
        prompt.extend(
            [
                "- Follow the hint concretely: inspect `sympy/printing/defaults.py` and `sympy/core/_print_helpers.py` before broader searching.",
                "- `DefaultPrinting` may be an alias rather than a `class` declaration, so search for the symbol name as well as class definitions.",
            ]
        )
    if hints_text:
        prompt.extend(["", "Hints:", hints_text])
    return "\n".join(prompt)


def sanitize_image_fragment(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "-" for char in value.lower())
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:80] or "default"


def make_instance_paths(run_root: Path, instance_id: str) -> InstancePaths:
    root = run_root / "instances" / instance_id
    input_dir = root / "input"
    output_dir = root / "output"
    openagent_root = output_dir / "openagent"
    return InstancePaths(
        root=root,
        input_dir=input_dir,
        output_dir=output_dir,
        openagent_root=openagent_root,
        patch_path=output_dir / "patch.diff",
        result_path=output_dir / "agent_result.json",
        log_path=root / "container.log",
        instance_json=input_dir / "instance.json",
    )


def ensure_runner_build_context(run_root: Path, openagent_source_root: Path) -> Path:
    context_root = run_root / "runner-build-context"
    src_root = openagent_source_root / "src"
    context_src = context_root / "src"
    context_src.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(openagent_source_root / "pyproject.toml", context_root / "pyproject.toml")
    shutil.copy2(openagent_source_root / "README.md", context_root / "README.md")
    if context_src.exists():
        shutil.rmtree(context_src)
    shutil.copytree(src_root, context_src)
    return context_root


def build_context_hash(build_context: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(build_context.rglob("*")):
        if path.is_dir():
            continue
        hasher.update(str(path.relative_to(build_context)).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()[:12]


def add_swebench_to_sys_path(swebench_root: Path) -> None:
    root = str(swebench_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def load_swebench_helpers(
    swebench_root: Path,
) -> tuple[SwebenchDatasetLoader, Any]:
    add_swebench_to_sys_path(swebench_root)
    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.utils import load_swebench_dataset

    return load_swebench_dataset, make_test_spec


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def prime_runtime_session(
    runtime: Any,
    *,
    session_id: str,
    repo_dir: Path,
    target_path: Path | None = None,
) -> SessionRecord:
    session = runtime.sessions.load_session(session_id)
    metadata = dict(session.metadata or {})
    metadata["workdir"] = str(repo_dir.resolve())
    metadata["target_path"] = str((target_path or repo_dir).resolve())
    if runtime.agent_root_dir is not None:
        metadata["agent_root_dir"] = str(Path(runtime.agent_root_dir).resolve())
    if runtime.role_id is not None:
        metadata["role_id"] = runtime.role_id
    session.metadata = metadata
    session.agent_id = session.agent_id or DEFAULT_RUNTIME_AGENT_ID
    runtime.sessions.save_session(session_id, session)
    return session


def resolve_session_artifact_paths(runtime: Any, session_id: str) -> dict[str, Path]:
    transcript_path = resolve_agent_transcript_path(
        runtime.agent_root_dir or "",
        DEFAULT_RUNTIME_AGENT_ID,
    )
    session_root = Path(runtime.sessions.root_dir).resolve() / session_id
    model_io_root = Path(runtime.model_io_capture.root_dir).resolve()
    return {
        "transcript": transcript_path,
        "events": session_root / "events.jsonl",
        "session_state": session_root / "state.json",
        "model_io": model_io_root,
    }


def copy_runtime_artifacts(artifact_paths: dict[str, Path], output_dir: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, source in artifact_paths.items():
        destination = output_dir / (
            "model_io" if key == "model_io" else source.name if key != "session_state" else "state.json"
        )
        if source.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
        elif source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        else:
            continue
        copied[key] = str(destination)
    return copied


def collect_git_diff(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "core.fileMode=false", "diff"],
        cwd=str(repo_dir),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout


def run_local_instance(
    *,
    instance: dict[str, Any],
    repo_dir: Path,
    output_dir: Path,
    model: Any | None = None,
    max_iterations: int = 8,
    turn_timeout_sec: float | None = None,
) -> InferenceOutcome:
    output_dir.mkdir(parents=True, exist_ok=True)
    openagent_root = output_dir / "openagent"
    session_root = openagent_root / "sessions"
    session_id = f"swebench-{instance['instance_id']}"
    runtime_model = model if model is not None else load_model_from_env()
    runtime = create_file_runtime(
        model=runtime_model,
        session_root=str(session_root),
        tools=create_local_code_edit_toolset(root=str(repo_dir)),
        include_agent_tool=False,
        include_skill_tool=False,
        openagent_root=str(openagent_root),
        model_io_root=str(openagent_root / "agent_default" / "local-agent" / "model-io"),
    )
    runtime.max_iterations = max_iterations
    prime_runtime_session(runtime, session_id=session_id, repo_dir=repo_dir)
    prompt = build_agent_prompt(instance)
    control = TurnControl(timeout_seconds=turn_timeout_sec)
    events, terminal = runtime.run_turn(prompt, session_id, control=control)
    patch_text = collect_git_diff(repo_dir)
    patch_path = output_dir / "patch.diff"
    patch_path.write_text(patch_text, encoding="utf-8")
    artifact_paths = resolve_session_artifact_paths(runtime, session_id)
    copied = copy_runtime_artifacts(artifact_paths, output_dir)
    result = {
        "instance_id": instance["instance_id"],
        "terminal_status": str(terminal.status.value if hasattr(terminal.status, "value") else terminal.status),
        "terminal_reason": terminal.reason,
        "event_count": len(events),
        "patch_path": str(patch_path),
        "artifacts": copied,
    }
    write_json(output_dir / "agent_result.json", result)
    return InferenceOutcome(
        instance_id=str(instance["instance_id"]),
        model_patch=patch_text,
        result=result,
    )


def docker_env_passthrough() -> list[str]:
    explicit = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "FIRECRAWL_API_KEY",
        "TAVILY_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "GOOGLE_API_KEY",
    }
    prefixes = ("OPENAGENT_",)
    variables: list[str] = []
    for key, value in os.environ.items():
        if not value:
            continue
        if key in explicit or any(key.startswith(prefix) for prefix in prefixes):
            variables.append(key)
    return sorted(set(variables))


def containerized_env_value(name: str, value: str) -> str:
    if name != "OPENAGENT_BASE_URL":
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    hostname = parsed.hostname
    if hostname not in {"127.0.0.1", "localhost"}:
        return value
    netloc = "host.docker.internal"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def runner_image_name(instance_image_key: str, build_hash: str) -> str:
    digest = hashlib.sha256(instance_image_key.encode("utf-8")).hexdigest()[:12]
    return (
        f"openagent.infer.{sanitize_image_fragment(instance_image_key)}-"
        f"{digest}-{build_hash}:latest"
    )


def docker_image_exists(image_name: str, docker_binary: str) -> bool:
    result = subprocess.run(
        [docker_binary, "image", "inspect", image_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ensure_runner_image(
    *,
    instance_image_key: str,
    build_context: Path,
    run_root: Path,
    docker_binary: str,
) -> str:
    image_name = runner_image_name(instance_image_key, build_context_hash(build_context))
    if docker_image_exists(image_name, docker_binary):
        return image_name
    dockerfile_dir = run_root / "dockerfiles"
    dockerfile_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = dockerfile_dir / f"{sanitize_image_fragment(instance_image_key)}.Dockerfile"
    dockerfile_path.write_text(
        "\n".join(
            [
                f"FROM {instance_image_key}",
                "WORKDIR /opt/open-agent",
                "RUN apt-get update && apt-get install -y python3.11 python3.11-distutils python3-pip && rm -rf /var/lib/apt/lists/*",
                'RUN python3.11 -m pip install --no-cache-dir "lark-oapi>=1.5.3"',
                "COPY src /opt/open-agent/src",
                "COPY pyproject.toml /opt/open-agent/pyproject.toml",
                "COPY README.md /opt/open-agent/README.md",
                "ENV PYTHONPATH=/opt/open-agent/src",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            docker_binary,
            "build",
            "-f",
            str(dockerfile_path),
            "-t",
            image_name,
            str(build_context),
        ],
        check=True,
    )
    return image_name


def ensure_remote_instance_image(
    *,
    instance_image_key: str,
    docker_binary: str,
) -> None:
    if docker_image_exists(instance_image_key, docker_binary):
        return
    subprocess.run([docker_binary, "pull", instance_image_key], check=True)


def repair_path_permissions(
    *,
    target_dir: Path,
    image_name: str,
    docker_binary: str,
) -> None:
    subprocess.run(
        [
            docker_binary,
            "run",
            "--rm",
            "-v",
            f"{target_dir.resolve()}:/oa-run/output",
            image_name,
            "/bin/bash",
            "-lc",
            f"chown -R {os.getuid()}:{os.getgid()} /oa-run/output",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_containerized_instance(
    *,
    instance: dict[str, Any],
    test_spec: Any,
    config: DriverConfig,
    build_context: Path,
    run_root: Path,
) -> InferenceOutcome:
    instance_id = str(instance["instance_id"])
    paths = make_instance_paths(run_root, instance_id)
    ensure_remote_instance_image(
        instance_image_key=test_spec.instance_image_key,
        docker_binary=config.docker_binary,
    )
    if paths.root.exists():
        repair_path_permissions(
            target_dir=paths.root,
            image_name=test_spec.instance_image_key,
            docker_binary=config.docker_binary,
        )
        shutil.rmtree(paths.root)
    paths.input_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.instance_json, instance)
    image_name = ensure_runner_image(
        instance_image_key=test_spec.instance_image_key,
        build_context=build_context,
        run_root=run_root,
        docker_binary=config.docker_binary,
    )
    docker_command = [
        config.docker_binary,
        "run",
        "--rm",
        "--name",
        f"openagent-swebench-{sanitize_image_fragment(instance_id)}",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-e",
        f"{RUN_ROOT_ENV}=/oa-run/output",
        "-v",
        f"{paths.input_dir.resolve()}:/oa-run/input",
        "-v",
        f"{paths.output_dir.resolve()}:/oa-run/output",
    ]
    for env_var in docker_env_passthrough():
        docker_command.extend(
            ["-e", f"{env_var}={containerized_env_value(env_var, os.environ[env_var])}"]
        )
    inner_command = (
        "source /root/.bashrc 2>/dev/null || true && "
        "python3.11 -m openagent.evals.swebench_container_runner "
        "--instance-json /oa-run/input/instance.json "
        f"--repo-dir {REPO_DIR_IN_CONTAINER} "
        "--output-dir /oa-run/output "
    )
    inner_command += f"--max-iterations {config.max_iterations} "
    if config.turn_timeout_sec is not None:
        inner_command += f"--turn-timeout-sec {config.turn_timeout_sec} "
    docker_command.extend([image_name, "/bin/bash", "-lc", inner_command.strip()])
    try:
        with paths.log_path.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(
                docker_command,
                check=False,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=config.container_timeout_sec,
            )
    finally:
        if paths.output_dir.exists():
            repair_path_permissions(
                target_dir=paths.output_dir,
                image_name=image_name,
                docker_binary=config.docker_binary,
            )
    if completed.returncode != 0 and not paths.result_path.exists():
        failure_result = {
            "instance_id": instance_id,
            "terminal_status": "failed",
            "terminal_reason": "container_failed",
            "exit_code": completed.returncode,
            "log_path": str(paths.log_path),
        }
        write_json(paths.result_path, failure_result)
    result_payload = json.loads(paths.result_path.read_text(encoding="utf-8"))
    patch_text = paths.patch_path.read_text(encoding="utf-8") if paths.patch_path.exists() else ""
    return InferenceOutcome(instance_id=instance_id, model_patch=patch_text, result=result_payload)


def run_containerized_instance_safely(
    *,
    instance: dict[str, Any],
    test_spec: Any,
    config: DriverConfig,
    build_context: Path,
    run_root: Path,
) -> InferenceOutcome:
    instance_id = str(instance["instance_id"])
    paths = make_instance_paths(run_root, instance_id)
    try:
        return run_containerized_instance(
            instance=instance,
            test_spec=test_spec,
            config=config,
            build_context=build_context,
            run_root=run_root,
        )
    except subprocess.TimeoutExpired as exc:
        failure_result = {
            "instance_id": instance_id,
            "terminal_status": "failed",
            "terminal_reason": "container_timeout",
            "timeout_seconds": exc.timeout,
            "log_path": str(paths.log_path),
        }
    except Exception as exc:
        failure_result = {
            "instance_id": instance_id,
            "terminal_status": "failed",
            "terminal_reason": "driver_exception",
            "error": str(exc),
            "log_path": str(paths.log_path),
        }
    write_json(paths.result_path, failure_result)
    return InferenceOutcome(instance_id=instance_id, model_patch="", result=failure_result)


def write_predictions(path: Path, outcomes: Iterable[InferenceOutcome], model_name_or_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for outcome in outcomes:
            handle.write(json.dumps(outcome.prediction_record(model_name_or_path)) + "\n")


def write_summary(path: Path, outcomes: Sequence[InferenceOutcome]) -> None:
    summary = {
        "total_instances": len(outcomes),
        "patches_generated": sum(1 for item in outcomes if item.model_patch.strip()),
        "empty_patches": sum(1 for item in outcomes if not item.model_patch.strip()),
        "failed_instances": sum(
            1
            for item in outcomes
            if str(item.result.get("terminal_status", "")).lower() in {"failed", "stopped"}
        ),
    }
    write_json(path, summary)


def run_driver(config: DriverConfig) -> Path:
    load_dataset, make_test_spec = load_swebench_helpers(config.swebench_root)
    dataset = load_dataset(config.dataset_name, config.split, config.instance_ids)
    run_root = config.output_root / config.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    build_context = ensure_runner_build_context(run_root, config.openagent_source_root)
    outcomes: list[InferenceOutcome] = []
    with ThreadPoolExecutor(max_workers=max(1, config.max_workers)) as executor:
        futures = {
            executor.submit(
                run_containerized_instance_safely,
                instance=instance,
                test_spec=make_test_spec(
                    instance,
                    namespace="swebench",
                    base_image_tag="latest",
                    env_image_tag="latest",
                    instance_image_tag="latest",
                ),
                config=config,
                build_context=build_context,
                run_root=run_root,
            ): str(instance["instance_id"])
            for instance in dataset
        }
        for future in as_completed(futures):
            outcomes.append(future.result())
    outcomes.sort(key=lambda item: item.instance_id)
    predictions_path = run_root / "predictions.jsonl"
    write_predictions(predictions_path, outcomes, config.model_name_for_report)
    write_summary(run_root / "summary.json", outcomes)
    return predictions_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openagent-swebench-verified-infer")
    parser.add_argument("--dataset_name", default="SWE-bench/SWE-bench_Verified")
    parser.add_argument("--split", default="test")
    parser.add_argument("--instance_ids", nargs="+")
    parser.add_argument("--run_id", required=True)
    parser.add_argument(
        "--output_root",
        default="artifacts/swebench",
        help="Directory that will contain run outputs.",
    )
    parser.add_argument(
        "--swebench_root",
        default=str(Path(__file__).resolve().parents[4] / "SWE-bench"),
        help="Path to the SWE-bench checkout.",
    )
    parser.add_argument(
        "--openagent_source_root",
        default=str(Path(__file__).resolve().parents[3]),
        help="Path to the open-agent repository root.",
    )
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--max_iterations", type=int, default=24)
    parser.add_argument("--turn_timeout_sec", type=float)
    parser.add_argument("--container_timeout_sec", type=float)
    parser.add_argument("--model_name_for_report", default="openagent")
    parser.add_argument("--prepare_images", action="store_true")
    parser.add_argument("--docker_binary", default="docker")
    parser.add_argument("--python_executable", default=sys.executable)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DriverConfig(
        dataset_name=args.dataset_name,
        split=args.split,
        run_id=args.run_id,
        output_root=Path(args.output_root).resolve(),
        swebench_root=Path(args.swebench_root).resolve(),
        openagent_source_root=Path(args.openagent_source_root).resolve(),
        max_workers=args.max_workers,
        max_iterations=args.max_iterations,
        turn_timeout_sec=args.turn_timeout_sec,
        container_timeout_sec=args.container_timeout_sec,
        model_name_for_report=args.model_name_for_report,
        instance_ids=list(args.instance_ids) if args.instance_ids else None,
        prepare_images=bool(args.prepare_images),
        docker_binary=args.docker_binary,
        python_executable=args.python_executable,
    )
    predictions_path = run_driver(config)
    print(predictions_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
