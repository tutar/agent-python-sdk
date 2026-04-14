"""MCP compatibility layer with deterministic and transport-backed seams."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from openagent.object_model import JsonObject, JsonValue, SerializableModel, ToolResult
from openagent.tools.commands import Command, CommandKind, CommandVisibility
from openagent.tools.skills import SkillDefinition


@dataclass(slots=True)
class McpServerDescriptor(SerializableModel):
    server_id: str
    label: str


@dataclass(slots=True)
class McpToolDescriptor(SerializableModel):
    name: str
    description: str
    input_schema: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpPromptDescriptor(SerializableModel):
    name: str
    description: str
    template: str


@dataclass(slots=True)
class McpResourceDescriptor(SerializableModel):
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    content: str = ""


@dataclass(slots=True)
class McpServerConnection:
    descriptor: McpServerDescriptor
    tools: dict[str, tuple[McpToolDescriptor, Callable[[JsonObject], ToolResult]]] = field(
        default_factory=dict
    )
    prompts: dict[str, McpPromptDescriptor] = field(default_factory=dict)
    resources: dict[str, McpResourceDescriptor] = field(default_factory=dict)


class McpTransport(Protocol):
    def list_tools(self, server_id: str) -> list[McpToolDescriptor]:
        """List remote MCP tools."""

    def list_prompts(self, server_id: str) -> list[McpPromptDescriptor]:
        """List remote MCP prompts."""

    def list_resources(self, server_id: str) -> list[McpResourceDescriptor]:
        """List remote MCP resources."""

    def call_tool(self, server_id: str, tool_name: str, input: JsonObject) -> ToolResult:
        """Invoke a remote MCP tool."""

    def get_prompt(self, server_id: str, prompt_name: str, args: JsonObject) -> str:
        """Render a remote MCP prompt."""

    def read_resource(self, server_id: str, resource_uri: str) -> McpResourceDescriptor:
        """Read a remote MCP resource."""


class InMemoryMcpTransport:
    """Deterministic local transport used by tests and offline development."""

    def __init__(self) -> None:
        self._servers: dict[str, McpServerConnection] = {}

    def connect(self, server: McpServerConnection) -> McpServerDescriptor:
        self._servers[server.descriptor.server_id] = server
        return server.descriptor

    def disconnect(self, server_id: str) -> None:
        self._servers.pop(server_id, None)

    def list_tools(self, server_id: str) -> list[McpToolDescriptor]:
        return [tool for tool, _ in self._servers[server_id].tools.values()]

    def list_prompts(self, server_id: str) -> list[McpPromptDescriptor]:
        return list(self._servers[server_id].prompts.values())

    def list_resources(self, server_id: str) -> list[McpResourceDescriptor]:
        return list(self._servers[server_id].resources.values())

    def call_tool(self, server_id: str, tool_name: str, input: JsonObject) -> ToolResult:
        _, handler = self._servers[server_id].tools[tool_name]
        return handler(input)

    def get_prompt(self, server_id: str, prompt_name: str, args: JsonObject) -> str:
        prompt = self._servers[server_id].prompts[prompt_name]
        return prompt.template.format_map(_SafeFormatMap(args))

    def read_resource(self, server_id: str, resource_uri: str) -> McpResourceDescriptor:
        return self._servers[server_id].resources[resource_uri]


class TransportBackedMcpClient:
    """Delegate MCP operations to a transport implementation."""

    def __init__(self, transport: McpTransport) -> None:
        self._transport = transport

    def list_tools(self, server_id: str) -> list[McpToolDescriptor]:
        return self._transport.list_tools(server_id)

    def list_prompts(self, server_id: str) -> list[McpPromptDescriptor]:
        return self._transport.list_prompts(server_id)

    def list_resources(self, server_id: str) -> list[McpResourceDescriptor]:
        return self._transport.list_resources(server_id)

    def call_tool(self, server_id: str, tool_name: str, input: JsonObject) -> ToolResult:
        return self._transport.call_tool(server_id, tool_name, input)

    def get_prompt(self, server_id: str, prompt_name: str, args: JsonObject) -> str:
        return self._transport.get_prompt(server_id, prompt_name, args)

    def read_resource(self, server_id: str, resource_uri: str) -> McpResourceDescriptor:
        return self._transport.read_resource(server_id, resource_uri)


class InMemoryMcpClient(TransportBackedMcpClient):
    """Minimal MCP client for deterministic local tests."""

    def __init__(self, transport: InMemoryMcpTransport | None = None) -> None:
        self._transport_impl = transport or InMemoryMcpTransport()
        super().__init__(self._transport_impl)

    def connect(self, server: McpServerConnection) -> McpServerDescriptor:
        return self._transport_impl.connect(server)

    def disconnect(self, server_id: str) -> None:
        self._transport_impl.disconnect(server_id)


class McpToolAdapter:
    def adapt_mcp_tool(self, server_id: str, remote_tool: McpToolDescriptor) -> McpToolDescriptor:
        remote_tool.description = f"[mcp:{server_id}] {remote_tool.description}"
        return remote_tool


class McpPromptAdapter:
    def adapt_mcp_prompt(self, server_id: str, remote_prompt: McpPromptDescriptor) -> Command:
        return Command(
            id=f"mcp__{server_id}__{remote_prompt.name}",
            name=remote_prompt.name,
            kind=CommandKind.PROMPT,
            description=remote_prompt.description,
            visibility=CommandVisibility.BOTH,
            source="mcp_prompt",
            metadata={"server_id": server_id, "prompt_name": remote_prompt.name},
        )


class McpSkillAdapter:
    def discover_skills_from_resources(
        self,
        server_id: str,
        resources: list[McpResourceDescriptor],
    ) -> list[SkillDefinition]:
        del server_id
        skills: list[SkillDefinition] = []
        for resource in resources:
            if not resource.uri.startswith("skill://"):
                continue
            skill_id = resource.uri.removeprefix("skill://")
            skills.append(
                SkillDefinition(
                    id=skill_id,
                    name=resource.name,
                    description=resource.description,
                    content=resource.content,
                    metadata={"source_uri": resource.uri, "loaded_from": "mcp"},
                )
            )
        return skills

    def adapt_mcp_skill(self, server_id: str, remote_skill: SkillDefinition) -> SkillDefinition:
        remote_skill.metadata["server_id"] = server_id
        return remote_skill


class _SafeFormatMap(dict[str, JsonValue]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
