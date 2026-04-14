"""Minimal harness baseline for local testing and spec prototyping."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from openagent.context_governance import (
    ContextGovernance,
    ContextReport,
)
from openagent.harness.models import (
    AgentRuntime,
    CancelledTurn,
    ModelProviderAdapter,
    ModelProviderStreamingAdapter,
    ModelTurnRequest,
    ModelTurnResponse,
    RetryExhaustedTurn,
    TimedOutTurn,
    TurnControl,
)
from openagent.harness.runtime import RalphLoop
from openagent.memory import MemoryStore
from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    TerminalStatus,
    ToolResult,
)
from openagent.session import (
    SessionMessage,
    SessionRecord,
    SessionStatus,
    SessionStore,
    ShortTermMemoryStore,
    ShortTermSessionMemory,
)
from openagent.tools import (
    ToolCall,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolExecutor,
    ToolRegistry,
)


@dataclass(slots=True)
class SimpleHarness:
    """Run a local turn against an injected model adapter.

    The harness remains local-first and synchronous by default, while exposing a
    stream-oriented turn path so frontends can consume deltas and intermediate
    runtime events in order.
    """

    model: ModelProviderAdapter
    sessions: SessionStore
    tools: ToolRegistry
    executor: ToolExecutor
    max_iterations: int = 8
    context_governance: ContextGovernance | None = None
    last_context_report: ContextReport | None = None
    memory_store: MemoryStore | None = None
    short_term_memory_store: ShortTermMemoryStore | None = None
    last_memory_consolidation_job_id: str | None = None
    runtime_loop: AgentRuntime = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Keep the loop explicit and spec-aligned while preserving the existing
        # facade API that tests and frontends already use.
        self.runtime_loop = RalphLoop(self)

    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        yield from self.runtime_loop.run_turn_stream(input, session_handle, control=control)

    def run_turn(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        events = list(self.run_turn_stream(input, session_handle, control=control))
        return events, self._terminal_state_from_event(events[-1])

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        return self.runtime_loop.continue_turn(session_handle, approved)

    def build_model_input(
        self,
        session_slice: SessionRecord,
        context_providers: list[object],
    ) -> ModelTurnRequest:
        del context_providers
        compacted = False
        recovered_from_overflow = False
        available_tools = [tool.name for tool in self.tools.list_tools()]
        if self.context_governance is not None and self.context_governance.should_compact(
            session_slice.messages
        ):
            compact_result = self.context_governance.compact(session_slice.messages)
            messages = self._normalize_message_payloads(compact_result.messages)
            compacted = compact_result.compacted_count > 0
            if self.context_governance.analyze(session_slice.messages, available_tools).over_budget:
                recovery_result = self.context_governance.recover_overflow(session_slice.messages)
                if recovery_result.recovered:
                    messages = self._normalize_message_payloads(recovery_result.messages)
                    recovered_from_overflow = True
        else:
            messages = [self._message_payload(message) for message in session_slice.messages]
        if self.context_governance is not None:
            self.last_context_report = self.context_governance.report_for_model_input(
                session_slice.messages,
                available_tools,
                compacted=compacted,
                recovered_from_overflow=recovered_from_overflow,
            )
            if not self.context_governance.should_allow_continuation(
                session_slice.messages,
                available_tools,
            ):
                overflow_result = self.context_governance.recover_overflow(session_slice.messages)
                if overflow_result.recovered:
                    messages = overflow_result.messages
                    messages = self._normalize_message_payloads(messages)
                    recovered_from_overflow = True
                    self.last_context_report = self.context_governance.report_for_model_input(
                        session_slice.messages,
                        available_tools,
                        compacted=compacted,
                        recovered_from_overflow=recovered_from_overflow,
                    )
        memory_context: list[JsonObject] = []
        if self.memory_store is not None and session_slice.messages:
            latest_query = session_slice.messages[-1].content
            recall_result = self.memory_store.recall(
                session_slice.session_id,
                latest_query,
            )
            memory_context = [record.to_dict() for record in recall_result.recalled]
        tool_definitions: list[JsonObject] = [
            {
                "name": tool.name,
                "description": tool.description(),
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.list_tools()
        ]
        short_term_memory = self._load_short_term_memory(session_slice)
        return ModelTurnRequest(
            session_id=session_slice.session_id,
            messages=messages,
            available_tools=available_tools,
            tool_definitions=tool_definitions,
            short_term_memory=(
                short_term_memory.to_dict() if short_term_memory is not None else None
            ),
            memory_context=memory_context,
        )

    def handle_model_output(self, output: ModelTurnResponse) -> ModelTurnResponse:
        return output

    def route_tool_call(self, tool_call: ToolCall) -> ToolResult:
        result = self.executor.run_tools(
            [tool_call],
            ToolExecutionContext(session_id="ad_hoc"),
        )
        return result[0]

    def _new_session_message(self, role: str, content: str) -> SessionMessage:
        return SessionMessage(role=role, content=content)

    def schedule_memory_maintenance(self, session: SessionRecord) -> None:
        if self.short_term_memory_store is not None:
            current_memory = self.short_term_memory_store.load(session.session_id)
            update = self.short_term_memory_store.update(
                session.session_id,
                list(session.messages),
                current_memory,
            )
            if update.memory is not None:
                session.short_term_memory = update.memory.to_dict()
        if self.memory_store is not None and session.messages:
            job = self.memory_store.schedule(session.session_id, list(session.messages))
            self.last_memory_consolidation_job_id = job.job_id

    def stabilize_short_term_memory(
        self,
        session: SessionRecord,
        timeout_ms: int = 250,
    ) -> None:
        if self.short_term_memory_store is None:
            return
        memory = self.short_term_memory_store.wait_until_stable(session.session_id, timeout_ms)
        if memory is not None:
            session.short_term_memory = memory.to_dict()

    def _load_short_term_memory(
        self,
        session: SessionRecord,
    ) -> ShortTermSessionMemory | None:
        if self.short_term_memory_store is not None:
            stable_memory = self.short_term_memory_store.wait_until_stable(session.session_id, 50)
            if stable_memory is not None:
                session.short_term_memory = stable_memory.to_dict()
                return stable_memory
            loaded = self.short_term_memory_store.load(session.session_id)
            if loaded is not None:
                session.short_term_memory = loaded.to_dict()
                return loaded
        return None

    def _message_payload(self, message: SessionMessage) -> JsonObject:
        payload = message.to_dict()
        if payload.get("metadata") == {}:
            payload.pop("metadata", None)
        return payload

    def _normalize_message_payloads(self, messages: list[JsonObject]) -> list[JsonObject]:
        normalized: list[JsonObject] = []
        for message in messages:
            payload = dict(message)
            if payload.get("metadata") == {}:
                payload.pop("metadata", None)
            normalized.append(payload)
        return normalized

    def _execute_tool_stream(
        self,
        session: SessionRecord,
        session_handle: str,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> tuple[
        list[RuntimeEvent],
        list[ToolResult],
        ToolExecutionFailedError | ToolCancelledError | None,
    ]:
        emitted_events: list[RuntimeEvent] = []
        results: list[ToolResult] = []
        for event in self.executor.run_tool_stream(tool_calls, context):
            emitted_events.append(self._append_event(session, event))
            if event.event_type in {
                RuntimeEventType.TOOL_PROGRESS,
                RuntimeEventType.TOOL_RESULT,
                RuntimeEventType.TOOL_FAILED,
                RuntimeEventType.TOOL_CANCELLED,
            }:
                self._persist_session(session_handle, session)
            if event.event_type is RuntimeEventType.TOOL_PROGRESS:
                continue
            if event.event_type is RuntimeEventType.TOOL_RESULT:
                payload = dict(event.payload)
                payload.pop("tool_use_id", None)
                result = ToolResult.from_dict(payload)
                results.append(result)
                continue
            if event.event_type is RuntimeEventType.TOOL_FAILED:
                return emitted_events, results, ToolExecutionFailedError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "tool_failed")),
                )
            if event.event_type is RuntimeEventType.TOOL_CANCELLED:
                return emitted_events, results, ToolCancelledError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "cancelled")),
                )
        return emitted_events, results, None

    def _run_model_with_retries(
        self,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        last_error: Exception | None = None
        for attempt in range(max(0, control.max_retries) + 1):
            if self._check_cancelled(control):
                raise CancelledTurn()
            try:
                return self._run_model_once(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                )
            except (CancelledTurn, TimedOutTurn):
                raise
            except Exception as exc:
                last_error = exc
                if attempt == control.max_retries:
                    raise RetryExhaustedTurn(str(exc)) from exc
        raise RetryExhaustedTurn(str(last_error))

    def _run_model_once(
        self,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        stream_generate = getattr(self.model, "stream_generate", None)
        if callable(stream_generate):
            streaming_model = cast(ModelProviderStreamingAdapter, self.model)
            return self._run_streaming_model(
                model=streaming_model,
                request=request,
                session=session,
                session_handle=session_handle,
                control=control,
            )
        return self._run_single_response_model(request=request, control=control)

    def _run_single_response_model(
        self,
        request: ModelTurnRequest,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        if control.timeout_seconds is None:
            return self.model.generate(request), []
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.model.generate, request)
            try:
                return future.result(timeout=control.timeout_seconds), []
            except FutureTimeoutError as exc:
                raise TimedOutTurn() from exc

    def _run_streaming_model(
        self,
        model: ModelProviderStreamingAdapter,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        stream = model.stream_generate(request)
        aggregated_message = ""
        final_message: str | None = None
        final_tool_calls: list[ToolCall] = []
        streamed_events: list[RuntimeEvent] = []
        for stream_event in stream:
            if self._check_cancelled(control):
                raise CancelledTurn()
            if stream_event.assistant_delta:
                aggregated_message += stream_event.assistant_delta
                runtime_event = self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_DELTA,
                        payload={"delta": stream_event.assistant_delta},
                    ),
                )
                self._persist_session(session_handle, session)
                streamed_events.append(runtime_event)
            if stream_event.assistant_message is not None:
                final_message = stream_event.assistant_message
            if stream_event.tool_calls:
                final_tool_calls = stream_event.tool_calls
        assistant_message = (
            final_message if final_message is not None else aggregated_message or None
        )
        return (
            ModelTurnResponse(
                assistant_message=assistant_message,
                tool_calls=final_tool_calls,
            ),
            streamed_events,
        )

    def _append_tool_results(self, session: SessionRecord, tool_results: list[ToolResult]) -> None:
        for result in tool_results:
            if self.context_governance is not None:
                result = self.context_governance.externalize_tool_result(result)
            storage_marker = result.persisted_ref or "inline"
            session.messages.append(
                SessionMessage(
                    role="tool",
                    content=f"{result.tool_name}: {result.content} [externalized:{storage_marker}]",
                    metadata=dict(result.metadata or {}),
                )
            )

    def _append_event(self, session: SessionRecord, event: RuntimeEvent) -> RuntimeEvent:
        session.events.append(event)
        return event

    def _persist_session(self, session_handle: str, session: SessionRecord) -> None:
        self.sessions.save_session(session_handle, session)

    def _ensure_tool_call_ids(self, tool_calls: list[ToolCall]) -> None:
        for index, tool_call in enumerate(tool_calls, start=1):
            if tool_call.call_id is None:
                tool_call.call_id = f"toolu_{index}"

    def _tool_call_payload(self, tool_call: ToolCall) -> JsonObject:
        payload = tool_call.to_dict()
        tool_use_id = payload.pop("call_id", None)
        if tool_use_id is not None:
            payload["tool_use_id"] = tool_use_id
        return payload

    def _tool_result_payload(self, result: ToolResult) -> JsonObject:
        payload = result.to_dict()
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "tool_use_id" in metadata:
            payload["tool_use_id"] = metadata["tool_use_id"]
        return payload

    def _requires_action_payload(self, requires_action: object) -> JsonObject:
        if not hasattr(requires_action, "to_dict"):
            raise TypeError("requires_action payload must support to_dict()")
        payload = cast(JsonObject, requires_action.to_dict())
        request_id = payload.get("request_id")
        if request_id is not None:
            payload["tool_use_id"] = request_id
        return payload

    def _new_event(
        self,
        session_id: str,
        event_type: RuntimeEventType,
        payload: JsonObject,
    ) -> RuntimeEvent:
        timestamp = datetime.now(UTC).isoformat()
        event_id = f"{event_type.value}:{len(self.sessions.load_session(session_id).events) + 1}"
        return RuntimeEvent(
            event_type=event_type,
            event_id=event_id,
            timestamp=timestamp,
            session_id=session_id,
            payload=payload,
        )

    def _emit_terminal(
        self,
        session: SessionRecord,
        session_handle: str,
        event_type: RuntimeEventType,
        terminal: TerminalState,
    ) -> RuntimeEvent:
        event = self._append_event(
            session,
            self._new_event(
                session_id=session_handle,
                event_type=event_type,
                payload=terminal.to_dict(),
            ),
        )
        session.status = SessionStatus.IDLE
        self.schedule_memory_maintenance(session)
        self.stabilize_short_term_memory(session)
        self._persist_session(session_handle, session)
        return event

    def _check_cancelled(self, control: TurnControl) -> bool:
        return control.cancellation_check is not None and control.cancellation_check()

    def _terminal_state_from_event(self, event: RuntimeEvent) -> TerminalState:
        if event.event_type is RuntimeEventType.TURN_COMPLETED:
            return TerminalState.from_dict(event.payload)
        if event.event_type is RuntimeEventType.TURN_FAILED:
            return TerminalState.from_dict(event.payload)
        if event.event_type is RuntimeEventType.REQUIRES_ACTION:
            summary = str(event.payload.get("description", "requires action"))
            return TerminalState(
                status=TerminalStatus.BLOCKED,
                reason="requires_action",
                summary=summary,
            )
        raise ValueError("Event stream did not terminate with a terminal event")
