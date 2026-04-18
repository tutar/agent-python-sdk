"""Filtering and host projection helpers for capability surfaces."""

from __future__ import annotations

from openagent.capability_surface.models import CapabilityDescriptor
from openagent.object_model import JsonObject


def apply_capability_filters(
    descriptors: list[CapabilityDescriptor],
    filters: JsonObject | None,
) -> list[CapabilityDescriptor]:
    if filters is None:
        return descriptors

    filtered = descriptors
    capability_type = filters.get("capability_type")
    if isinstance(capability_type, str):
        filtered = [
            descriptor for descriptor in filtered if descriptor.capability_type == capability_type
        ]

    host_profile = filters.get("host_profile")
    if isinstance(host_profile, str):
        filtered = project_descriptors_for_host(filtered, host_profile)

    visibility = filters.get("visibility")
    if visibility == "model":
        filtered = [descriptor for descriptor in filtered if descriptor.visible_to_model]
    if visibility == "user":
        filtered = [descriptor for descriptor in filtered if descriptor.visible_to_user]

    origin_type = filters.get("origin_type")
    if isinstance(origin_type, str):
        filtered = [
            descriptor
            for descriptor in filtered
            if descriptor.origin.get("origin_type") == origin_type
        ]
    return filtered


def project_descriptors_for_host(
    descriptors: list[CapabilityDescriptor],
    host_profile: str,
) -> list[CapabilityDescriptor]:
    if host_profile in {"local", "terminal"}:
        return descriptors

    if host_profile == "feishu":
        return [
            descriptor
            for descriptor in descriptors
            if descriptor.metadata.get("kind") != "local_ui"
        ]

    if host_profile == "cloud":
        return [
            descriptor
            for descriptor in descriptors
            if descriptor.metadata.get("kind") not in {"local_ui", "local"}
        ]

    return descriptors
