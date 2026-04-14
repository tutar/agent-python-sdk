# Terminal TUI

React + Ink terminal frontend for the Python SDK. Ink itself is built on Yoga, so this keeps the
same terminal UI stack style as `claude-code`.

The TUI does not call the harness directly. It talks to a local Python gateway bridge over stdio JSON lines.

## Run

From `agent-python-sdk/`:

```bash
cd frontend/terminal-tui
npm install
npm run dev
```

If `python3` is not the right interpreter on your machine, set `PYTHON` first:

```bash
PYTHON=/path/to/python npm run dev
```

## Real Provider Mode

If you want the bridge to call a real LLM backend instead of the demo model, set:

```bash
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
cd frontend/terminal-tui
npm run dev
```

Or for Anthropic-compatible proxies:

```bash
export OPENAGENT_PROVIDER=anthropic
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=claude-sonnet-4-5
cd frontend/terminal-tui
npm run dev
```

## Demo Commands

- Plain text: normal assistant reply
- `tool <text>`: trigger the demo echo tool
- `admin <text>`: trigger a permission-gated tool
- `/new <name>`: create and bind a local session
- `/switch <name>`: switch to an existing local session and replay its event log
- `/sessions`: list known local sessions
- `/approve`: approve the pending tool request
- `/reject`: reject the pending tool request
- `/interrupt`: interrupt the current session handle
- `/session`: print local session state
- `/clear`: clear the local log view
- `/help`
- `/exit`
