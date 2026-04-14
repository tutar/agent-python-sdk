from dataclasses import dataclass, field

from openagent.harness import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.harness.providers import (
    AnthropicMessagesModelAdapter,
    OpenAIChatCompletionsModelAdapter,
)
from openagent.harness.providers.base import HttpResponse
from openagent.object_model import JsonObject, ToolResult
from openagent.session import InMemorySessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolCall


@dataclass(slots=True)
class FakeTransport:
    response_body: JsonObject
    seen_url: str | None = None
    seen_payload: JsonObject | None = None
    seen_headers: dict[str, str] = field(default_factory=dict)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        return HttpResponse(status_code=200, body=self.response_body)


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, object] = field(
        default_factory=lambda: {"type": "object", "properties": {"text": {"type": "string"}}}
    )

    def description(self) -> str:
        return "Echo the provided text."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments.get("text"))])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return "allow"

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class ToolThenReplyModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_openai_chat_adapter_builds_tool_payload_and_parses_tool_calls() -> None:
    transport = FakeTransport(
        response_body={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"text": "payload"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    response = adapter.generate(
        ModelTurnRequest(
            session_id="sess_1",
            messages=[{"role": "user", "content": "use echo"}],
            tool_definitions=[
                {
                    "name": "echo",
                    "description": "Echo text.",
                    "input_schema": {"type": "object"},
                }
            ],
        )
    )

    assert transport.seen_url == "http://127.0.0.1:8001/v1/chat/completions"
    assert transport.seen_payload is not None
    assert transport.seen_payload["model"] == "gpt-test"
    assert isinstance(transport.seen_payload["tools"], list)
    assert transport.seen_payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text.",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="call_1")
    ]


def test_anthropic_adapter_builds_tool_payload_and_parses_tool_use() -> None:
    transport = FakeTransport(
        response_body={
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "echo",
                    "input": {"text": "payload"},
                },
            ]
        }
    )
    adapter = AnthropicMessagesModelAdapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    response = adapter.generate(
        ModelTurnRequest(
            session_id="sess_1",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "use echo"},
                {
                    "role": "tool",
                    "content": "done",
                    "metadata": {"tool_use_id": "toolu_prev"},
                },
            ],
            tool_definitions=[
                {
                    "name": "echo",
                    "description": "Echo text.",
                    "input_schema": {"type": "object"},
                }
            ],
        )
    )

    assert transport.seen_url == "http://127.0.0.1:8001/v1/messages"
    assert transport.seen_payload is not None
    assert transport.seen_payload["system"] == "system prompt"
    assert isinstance(transport.seen_payload["tools"], list)
    assert isinstance(transport.seen_payload["messages"], list)
    assert transport.seen_payload["tools"] == [
        {
            "name": "echo",
            "description": "Echo text.",
            "input_schema": {"type": "object"},
        }
    ]
    messages_payload = transport.seen_payload["messages"]
    assert isinstance(messages_payload, list)
    assert messages_payload[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_prev", "content": "done"}],
    }
    assert response.assistant_message == "Let me check."
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="toolu_1")
    ]


def test_harness_build_model_input_includes_tool_definitions() -> None:
    tool = EchoTool()
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([tool]),
        executor=SimpleToolExecutor(StaticToolRegistry([tool])),
    )
    session = harness.sessions.load_session("sess_tools")
    session.messages.append(harness._new_session_message(role="user", content="hi"))

    request = harness.build_model_input(session, [])

    assert request.available_tools == ["echo"]
    assert request.tool_definitions == [
        {
            "name": "echo",
            "description": "Echo the provided text.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        }
    ]


def test_tool_results_preserve_tool_use_id_in_session_messages() -> None:
    tool = EchoTool()
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ToolThenReplyModel(
            responses=[
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    harness.run_turn("use tool", "sess_tool_metadata")
    session = harness.sessions.load_session("sess_tool_metadata")
    tool_messages = [message for message in session.messages if message.role == "tool"]

    assert len(tool_messages) == 1
    assert tool_messages[0].metadata["tool_use_id"] == "toolu_1"
