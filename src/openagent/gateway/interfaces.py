"""Gateway protocols shared across bindings, channels, and runtimes."""

from __future__ import annotations

from typing import Protocol

from openagent.object_model import RuntimeEvent
from openagent.session import SessionCheckpoint

from .models import SessionBinding


class ChannelAdapter(Protocol):
    """Describe what a channel wants to observe from the runtime."""

    channel_type: str

    def accepted_event_types(self) -> list[str]:
        """Return runtime event types that should be projected to the channel."""


class SessionBindingStore(Protocol):
    """Persist and restore frontend-session bindings."""

    def save_binding(self, binding: SessionBinding) -> None:
        """Persist a gateway session binding."""

    def load_binding(self, channel_type: str, conversation_id: str) -> SessionBinding | None:
        """Load a binding for the given frontend conversation."""


class SessionAdapter(Protocol):
    """Bridge the gateway to the actual session runtime implementation."""

    def spawn(self, session_id: str) -> object:
        """Ensure the session exists and return an implementation-specific handle."""

    def write_input(self, session_handle: str, input_text: str) -> list[RuntimeEvent]:
        """Append user or supplement input to the active interaction."""

    def observe(self, session_handle: str, after: int = 0) -> list[RuntimeEvent]:
        """Read runtime events emitted by the session."""

    def continue_session(self, session_handle: str, approved: bool) -> list[RuntimeEvent]:
        """Continue a paused session, usually after a permission decision."""

    def kill(self, session_handle: str) -> None:
        """Interrupt the active interaction for a session."""

    def get_checkpoint(self, session_handle: str) -> SessionCheckpoint:
        """Return the latest persisted checkpoint for a session."""

    def get_restore_marker(self, session_handle: str) -> str | None:
        """Return the restore marker used for restart-safe resume."""
