"""Feishu host profile baseline."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.gateway.feishu import FeishuChannelAdapter, create_feishu_runtime
from openagent.harness import ModelProviderAdapter, SimpleHarness
from openagent.harness.providers import load_model_from_env
from openagent.tools import ToolDefinition


@dataclass(slots=True)
class FeishuProfile:
    """Local Feishu profile using direct file-backed runtime calls."""

    name: str = "feishu"
    binding_name: str = "in_process"

    def create_runtime(
        self,
        model: ModelProviderAdapter,
        session_root: str,
        tools: list[ToolDefinition] | None = None,
    ) -> SimpleHarness:
        return create_feishu_runtime(model=model, session_root=session_root, tools=tools)

    def create_channel_adapter(
        self,
        mention_required_in_group: bool = True,
    ) -> FeishuChannelAdapter:
        return FeishuChannelAdapter(mention_required_in_group=mention_required_in_group)

    def create_runtime_from_env(
        self,
        session_root: str,
        tools: list[ToolDefinition] | None = None,
    ) -> SimpleHarness:
        return self.create_runtime(
            model=load_model_from_env(),
            session_root=session_root,
            tools=tools,
        )
