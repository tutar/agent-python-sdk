import json
import sys
from pathlib import Path
from subprocess import PIPE, Popen


def test_terminal_bridge_smoke() -> None:
    bridge = (
        Path(__file__).resolve().parents[1] / "frontend" / "terminal-tui" / "scripts" / "bridge.py"
    )
    process = Popen(
        [sys.executable, str(bridge)],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
    )

    assert process.stdout is not None
    assert process.stdin is not None

    ready = json.loads(process.stdout.readline())
    assert ready["message"] == "ready"

    process.stdin.write(json.dumps({"kind": "message", "content": "hello"}) + "\n")
    process.stdin.flush()

    event_types = [json.loads(process.stdout.readline())["event_type"] for _ in range(3)]
    process.kill()

    assert event_types == ["turn_started", "assistant_message", "turn_completed"]


def test_terminal_bridge_session_binding_and_listing() -> None:
    bridge = (
        Path(__file__).resolve().parents[1] / "frontend" / "terminal-tui" / "scripts" / "bridge.py"
    )
    process = Popen(
        [sys.executable, str(bridge)],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
    )

    assert process.stdin is not None
    assert process.stdout is not None

    ready = json.loads(process.stdout.readline())
    assert ready["message"] == "ready"
    assert ready["session_name"] == "main"

    process.stdin.write(json.dumps({"kind": "bind", "session_name": "ops"}) + "\n")
    process.stdin.flush()

    bound = json.loads(process.stdout.readline())
    assert bound["message"] == "bound"
    assert bound["session_name"] == "ops"

    process.stdin.write(json.dumps({"kind": "list_sessions"}) + "\n")
    process.stdin.flush()

    listing = json.loads(process.stdout.readline())
    process.kill()

    assert listing["type"] == "sessions"
    assert listing["current_session_name"] == "ops"
    assert listing["sessions"] == ["main", "ops"]
