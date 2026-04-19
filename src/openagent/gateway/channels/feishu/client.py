"""Feishu SDK client integration."""

from __future__ import annotations

import importlib
import json
import traceback
from collections.abc import Callable
from typing import Any

from openagent.object_model import JsonObject


class OfficialFeishuBotClient:
    """Runtime wrapper over the official Feishu Python SDK."""

    def __init__(self, app_id: str, app_secret: str) -> None:
        try:
            self._lark = importlib.import_module("lark_oapi")
            im_v1 = importlib.import_module("lark_oapi.api.im.v1")
            cardkit_v1 = importlib.import_module("lark_oapi.api.cardkit.v1")
        except ImportError as exc:
            raise RuntimeError(
                "Feishu support requires the optional dependency 'lark-oapi'. "
                "Install it with: pip install 'openagent[feishu]'"
            ) from exc

        self._create_message_request = getattr(im_v1, "CreateMessageRequest")
        self._create_message_request_body = getattr(im_v1, "CreateMessageRequestBody")
        self._patch_message_request = getattr(im_v1, "PatchMessageRequest")
        self._patch_message_request_body = getattr(im_v1, "PatchMessageRequestBody")
        self._create_message_reaction_request = getattr(im_v1, "CreateMessageReactionRequest")
        self._create_message_reaction_request_body = getattr(
            im_v1, "CreateMessageReactionRequestBody"
        )
        self._emoji = getattr(im_v1, "Emoji")
        self._delete_message_reaction_request = getattr(im_v1, "DeleteMessageReactionRequest")

        self._id_convert_card_request = getattr(cardkit_v1, "IdConvertCardRequest")
        self._id_convert_card_request_body = getattr(cardkit_v1, "IdConvertCardRequestBody")
        self._settings_card_request = getattr(cardkit_v1, "SettingsCardRequest")
        self._settings_card_request_body = getattr(cardkit_v1, "SettingsCardRequestBody")
        self._update_card_request = getattr(cardkit_v1, "UpdateCardRequest")
        self._update_card_request_body = getattr(cardkit_v1, "UpdateCardRequestBody")
        self._cardkit_card = getattr(cardkit_v1, "Card")

        self._app_id = app_id
        self._app_secret = app_secret
        self._client = (
            self._lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(self._lark.LogLevel.INFO)
            .build()
        )
        self._ws_client: Any | None = None

    def start(self, event_handler: Callable[[JsonObject], JsonObject | None]) -> None:
        """Open the Feishu long connection and dispatch incoming events."""

        def _safe_dispatch(data: Any) -> JsonObject | None:
            try:
                return event_handler(self._marshal_event(data))
            except Exception as exc:  # pragma: no cover
                print(f"feishu-host> event handler failed: {exc}", flush=True)
                print(traceback.format_exc(), flush=True)
                return None

        dispatcher = (
            self._lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_safe_dispatch)
            .register_p2_card_action_trigger(_safe_dispatch)
            .build()
        )
        self._ws_client = self._lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=dispatcher,
            log_level=self._lark.LogLevel.INFO,
        )
        self._ws_client.start()

    def close(self) -> None:
        """Close the websocket client when possible."""

        if self._ws_client is not None and hasattr(self._ws_client, "close"):
            self._ws_client.close()

    def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """Send a text message to the target chat."""

        print(
            "feishu-host> agent send_text"
            f" chat={chat_id} thread={thread_id} text={text}",
            flush=True,
        )
        body_builder = (
            self._create_message_request_body.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
        )
        if thread_id is not None:
            if hasattr(body_builder, "root_id"):
                body_builder = body_builder.root_id(thread_id)
            if hasattr(body_builder, "reply_in_thread"):
                body_builder = body_builder.reply_in_thread(True)

        request = (
            self._create_message_request.builder()
            .receive_id_type("chat_id")
            .request_body(body_builder.build())
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu send_text failed: code={response.code} msg={response.msg}")

    def send_card(
        self,
        chat_id: str,
        card: JsonObject,
        thread_id: str | None = None,
    ) -> str:
        """Send an interactive card message and return its message id."""

        print(
            "feishu-host> agent send_card"
            f" chat={chat_id} thread={thread_id}",
            flush=True,
        )
        body_builder = (
            self._create_message_request_body.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
        )
        if thread_id is not None:
            if hasattr(body_builder, "root_id"):
                body_builder = body_builder.root_id(thread_id)
            if hasattr(body_builder, "reply_in_thread"):
                body_builder = body_builder.reply_in_thread(True)
        request = (
            self._create_message_request.builder()
            .receive_id_type("chat_id")
            .request_body(body_builder.build())
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu send_card failed: code={response.code} msg={response.msg}")
        data = getattr(response, "data", None)
        message_id = str(getattr(data, "message_id", "")).strip()
        if not message_id:
            raise RuntimeError("Feishu send_card succeeded without message_id")
        return message_id

    def resolve_card_id(self, message_id: str) -> str:
        """Resolve a message id into a CardKit card id."""

        body = self._id_convert_card_request_body.builder().message_id(message_id).build()
        request = self._id_convert_card_request.builder().request_body(body).build()
        response = self._client.cardkit.v1.card.id_convert(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu resolve_card_id failed: code={response.code} msg={response.msg}"
            )
        data = getattr(response, "data", None)
        card_id = str(getattr(data, "card_id", "")).strip()
        if not card_id:
            raise RuntimeError("Feishu resolve_card_id succeeded without card_id")
        return card_id

    def enable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        """Enable streaming mode for a CardKit card."""

        self._set_card_streaming(card_id, enabled=True, uuid=uuid, sequence=sequence)

    def disable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        """Disable streaming mode for a CardKit card."""

        self._set_card_streaming(card_id, enabled=False, uuid=uuid, sequence=sequence)

    def stream_update_card(
        self,
        card_id: str,
        card: JsonObject,
        *,
        uuid: str,
        sequence: int,
    ) -> None:
        """Update a CardKit card entity during a streaming session."""

        print(
            "feishu-host> agent stream_update_card"
            f" card_id={card_id} sequence={sequence}",
            flush=True,
        )
        card_body = (
            self._cardkit_card.builder()
            .type("card_json")
            .data(json.dumps(card, ensure_ascii=False))
            .build()
        )
        body = (
            self._update_card_request_body.builder()
            .card(card_body)
            .uuid(uuid)
            .sequence(sequence)
            .build()
        )
        request = (
            self._update_card_request.builder()
            .card_id(card_id)
            .request_body(body)
            .build()
        )
        response = self._client.cardkit.v1.card.update(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu stream_update_card failed: code={response.code} msg={response.msg}"
            )

    def update_card(self, message_id: str, card: JsonObject) -> None:
        """Patch an existing interactive card message by message id."""

        print(f"feishu-host> agent patch_card message_id={message_id}", flush=True)
        body = (
            self._patch_message_request_body.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        request = (
            self._patch_message_request.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message.patch(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu update_card failed: code={response.code} msg={response.msg}"
            )

    def add_reaction(self, message_id: str, reaction_type: str) -> str | None:
        """Add a reaction to a Feishu message."""

        print(
            "feishu-host> agent add_reaction"
            f" message_id={message_id} reaction={reaction_type}",
            flush=True,
        )
        emoji = self._emoji.builder().emoji_type(reaction_type).build()
        body = self._create_message_reaction_request_body.builder().reaction_type(emoji).build()
        request = (
            self._create_message_reaction_request.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message_reaction.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu add_reaction failed: code={response.code} msg={response.msg}"
            )
        data = getattr(response, "data", None)
        return str(getattr(data, "reaction_id", "")).strip() or None

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """Remove a reaction from a Feishu message."""

        print(
            "feishu-host> agent remove_reaction"
            f" message_id={message_id} reaction_id={reaction_id}",
            flush=True,
        )
        request = (
            self._delete_message_reaction_request.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = self._client.im.v1.message_reaction.delete(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu remove_reaction failed: code={response.code} msg={response.msg}"
            )

    def _marshal_event(self, data: Any) -> JsonObject:
        raw = self._lark.JSON.marshal(data)
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected Feishu event payload")
        if "header" not in parsed:
            parsed["header"] = {"event_type": "im.message.receive_v1"}
        return parsed

    def _set_card_streaming(
        self,
        card_id: str,
        *,
        enabled: bool,
        uuid: str,
        sequence: int,
    ) -> None:
        state = "enable" if enabled else "disable"
        print(
            "feishu-host> agent set_card_streaming"
            f" card_id={card_id} state={state} sequence={sequence}",
            flush=True,
        )
        settings = {
            "config": {
                "streaming_mode": enabled,
                "update_multi": True,
                "width_mode": "fill",
            }
        }
        body = (
            self._settings_card_request_body.builder()
            .settings(json.dumps(settings, ensure_ascii=False))
            .uuid(uuid)
            .sequence(sequence)
            .build()
        )
        request = (
            self._settings_card_request.builder()
            .card_id(card_id)
            .request_body(body)
            .build()
        )
        response = self._client.cardkit.v1.card.settings(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu set_card_streaming failed: code={response.code} msg={response.msg}"
            )
