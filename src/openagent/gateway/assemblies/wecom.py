"""Compatibility exports for WeCom assembly helpers."""

from openagent.gateway.channels.wecom.assembly import (
    WeComAppConfig,
    create_wecom_gateway,
    create_wecom_host,
    create_wecom_host_from_env,
    create_wecom_runtime,
)

__all__ = [
    "WeComAppConfig",
    "create_wecom_gateway",
    "create_wecom_host",
    "create_wecom_host_from_env",
    "create_wecom_runtime",
]
