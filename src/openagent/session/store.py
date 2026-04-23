"""In-memory session storage baseline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openagent.object_model import RuntimeEvent, SessionHarnessLease
from openagent.session.models import (
    ResumeSnapshot,
    SessionCheckpoint,
    SessionCursor,
    SessionRecord,
    WakeRequest,
)


class InMemorySessionStore:
    """Simple session store used for tests and local baseline wiring."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._leases: dict[str, SessionHarnessLease] = {}

    def append_event(self, event: RuntimeEvent) -> None:
        self.append_events(event.session_id, [event])

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        session = self.load_session(session_id)
        session.events.extend(events)
        self.save_session(session_id, session)
        return self.get_checkpoint(session_id)

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        session = self.load_session(session_id)
        start = cursor.event_offset if cursor is not None else after
        return session.events[start:]

    def load_session(self, session_id: str) -> SessionRecord:
        return self._sessions.setdefault(session_id, SessionRecord(session_id=session_id))

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        self._sessions[session_id] = state

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        session = self.load_session(session_id)
        last_event_id = session.events[-1].event_id if session.events else None
        cursor = SessionCursor(
            session_id=session_id,
            event_offset=len(session.events),
            last_event_id=last_event_id,
        )
        return SessionCheckpoint(
            session_id=session_id,
            event_offset=len(session.events),
            last_event_id=last_event_id,
            cursor=cursor,
            committed_at=datetime.now(UTC).isoformat(),
        )

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        session = self.load_session(session_id)
        checkpoint = self.get_checkpoint(session_id)
        marker = (
            cursor.last_event_id
            if cursor is not None and cursor.last_event_id is not None
            else checkpoint.last_event_id
        )
        session.restore_marker = marker
        self.save_session(session_id, session)

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        session = self.load_session(wake_request.session_id)
        events = self.read_events(
            wake_request.session_id,
            cursor=wake_request.cursor,
        )
        return ResumeSnapshot(
            session_id=wake_request.session_id,
            runtime_state={
                "status": session.status.value,
                "restore_marker": session.restore_marker,
            },
            transcript_slice=[message.to_dict() for message in session.messages],
            working_state={
                "pending_tool_calls": [
                    tool_call.to_dict() for tool_call in session.pending_tool_calls
                ],
                "event_count": len(events),
            },
            short_term_memory=(
                dict(session.short_term_memory)
                if isinstance(session.short_term_memory, dict)
                else None
            ),
        )

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        existing = self._leases.get(session_id)
        if existing is not None and existing.harness_instance_id != harness_instance_id:
            raise ValueError("Session already has an active harness lease")
        lease = SessionHarnessLease(
            session_id=session_id,
            harness_instance_id=harness_instance_id,
            agent_id=agent_id,
            acquired_at=datetime.now(UTC).isoformat(),
        )
        self._leases[session_id] = lease
        return lease

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        existing = self._leases.get(session_id)
        if existing is None or existing.harness_instance_id != harness_instance_id:
            return False
        self._leases.pop(session_id, None)
        return True

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        return self._leases.get(session_id)


class FileSessionStore:
    """Durable JSON-backed session store for resume semantics."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir)
        self._root_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, event: RuntimeEvent) -> None:
        self.append_events(event.session_id, [event])

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        session = self.load_session(session_id)
        session.events.extend(events)
        log_path = self._event_log_path(session_id)
        with log_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_dict()) + "\n")
        self.save_session(session_id, session)
        return self.get_checkpoint(session_id)

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        log_path = self._event_log_path(session_id)
        if not log_path.exists():
            return []
        events: list[RuntimeEvent] = []
        start = cursor.event_offset if cursor is not None else after
        for line in log_path.read_text(encoding="utf-8").splitlines()[start:]:
            raw = json.loads(line)
            if isinstance(raw, dict):
                events.append(RuntimeEvent.from_dict(raw))
        return events

    def load_session(self, session_id: str) -> SessionRecord:
        path = self._session_path(session_id)
        if not path.exists():
            return SessionRecord(session_id=session_id, events=self.read_events(session_id))

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Session file must contain a JSON object")
        record = SessionRecord.from_dict(data)
        record.events = self.read_events(session_id)
        return record

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        path = self._session_path(session_id)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        log_path = self._event_log_path(session_id)
        log_lines = [json.dumps(event.to_dict()) for event in state.events]
        if log_lines:
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        elif log_path.exists():
            log_path.unlink()

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        all_events = self.read_events(session_id)
        last_event_id = all_events[-1].event_id if all_events else None
        cursor = SessionCursor(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
        )
        return SessionCheckpoint(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
            cursor=cursor,
            committed_at=datetime.now(UTC).isoformat(),
        )

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        session = self.load_session(session_id)
        checkpoint = self.get_checkpoint(session_id)
        marker = (
            cursor.last_event_id
            if cursor is not None and cursor.last_event_id is not None
            else checkpoint.last_event_id
        )
        session.restore_marker = marker
        self.save_session(session_id, session)

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        session = self.load_session(wake_request.session_id)
        events = self.read_events(
            wake_request.session_id,
            cursor=wake_request.cursor,
        )
        return ResumeSnapshot(
            session_id=wake_request.session_id,
            runtime_state={
                "status": session.status.value,
                "restore_marker": session.restore_marker,
            },
            transcript_slice=[message.to_dict() for message in session.messages],
            working_state={
                "pending_tool_calls": [
                    tool_call.to_dict() for tool_call in session.pending_tool_calls
                ],
                "event_count": len(events),
            },
            short_term_memory=(
                dict(session.short_term_memory)
                if isinstance(session.short_term_memory, dict)
                else None
            ),
        )

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        existing = self.get_active_lease(session_id)
        if existing is not None and existing.harness_instance_id != harness_instance_id:
            raise ValueError("Session already has an active harness lease")
        lease = SessionHarnessLease(
            session_id=session_id,
            harness_instance_id=harness_instance_id,
            agent_id=agent_id,
            acquired_at=datetime.now(UTC).isoformat(),
        )
        self._lease_path(session_id).write_text(
            json.dumps(lease.to_dict(), indent=2),
            encoding="utf-8",
        )
        return lease

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        existing = self.get_active_lease(session_id)
        if existing is None or existing.harness_instance_id != harness_instance_id:
            return False
        path = self._lease_path(session_id)
        if path.exists():
            path.unlink()
        return True

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        path = self._lease_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Lease file must contain a JSON object")
        return SessionHarnessLease.from_dict(data)

    def _session_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.json"

    def _event_log_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.events.jsonl"

    def _lease_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.lease.json"
