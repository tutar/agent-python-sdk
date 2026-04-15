"""Host profile exports."""

from openagent.profiles.desktop import DesktopExtension, DesktopExtensionManager, DesktopProfile
from openagent.profiles.feishu import FeishuProfile
from openagent.profiles.tui import TuiProfile

__all__ = [
    "DesktopExtension",
    "DesktopExtensionManager",
    "DesktopProfile",
    "FeishuProfile",
    "TuiProfile",
]
