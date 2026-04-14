"""TUI-first host profile assembly placeholder."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.context_governance import ContextGovernance
from openagent.gateway import FileSessionBindingStore, IngressGateway, InProcessSessionAdapter
from openagent.harness import ModelAdapter, SimpleHarness
from openagent.orchestration import FileTaskManager, InMemoryTaskManager
from openagent.session import InMemorySessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolDefinition


@dataclass(slots=True)
class TuiProfile:
    """Assembly placeholder for a local single-process host profile.

    Profiles represent deployment and wiring choices, not new domain modules.
    """

    name: str = "tui"
    binding_name: str = "in_process"

    def create_runtime(
        self,
        model: ModelAdapter,
        tools: list[ToolDefinition] | None = None,
    ) -> SimpleHarness:
        registry = StaticToolRegistry(tools or [])
        return SimpleHarness(
            model=model,
            sessions=InMemorySessionStore(),
            tools=registry,
            executor=SimpleToolExecutor(registry),
            context_governance=ContextGovernance(),
        )

    def create_gateway(
        self,
        model: ModelAdapter,
        tools: list[ToolDefinition] | None = None,
        binding_root: str | None = None,
    ) -> IngressGateway:
        runtime = self.create_runtime(model=model, tools=tools)
        binding_store = FileSessionBindingStore(binding_root) if binding_root is not None else None
        return IngressGateway(InProcessSessionAdapter(runtime), binding_store=binding_store)

    def create_task_manager(self, root: str | None = None) -> InMemoryTaskManager | FileTaskManager:
        if root is not None:
            return FileTaskManager(root)
        return InMemoryTaskManager()
