"""In-process gateway session adapter."""

from __future__ import annotations

from openagent.harness import SimpleHarness
from openagent.object_model import RuntimeEvent
from openagent.session import SessionCheckpoint

from .models import LocalSessionHandle


class InProcessSessionAdapter:
    """Expose a local harness as a gateway-managed session runtime."""

    def __init__(self, runtime: SimpleHarness) -> None:
        self._runtime = runtime
        self._handles: dict[str, LocalSessionHandle] = {}

    def spawn(self, session_id: str) -> LocalSessionHandle:
        handle = self._handles.setdefault(session_id, LocalSessionHandle(session_id=session_id))
        return handle

    def write_input(self, session_handle: str, input_text: str) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "turn"
        handle.activities.append("turn")
        events, _ = self._runtime.run_turn(input_text, session_handle)
        handle.done = True
        handle.current_activity = None
        return events

    def observe(self, session_handle: str, after: int = 0) -> list[RuntimeEvent]:
        return self._runtime.sessions.read_events(session_handle, after=after)

    def continue_session(self, session_handle: str, approved: bool) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "continuation"
        handle.activities.append("continuation")
        events, _ = self._runtime.continue_turn(session_handle, approved=approved)
        handle.done = True
        handle.current_activity = None
        return events

    def kill(self, session_handle: str) -> None:
        handle = self.spawn(session_handle)
        handle.done = True
        handle.current_activity = "killed"

    def get_checkpoint(self, session_handle: str) -> SessionCheckpoint:
        return self._runtime.sessions.get_checkpoint(session_handle)

    def get_restore_marker(self, session_handle: str) -> str | None:
        session = self._runtime.sessions.load_session(session_handle)
        return getattr(session, "restore_marker", None)
