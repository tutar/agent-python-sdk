from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.harness import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.memory import FileMemoryStore
from openagent.object_model import JsonObject, RuntimeEventType, TerminalStatus, ToolResult
from openagent.orchestration import (
    BackgroundTaskContext,
    InMemoryTaskManager,
    LocalBackgroundAgentOrchestrator,
)
from openagent.sandbox import LocalSandbox, SandboxExecutionRequest
from openagent.session import (
    FileSessionStore,
    InMemorySessionStore,
    SessionMessage,
    SessionRecord,
    SessionStatus,
)
from openagent.tools import PermissionDecision, SimpleToolExecutor, StaticToolRegistry, ToolCall


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_conformance_basic_turn() -> None:
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    events, terminal = harness.run_turn("hi", "case_basic")
    session = store.load_session("case_basic")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert session.status is SessionStatus.IDLE
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_conformance_tool_call_roundtrip() -> None:
    tool = FakeTool(name="echo")
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    events, terminal = harness.run_turn("use tool", "case_tool")

    assert terminal.status is TerminalStatus.COMPLETED
    assert RuntimeEventType.TURN_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_RESULT in [event.event_type for event in events]
    assert events[-1].event_type is RuntimeEventType.TURN_COMPLETED


def test_conformance_requires_action_approval() -> None:
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="approved and finished"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, first_terminal = harness.run_turn("please rotate", "case_approval")
    session = store.load_session("case_approval")

    assert first_terminal.status is TerminalStatus.BLOCKED
    assert session.status is SessionStatus.REQUIRES_ACTION
    assert first_events[-1].event_type is RuntimeEventType.REQUIRES_ACTION
    assert first_events[-1].payload["tool_name"] == "admin"

    second_events, second_terminal = harness.continue_turn("case_approval", approved=True)
    resumed = store.load_session("case_approval")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in second_events[:2]] == [
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
    ]
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert resumed.status is SessionStatus.IDLE


def test_conformance_session_resume(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    store = FileSessionStore(session_root)
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first reply")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    first_events, first_terminal = harness.run_turn("first", "case_resume")

    assert first_terminal.status is TerminalStatus.COMPLETED
    assert first_events[-1].event_type is RuntimeEventType.TURN_COMPLETED

    restored_store = FileSessionStore(session_root)
    resumed_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second reply")]),
        sessions=restored_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_events, second_terminal = resumed_harness.run_turn("second", "case_resume")
    restored_session = restored_store.load_session("case_resume")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert [message.content for message in restored_session.messages] == [
        "first",
        "first reply",
        "second",
        "second reply",
    ]


def test_conformance_sandbox_deny() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])
    negotiation = sandbox.negotiate(
        SandboxExecutionRequest(
            command=["bash", "-lc", "echo no"],
            requires_network=True,
        )
    )

    assert negotiation.allowed is False
    assert "Command is not allowed by sandbox policy: bash" in negotiation.reasons
    assert "Network access is not available in this sandbox" in negotiation.reasons

    try:
        sandbox.execute(
            SandboxExecutionRequest(
                command=["bash", "-lc", "echo no"],
                requires_network=True,
            )
        )
    except PermissionError as exc:
        assert "Command is not allowed by sandbox policy: bash" in str(exc)
    else:
        raise AssertionError("Expected PermissionError")


def test_conformance_mcp_tool_adaptation() -> None:
    from openagent.tools import (
        InMemoryMcpClient,
        McpPromptAdapter,
        McpPromptDescriptor,
        McpResourceDescriptor,
        McpServerConnection,
        McpServerDescriptor,
        McpSkillAdapter,
        McpToolAdapter,
        McpToolDescriptor,
    )

    client = InMemoryMcpClient()
    client.connect(
        McpServerConnection(
            descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
            tools={
                "echo": (
                    McpToolDescriptor(name="echo", description="Echo text"),
                    lambda args: ToolResult(
                        tool_name="echo",
                        success=True,
                        content=[str(args["text"])],
                    ),
                )
            },
            prompts={
                "review": McpPromptDescriptor(
                    name="review",
                    description="Review prompt",
                    template="Review {topic}",
                )
            },
            resources={
                "skill://summarize": McpResourceDescriptor(
                    uri="skill://summarize",
                    name="Summarize",
                    description="Summarize notes",
                    content="Summarize {topic}",
                )
            },
        )
    )

    adapted_tool = McpToolAdapter().adapt_mcp_tool("docs", client.list_tools("docs")[0])
    adapted_prompt = McpPromptAdapter().adapt_mcp_prompt("docs", client.list_prompts("docs")[0])
    adapted_skill = McpSkillAdapter().adapt_mcp_skill(
        "docs",
        McpSkillAdapter().discover_skills_from_resources("docs", client.list_resources("docs"))[0],
    )
    result = client.call_tool("docs", "echo", {"text": "hello"})

    assert adapted_tool.name == "echo"
    assert adapted_prompt.kind.value == "prompt"
    assert adapted_prompt.source == "mcp_prompt"
    assert adapted_skill.id == "summarize"
    assert adapted_skill.metadata["server_id"] == "docs"
    assert result.content == ["hello"]


def test_conformance_memory_recall_and_consolidation(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    memory_store = FileMemoryStore(tmp_path / "memory")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="stored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )

    harness.run_turn("Remember that the launch code is sunrise", "case_memory")
    session = store.load_session("case_memory")
    consolidation = memory_store.consolidate("case_memory", session.messages)

    request = harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="What is the launch code?")],
        ),
        [],
    )

    restored_memory_store = FileMemoryStore(tmp_path / "memory")
    restored_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="restored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=restored_memory_store,
    )
    restored_request = restored_harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="launch code?")],
        ),
        [],
    )

    assert consolidation.new_records
    assert request.memory_context
    assert "sunrise" in str(request.memory_context[0]["content"])
    assert request.messages == [{"role": "user", "content": "What is the launch code?"}]
    assert restored_request.memory_context
    assert "sunrise" in str(restored_request.memory_context[0]["content"])


def test_conformance_background_agent() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)

    def worker(context: BackgroundTaskContext) -> JsonObject:
        context.progress({"message": "started"})
        context.checkpoint({"step": "summary"})
        return {"output_ref": "memory://tasks/summary"}

    handle = orchestrator.start_background_task(
        "summarize repo",
        worker,
    )

    initial_events = orchestrator.list_events(handle.task_id)
    assert initial_events[0].event_type.value == "task_created"

    for _ in range(20):
        task = orchestrator.get_task(handle.task_id)
        if task.status is TerminalStatus.COMPLETED:
            break

    events = orchestrator.list_events(handle.task_id)
    task = orchestrator.get_task(handle.task_id)

    assert any(event.event_type.value == "task_progress" for event in events)
    assert events[-1].event_type.value == "task_completed"
    assert task.output_ref == "memory://tasks/summary"
    assert task.status is TerminalStatus.COMPLETED
