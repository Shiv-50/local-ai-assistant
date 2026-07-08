# local-ai-assistant

## MCP tool prerequisites

This assistant uses two MCP servers, spawned automatically on startup:

- **Browser automation** — `@playwright/mcp` via `npx` (requires Node.js).
- **Web search** — [`duckduckgo-mcp-server`](https://pypi.org/project/duckduckgo-mcp-server/) via `uvx` (requires [`uv`](https://docs.astral.sh/uv/)). Free, no API key required. Provides a `search` tool and a `fetch_content` tool for pulling clean page text from a result URL — this replaces the old hand-rolled `parallel_search` scraper.

Make sure `npx` and `uvx` are on your `PATH` before launching the assistant.

## Electron desktop-app automation (agent-browser)

The `desktop_app` agent automates Electron-based desktop apps (Slack, VS
Code, Discord, Figma, Notion, Spotify, and similar Chromium-shell apps) via
[`agent-browser`](https://github.com/vercel-labs/agent-browser), a CLI that
drives apps over the Chrome DevTools Protocol using the same
snapshot-then-interact workflow as the Playwright browser agent. Every
Electron app supports `--remote-debugging-port` since it's built into
Chromium, so `agent-browser` can automate them the same way it automates a
regular web page.

This is a **separate capability from the existing `vision`/`general`
Win32 automation** (pyautogui/pywinauto), which still handles any non-Electron
native app. Only route Electron-app requests to `desktop_app`.

Install:

```bash
npm i -g agent-browser
agent-browser install
```

Make sure `agent-browser` is on your `PATH`. The agent will launch known
apps (`slack`, `vscode`, `discord`, `figma`, `notion`, `spotify`) with the
debugging port pre-wired; other Electron apps work too as long as their
executable is on `PATH` — pass the exact executable name as `app_name`.

If an app is already running without the debugging flag, it needs to be
fully quit (check the system tray) before the agent can relaunch it with
`--remote-debugging-port` enabled.
