# local-ai-assistant

## MCP tool prerequisites

This assistant uses two MCP servers, spawned automatically on startup:

- **Browser automation** — `@playwright/mcp` via `npx` (requires Node.js).
- **Web search** — [`duckduckgo-mcp-server`](https://pypi.org/project/duckduckgo-mcp-server/) via `uvx` (requires [`uv`](https://docs.astral.sh/uv/)). Free, no API key required. Provides a `search` tool and a `fetch_content` tool for pulling clean page text from a result URL — this replaces the old hand-rolled `parallel_search` scraper.

Make sure `npx` and `uvx` are on your `PATH` before launching the assistant.
