"""Feishu channel adapter implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from openagent.object_model import JsonObject

from ...models import ChannelIdentity, EgressEnvelope, InboundEnvelope
from ..tui.terminal import _default_terminal_event_types


class FeishuBotClient(Protocol):
    """Minimal Feishu client surface used by the host."""

    def start(self, event_handler: Callable[[JsonObject], JsonObject | None]) -> None:
        """Start receiving Feishu events and forward them to the host."""

    def close(self) -> None:
        """Stop the Feishu connection."""

    def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """Send a text reply to a Feishu chat or thread."""

    def send_card(self, chat_id: str, card: JsonObject, thread_id: str | None = None) -> str:
        """Send an interactive card reply and return the created message id."""

    def resolve_card_id(self, message_id: str) -> str:
        """Resolve a Feishu message id into a CardKit card id."""

    def enable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        """Enable CardKit streaming mode for a card."""

    def disable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        """Disable CardKit streaming mode for a card."""

    def stream_update_card(
        self,
        card_id: str,
        card: JsonObject,
        *,
        uuid: str,
        sequence: int,
    ) -> None:
        """Update a CardKit card entity during a streaming session."""

    def update_card(self, message_id: str, card: JsonObject) -> None:
        """Patch a previously created message card by message id."""

    def add_reaction(self, message_id: str, reaction_type: str) -> str | None:
        """Add a reaction to a Feishu message and return its reaction id when available."""

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """Remove a previously created reaction from a Feishu message."""


@dataclass(slots=True)
class FeishuChannelAdapter:
    """Project Feishu events into gateway envelopes and back into chat messages."""

    client: FeishuBotClient | None = None
    mention_required_in_group: bool = True
    channel_type: str = "feishu"
    def accepted_event_types(self) -> list[str]:
        """Expose the local frontend event surface to Feishu."""

        return _default_terminal_event_types()

    def normalize_inbound(self, raw_event: JsonObject) -> InboundEnvelope | None:
        """Convert a Feishu message event into a gateway input envelope."""

        event_type = str(raw_event.get("event_type", ""))
        if event_type != "im.message.receive_v1":
            header = raw_event.get("header")
            if not isinstance(header, dict):
                return None
            if str(header.get("event_type", "")) != "im.message.receive_v1":
                return None

        event = raw_event.get("event")
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return None
        if str(message.get("message_type", "")) != "text":
            return None

        sender_id = sender.get("sender_id")
        open_id = None
        if isinstance(sender_id, dict) and sender_id.get("open_id") is not None:
            open_id = str(sender_id["open_id"])

        chat_id = str(message.get("chat_id", ""))
        message_id = str(message.get("message_id", ""))
        chat_type = str(message.get("chat_type", ""))
        thread_id = self._extract_thread_root(message)
        raw_text = self._extract_text_content(message.get("content"))
        mentions = self._extract_mentions(message)

        if (
            chat_type != "p2p"
            and self.mention_required_in_group
            and not self._has_group_mention(raw_text, mentions)
        ):
            return None

        text = self._strip_mentions(raw_text, mentions).strip()
        if not text:
            return None

        channel_identity = ChannelIdentity(
            channel_type=self.channel_type,
            user_id=open_id,
            conversation_id=self._conversation_id(chat_id, thread_id),
        )
        input_kind, payload = self._parse_input(text)
        return InboundEnvelope(
            channel_identity=channel_identity.to_dict(),
            input_kind=input_kind,
            payload=payload,
            delivery_metadata={
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "thread_id": thread_id,
            },
        )

    def project_outbound(self, egress_event: EgressEnvelope) -> JsonObject | None:
        """Project management-only outbound events into Feishu text messages."""

        event = egress_event.event
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        normalized_payload = payload if isinstance(payload, dict) else {}
        chat_id, thread_id = self.parse_conversation_id(egress_event.conversation_id)
        text = self._event_text(event_type, normalized_payload)
        if text is None:
            return None
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "text": text,
        }

    def send(self, projected_message: JsonObject) -> None:
        """Send a projected message through the configured Feishu client."""

        if self.client is None:
            raise RuntimeError("Feishu client is not configured")
        self.client.send_text(
            chat_id=str(projected_message["chat_id"]),
            text=str(projected_message["text"]),
            thread_id=str(projected_message["thread_id"])
            if projected_message.get("thread_id") is not None
            else None,
        )

    def parse_conversation_id(self, conversation_id: str) -> tuple[str, str | None]:
        """Recover Feishu chat/thread information from a gateway conversation id."""

        parts = conversation_id.split(":")
        if len(parts) >= 3 and parts[0] == "feishu" and parts[1] == "chat":
            chat_id = parts[2]
            if len(parts) >= 5 and parts[3] == "thread":
                return chat_id, parts[4]
            return chat_id, None
        raise ValueError(f"Invalid Feishu conversation id: {conversation_id}")

    def parse_card_action(self, value: JsonObject) -> tuple[str, JsonObject] | None:
        """Map a Feishu card action value back into a canonical control payload."""

        subtype = str(value.get("subtype", "")).strip()
        if subtype == "permission_response":
            return "control", {"subtype": subtype, "approved": bool(value.get("approved", False))}
        return None

    def _event_text(self, event_type: str, payload: JsonObject) -> str | None:
        if event_type == "assistant_message":
            message = payload.get("message")
            if message is None:
                return None
            text = str(message).strip()
            return text or None
        return None

    def _parse_input(self, text: str) -> tuple[str, JsonObject]:
        command = text.strip()
        if command == "/channel" or command.startswith("/channel ") or command.startswith(
            "/channel-config "
        ):
            return "management", {"command": command}
        return "user_message", {"content": text}

    def _conversation_id(self, chat_id: str, thread_id: str | None) -> str:
        if thread_id:
            return f"feishu:chat:{chat_id}:thread:{thread_id}"
        return f"feishu:chat:{chat_id}"

    def _extract_text_content(self, content: object) -> str:
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return content
            if isinstance(parsed, dict) and parsed.get("text") is not None:
                return str(parsed["text"])
        return ""

    def _extract_mentions(self, message: JsonObject) -> list[JsonObject]:
        mentions = message.get("mentions")
        if isinstance(mentions, list):
            return [item for item in mentions if isinstance(item, dict)]
        return []

    def _extract_thread_root(self, message: JsonObject) -> str | None:
        if message.get("root_id") is not None:
            return str(message["root_id"])
        if message.get("parent_id") is not None:
            return str(message["parent_id"])
        return None

    def _strip_mentions(self, text: str, mentions: list[JsonObject]) -> str:
        normalized = text
        for mention in mentions:
            name = mention.get("name")
            key = mention.get("key")
            if name is not None:
                normalized = normalized.replace(f"@{name}", "")
            if key is not None:
                normalized = normalized.replace(str(key), "")
                normalized = normalized.replace(f"<at user_id=\"{key}\"></at>", "")
                normalized = normalized.replace(f"<at user_id={key}></at>", "")
        normalized = self._strip_textual_leading_mention(normalized)
        return " ".join(normalized.split())

    def _has_group_mention(self, text: str, mentions: list[JsonObject]) -> bool:
        if mentions:
            return True
        stripped = text.strip()
        return stripped.startswith("@") and " " in stripped

    def _strip_textual_leading_mention(self, text: str) -> str:
        stripped = text.lstrip()
        if not stripped.startswith("@"):
            return text
        parts = stripped.split(maxsplit=1)
        if len(parts) == 1:
            return ""
        return parts[1]
