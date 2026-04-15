"""Binding store implementations for the gateway."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SessionBinding


class FileSessionBindingStore:
    """Persist bindings on disk for local restart-safe channel recovery."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def save_binding(self, binding: SessionBinding) -> None:
        path = self._binding_path(
            str(binding.channel_identity["channel_type"]),
            binding.conversation_id,
        )
        path.write_text(
            json.dumps(binding.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load_binding(self, channel_type: str, conversation_id: str) -> SessionBinding | None:
        path = self._binding_path(channel_type, conversation_id)
        if not path.exists():
            return None
        return SessionBinding.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _binding_path(self, channel_type: str, conversation_id: str) -> Path:
        safe_name = f"{channel_type}__{conversation_id}".replace("/", "_")
        return self._root / f"{safe_name}.json"
