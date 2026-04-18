"""Shared capability surface object model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


class CapabilityOriginType(StrEnum):
    BUILTIN = "builtin"
    BUNDLED = "bundled"
    PLUGIN = "plugin"
    USER = "user"
    PROJECT = "project"
    MANAGED = "managed"
    MCP = "mcp"
    REMOTE = "remote"


@dataclass(slots=True)
class CapabilityOrigin(SerializableModel):
    origin_type: CapabilityOriginType
    package_id: str | None = None
    provider_id: str | None = None
    installation_scope: str | None = None


@dataclass(slots=True)
class InvocableEntry(SerializableModel):
    entry_id: str
    entry_type: str
    display_name: str
    description: str
    source_origin: JsonObject
    invocation_mode: str
    visible_to_model: bool = True
    visible_to_user: bool = True
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CapabilityDescriptor(SerializableModel):
    capability_id: str
    capability_type: str
    display_name: str
    description: str
    invocation_mode: str
    origin: JsonObject
    visible_to_model: bool = True
    visible_to_user: bool = True
    metadata: JsonObject = field(default_factory=dict)
