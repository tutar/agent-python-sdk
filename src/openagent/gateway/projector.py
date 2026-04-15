"""Projection helpers from runtime events to channel egress envelopes."""

from __future__ import annotations

from openagent.object_model import RuntimeEvent

from .models import EgressEnvelope, SessionBinding


def project_runtime_event(
    runtime_event: RuntimeEvent,
    binding: SessionBinding,
) -> EgressEnvelope | None:
    """Project a runtime event if the binding is interested in it."""

    if binding.event_types and runtime_event.event_type.value not in binding.event_types:
        return None
    channel_type = str(binding.channel_identity["channel_type"])
    return EgressEnvelope(
        channel=channel_type,
        conversation_id=binding.conversation_id,
        session_id=binding.session_id,
        event=runtime_event.to_dict(),
    )
