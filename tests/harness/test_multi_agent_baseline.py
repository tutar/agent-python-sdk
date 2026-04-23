from openagent.harness import (
    DelegatedAgentInvocation,
    DirectViewInput,
    InMemoryTaskManager,
    LocalBackgroundAgentOrchestrator,
    LocalMultiAgentRuntime,
    TaskNotificationRouter,
    TaskRetentionPolicy,
    TaskRetentionRuntime,
)
from openagent.tools import ToolExecutionContext, create_builtin_toolset


def _build_runtime() -> tuple[LocalMultiAgentRuntime, InMemoryTaskManager]:
    manager = InMemoryTaskManager(retention_policy=TaskRetentionPolicy(grace_period_seconds=1.0))
    runtime = LocalMultiAgentRuntime(
        task_manager=manager,
        background_orchestrator=LocalBackgroundAgentOrchestrator(manager),
        retention=TaskRetentionRuntime(manager, TaskRetentionPolicy(grace_period_seconds=1.0)),
        notification_router=TaskNotificationRouter(),
    )
    return runtime, manager


def test_synchronous_delegation_returns_structured_identity_and_result() -> None:
    runtime, _ = _build_runtime()

    result = runtime.delegate(
        DelegatedAgentInvocation(
            prompt="Review the diff",
            parent_session_id="sess_parent",
            invoking_request_id="req_1",
        )
    )

    assert result["mode"] == "synchronous"
    assert result["agent"]["parent_session_id"] == "sess_parent"
    assert result["agent"]["invoking_request_id"] == "req_1"
    assert "output_ref" in result


def test_background_delegation_returns_task_handle_and_identity() -> None:
    runtime, manager = _build_runtime()

    result = runtime.delegate(
        DelegatedAgentInvocation(
            prompt="Investigate the failure",
            run_in_background=True,
            parent_session_id="sess_parent",
            invoking_request_id="req_bg",
        )
    )

    task_id = str(result["task_id"])
    task = manager.await_task(task_id, timeout=2.0)

    assert result["mode"] == "background"
    assert result["agent"]["invocation_kind"] == "background"
    assert task.session_id == "sess_parent"
    assert task.metadata is not None
    assert task.metadata["invoking_request_id"] == "req_bg"


def test_task_notification_router_scopes_by_recipient() -> None:
    runtime, manager = _build_runtime()
    result = runtime.delegate(
        DelegatedAgentInvocation(
            prompt="Background review",
            run_in_background=True,
            parent_session_id="sess_a",
        )
    )
    task_id = str(result["task_id"])
    manager.await_task(task_id, timeout=2.0)

    notifications_a = runtime.sync_task_notifications("sess_a")
    notifications_b = runtime.sync_task_notifications("sess_b")

    assert len(notifications_a) == 1
    assert notifications_a[0]["recipient"]["recipient_id"] == "sess_a"
    assert notifications_b == []


def test_direct_view_input_isolated_to_target_worker() -> None:
    runtime, _ = _build_runtime()

    runtime.send_direct_view_input(
        DirectViewInput(recipient_id="worker_a", sender_id="leader", content="focus this file")
    )

    assert len(runtime.read_direct_view_inputs("worker_a")) == 1
    assert runtime.read_direct_view_inputs("worker_b") == []


def test_viewed_transcript_projection_uses_task_events_and_output() -> None:
    runtime, manager = _build_runtime()
    result = runtime.delegate(
        DelegatedAgentInvocation(
            prompt="Background review",
            run_in_background=True,
            parent_session_id="sess_a",
        )
    )
    task_id = str(result["task_id"])
    manager.await_task(task_id, timeout=2.0)

    projected = runtime.project_view(task_id)

    assert projected.task_id == task_id
    assert any(entry.source == "task_event" for entry in projected.entries)
    assert any(entry.source == "task_output" for entry in projected.entries)


def test_viewed_transcript_holds_and_releases_retention() -> None:
    runtime, manager = _build_runtime()
    result = runtime.delegate(
        DelegatedAgentInvocation(
            prompt="Background review",
            run_in_background=True,
            parent_session_id="sess_a",
        )
    )
    task_id = str(result["task_id"])
    manager.await_task(task_id, timeout=2.0)

    opened = runtime.open_view(task_id, "terminal:chat:view_1")
    runtime.close_view(task_id, "terminal:chat:view_1")
    closed = runtime.project_view(task_id)

    assert opened.retained is True
    assert closed.retained is False


def test_agent_tool_maps_to_local_multi_agent_runtime() -> None:
    runtime, _ = _build_runtime()
    toolset = {
        tool.name: tool for tool in create_builtin_toolset(agent_handler=runtime.as_agent_handler())
    }
    result = toolset["Agent"].call(
        {"task": "Summarize the current state", "run_in_background": False},
        ToolExecutionContext(session_id="sess_tool", task_id="task_parent"),
    )

    assert result.structured_content is not None
    linkage = result.structured_content["agent_linkage"]
    assert linkage["mode"] == "synchronous"
    assert linkage["agent"]["parent_session_id"] == "sess_tool"
