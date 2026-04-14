"""Explicit harness runtime loop implementations."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openagent.harness.models import (
    CancelledTurn,
    RetryExhaustedTurn,
    TimedOutTurn,
    TurnControl,
    TurnState,
)
from openagent.object_model import RuntimeEvent, RuntimeEventType, TerminalState, TerminalStatus
from openagent.session import SessionRecord, SessionStatus
from openagent.tools import (
    RequiresActionError,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolPermissionDeniedError,
)

if TYPE_CHECKING:
    from openagent.harness.simple import SimpleHarness


@dataclass(slots=True)
class RalphLoop:
    """Concrete local `AgentRuntime` used by `SimpleHarness`.

    This keeps the turn state machine explicit and spec-aligned, while the
    harness remains a thin convenience facade.
    """

    harness: SimpleHarness
    state: TurnState = field(default_factory=TurnState)

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

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        session = self.harness.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")
        if session.status is not SessionStatus.REQUIRES_ACTION or not session.pending_tool_calls:
            raise ValueError("Session has no pending requires_action continuation")

        if not approved:
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._persist_session(session_handle, session)
            terminal = TerminalState(status=TerminalStatus.STOPPED, reason="approval_rejected")
            event = self.harness._append_event(
                session,
                self.harness._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_FAILED,
                    payload=terminal.to_dict(),
                ),
            )
            return [event], terminal

        emitted_events: list[RuntimeEvent] = []
        session.status = SessionStatus.RUNNING
        pending_calls = list(session.pending_tool_calls)
        tool_events, tool_results, tool_error = self.harness._execute_tool_stream(
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
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._persist_session(session_handle, session)
            return emitted_events, terminal
        if isinstance(tool_error, ToolCancelledError):
            terminal = TerminalState(
                status=TerminalStatus.STOPPED,
                reason="tool_cancelled",
                summary=str(tool_error),
            )
            emitted_events.append(
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._persist_session(session_handle, session)
            return emitted_events, terminal
        session.pending_tool_calls = []
        self.harness._append_tool_results(session, tool_results)

        request = self.harness.build_model_input(session, [])
        response = self.harness.model.generate(request)
        handled = self.harness.handle_model_output(response)
        if handled.assistant_message is not None:
            session.messages.append(
                self.harness._new_session_message(
                    role="assistant",
                    content=handled.assistant_message,
                )
            )
            emitted_events.append(
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
            )

        terminal = TerminalState(status=TerminalStatus.COMPLETED, reason="approval_continuation")
        emitted_events.append(
            self.harness._append_event(
                session,
                self.harness._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_COMPLETED,
                    payload=terminal.to_dict(),
                ),
            )
        )
        session.status = SessionStatus.IDLE
        self.harness._persist_session(session_handle, session)
        return emitted_events, terminal

    def _execute_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl,
    ) -> Iterator[RuntimeEvent]:
        session = self.harness.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")

        self.state = TurnState(
            messages=[message.to_dict() for message in session.messages],
            turn_count=0,
            transition="turn_started",
            requires_action=False,
        )
        session.status = SessionStatus.RUNNING
        session.messages.append(self.harness._new_session_message(role="user", content=input))
        self.state.messages = [message.to_dict() for message in session.messages]
        yield self.harness._append_event(
            session,
            self.harness._new_event(
                session_id=session_handle,
                event_type=RuntimeEventType.TURN_STARTED,
                payload={"input": input},
            ),
        )
        self.harness._persist_session(session_handle, session)

        for iteration in range(self.harness.max_iterations):
            self.state.turn_count = iteration + 1
            self.state.transition = "model_request"
            cancelled = self.harness._check_cancelled(control)
            if cancelled:
                self.state.transition = "aborted"
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return

            request = self.harness.build_model_input(session, [])
            try:
                handled, streamed_events = self.harness._run_model_with_retries(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                )
            except CancelledTurn:
                self.state.transition = "aborted"
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return
            except TimedOutTurn:
                self.state.transition = "failed"
                yield self.harness._emit_terminal(
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
            except RetryExhaustedTurn as exc:
                self.state.transition = "failed"
                yield self.harness._emit_terminal(
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
                    self.harness._new_session_message(
                        role="assistant",
                        content=handled.assistant_message,
                    )
                )
                self.state.messages = [message.to_dict() for message in session.messages]
                yield self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
                self.harness._persist_session(session_handle, session)

            if not handled.tool_calls:
                self.state.transition = "completed"
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_COMPLETED,
                    TerminalState(status=TerminalStatus.COMPLETED, reason="assistant_message"),
                )
                return

            self.harness._ensure_tool_call_ids(handled.tool_calls)
            self.state.transition = "tool_execution"

            try:
                tool_events, tool_results, tool_error = self.harness._execute_tool_stream(
                    session=session,
                    session_handle=session_handle,
                    tool_calls=handled.tool_calls,
                    context=ToolExecutionContext(session_id=session_handle),
                )
                yield from tool_events
            except RequiresActionError as exc:
                self.state.transition = "requires_action"
                self.state.requires_action = True
                event = self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.REQUIRES_ACTION,
                        payload=self.harness._requires_action_payload(exc.requires_action),
                    ),
                )
                yield event
                session.status = SessionStatus.REQUIRES_ACTION
                session.pending_tool_calls = handled.tool_calls
                self.harness._persist_session(session_handle, session)
                return
            except ToolPermissionDeniedError as exc:
                self.state.transition = "failed"
                yield self.harness._emit_terminal(
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
                self.state.transition = "failed"
                yield self.harness._emit_terminal(
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
                self.state.transition = "aborted"
                yield self.harness._emit_terminal(
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
                self.state.transition = "failed"
                yield self.harness._emit_terminal(
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
                self.state.transition = "aborted"
                yield self.harness._emit_terminal(
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

            self.harness._append_tool_results(session, tool_results)
            self.state.messages = [message.to_dict() for message in session.messages]
            self.harness._persist_session(session_handle, session)

        self.state.transition = "failed"
        yield self.harness._emit_terminal(
            session,
            session_handle,
            RuntimeEventType.TURN_FAILED,
            TerminalState(status=TerminalStatus.FAILED, reason="iteration_limit_exceeded"),
        )
