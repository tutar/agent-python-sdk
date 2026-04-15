"""Gateway control routing helpers."""

from __future__ import annotations

from openagent.object_model import JsonObject

SUPPORTED_CONTROL_SUBTYPES = {
    "interrupt",
    "resume",
    "permission_response",
    "mode_change",
}


def route_control_message(control_message: JsonObject) -> JsonObject:
    """Classify whether a control message is understood by the gateway."""

    subtype = str(control_message.get("subtype", "unknown"))
    return {
        "subtype": subtype,
        "accepted": subtype in SUPPORTED_CONTROL_SUBTYPES,
    }
