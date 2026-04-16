"""CLI wrapper for the Feishu long-connection host."""

from __future__ import annotations

from openagent.gateway.feishu import main as _gateway_main


def main() -> None:
    """Start the default Feishu long-connection host."""

    _gateway_main()


if __name__ == "__main__":
    main()
