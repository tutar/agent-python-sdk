"""Minimal harness baseline for local testing and spec prototyping."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from openagent.context_governance import (
    ContextGovernance,
    ContextReport,
)
from openagent.harness.models import (
    ModelAdapter,
    ModelTurnRequest,
    ModelTurnResponse,
    StreamingModelAdapter,
    TurnControl,
)
from openagent.memory import MemoryStore
from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    TerminalStatus,
    ToolResult,
)
from openagent.session import SessionMessage, SessionRecord, SessionStatus, SessionStore
from openagent.tools import (
    RequiresActionError,
    ToolCall,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolExecutor,
    ToolPermissionDeniedError,
    ToolRegistry,
)


@dataclass(slots=True)
class SimpleHarness:
    """Run a local turn against an injected model adapter.

    The harness remains local-first and synchronous by default, while exposing a
    stream-oriented turn path so frontends can consume deltas and intermediate
    runtime events in order.
    """

    model: ModelAdapter
    sessions: SessionStore
    tools: ToolRegistry
    executor: ToolExecutor
    max_iterations: int = 8
    context_governance: ContextGovernance | None = None
    last_context_report: ContextReport | None = None
    memory_store: MemoryStore | None = None

    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        yield from self._execute_turn_stream(
            input=input,
            session_handle=session_handle,
            control=control or TurnControl(),
        )

    def run_turn(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        events = list(self.run_turn_stream(input, session_handle, control=control))
        return events, self._terminal_state_from_event(events[-1])

    def _execute_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl,
    ) -> Iterator[RuntimeEvent]:
        session = self.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")

        session.status = SessionStatus.RUNNING
        session.messages.append(SessionMessage(role="user", content=input))
        yield self._append_event(
            session,
            self._new_event(
                session_id=session_handle,
                event_type=RuntimeEventType.TURN_STARTED,
                payload={"input": input},
            ),
        )
        self._persist_session(session_handle, session)

        for _ in range(self.max_iterations):
            cancelled = self._check_cancelled(control)
            if cancelled:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return

            request = self.build_model_input(session, [])
            try:
                handled, streamed_events = self._run_model_with_retries(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                )
            except _CancelledTurn:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return
            except _TimedOutTurn:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="timeout",
                        retryable=True,
                    ),
                )
                return
            except _RetryExhaustedTurn as exc:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="retry_exhausted",
                        retryable=False,
                        summary=str(exc),
                    ),
                )
                return

            yield from streamed_events

            if handled.assistant_message is not None:
                session.messages.append(
                    SessionMessage(role="assistant", content=handled.assistant_message)
                )
                yield self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
                self._persist_session(session_handle, session)

            if not handled.tool_calls:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_COMPLETED,
                    TerminalState(status=TerminalStatus.COMPLETED, reason="assistant_message"),
                )
                return

            self._ensure_tool_call_ids(handled.tool_calls)

            try:
                tool_events, tool_results, tool_error = self._execute_tool_stream(
                    session=session,
                    session_handle=session_handle,
                    tool_calls=handled.tool_calls,
                    context=ToolExecutionContext(session_id=session_handle),
                )
                yield from tool_events
            except RequiresActionError as exc:
                event = self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.REQUIRES_ACTION,
                        payload=self._requires_action_payload(exc.requires_action),
                    ),
                )
                yield event
                session.status = SessionStatus.REQUIRES_ACTION
                session.pending_tool_calls = handled.tool_calls
                self._persist_session(session_handle, session)
                return
            except ToolPermissionDeniedError as exc:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_permission_denied",
                        summary=str(exc),
                    ),
                )
                return
            except ToolExecutionFailedError as exc:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_execution_failed",
                        summary=str(exc),
                    ),
                )
                return
            except ToolCancelledError as exc:
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.STOPPED,
                        reason="tool_cancelled",
                        summary=str(exc),
                    ),
                )
                return

            if isinstance(tool_error, ToolExecutionFailedError):
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_execution_failed",
                        summary=str(tool_error),
                    ),
                )
                return
            if isinstance(tool_error, ToolCancelledError):
                yield self._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.STOPPED,
                        reason="tool_cancelled",
                        summary=str(tool_error),
                    ),
                )
                return

            self._append_tool_results(session, tool_results)
            self._persist_session(session_handle, session)

        yield self._emit_terminal(
            session,
            session_handle,
            RuntimeEventType.TURN_FAILED,
            TerminalState(status=TerminalStatus.FAILED, reason="iteration_limit_exceeded"),
        )

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        session = self.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")
        if session.status is not SessionStatus.REQUIRES_ACTION or not session.pending_tool_calls:
            raise ValueError("Session has no pending requires_action continuation")

        if not approved:
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self._persist_session(session_handle, session)
            terminal = TerminalState(status=TerminalStatus.STOPPED, reason="approval_rejected")
            event = self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_FAILED,
                    payload=terminal.to_dict(),
                ),
            )
            return [event], terminal

        emitted_events: list[RuntimeEvent] = []
        session.status = SessionStatus.RUNNING
        pending_calls = list(session.pending_tool_calls)
        tool_events, tool_results, tool_error = self._execute_tool_stream(
            session=session,
            session_handle=session_handle,
            tool_calls=pending_calls,
            context=ToolExecutionContext(
                session_id=session_handle,
                approved_tool_names=[tool_call.tool_name for tool_call in pending_calls],
            ),
        )
        emitted_events.extend(tool_events)
        if isinstance(tool_error, ToolExecutionFailedError):
            terminal = TerminalState(
                status=TerminalStatus.FAILED,
                reason="tool_execution_failed",
                summary=str(tool_error),
            )
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self._persist_session(session_handle, session)
            return emitted_events, terminal
        if isinstance(tool_error, ToolCancelledError):
            terminal = TerminalState(
                status=TerminalStatus.STOPPED,
                reason="tool_cancelled",
                summary=str(tool_error),
            )
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self._persist_session(session_handle, session)
            return emitted_events, terminal
        session.pending_tool_calls = []
        self._append_tool_results(session, tool_results)

        request = self.build_model_input(session, [])
        response = self.model.generate(request)
        handled = self.handle_model_output(response)
        if handled.assistant_message is not None:
            session.messages.append(
                SessionMessage(role="assistant", content=handled.assistant_message)
            )
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
            )

        terminal = TerminalState(status=TerminalStatus.COMPLETED, reason="approval_continuation")
        emitted_events.append(
            self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_COMPLETED,
                    payload=terminal.to_dict(),
                ),
            )
        )
        session.status = SessionStatus.IDLE
        self._persist_session(session_handle, session)
        return emitted_events, terminal

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
            messages = compact_result.messages
            compacted = compact_result.compacted_count > 0
            if self.context_governance.analyze(session_slice.messages, available_tools).over_budget:
                recovery_result = self.context_governance.recover_overflow(session_slice.messages)
                if recovery_result.recovered:
                    messages = recovery_result.messages
                    recovered_from_overflow = True
        else:
            messages = [message.to_dict() for message in session_slice.messages]
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
        return ModelTurnRequest(
            session_id=session_slice.session_id,
            messages=messages,
            available_tools=available_tools,
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
                raise _CancelledTurn()
            try:
                return self._run_model_once(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                )
            except (_CancelledTurn, _TimedOutTurn):
                raise
            except Exception as exc:
                last_error = exc
                if attempt == control.max_retries:
                    raise _RetryExhaustedTurn(str(exc)) from exc
        raise _RetryExhaustedTurn(str(last_error))

    def _run_model_once(
        self,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        stream_generate = getattr(self.model, "stream_generate", None)
        if callable(stream_generate):
            streaming_model = cast(StreamingModelAdapter, self.model)
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
                raise _TimedOutTurn() from exc

    def _run_streaming_model(
        self,
        model: StreamingModelAdapter,
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
                raise _CancelledTurn()
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


class _CancelledTurn(Exception):
    pass


class _TimedOutTurn(Exception):
    pass


class _RetryExhaustedTurn(Exception):
    pass
