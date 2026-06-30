# src/core/mcp_manager.py

import asyncio
import threading

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS, async_with_timeout

log = get_logger(__name__)


# =========================================================
# MCP SERVER DEFINITIONS
# =========================================================
#
# "playwright" – full browser automation (click/scroll/screenshot/etc.)
#   used by the dedicated browser_agent.
#
# "search" – the DuckDuckGo MCP server (nickclyde/duckduckgo-mcp-server,
#   run via `uvx`). It's free and requires no API key (unlike Tavily/
#   Brave/etc.), and it already covers both halves of what the old
#   parallel_search.py hand-rolled: a `search` tool (rate-limited,
#   LLM-formatted DuckDuckGo results) and a `fetch_content` tool that
#   downloads + cleans a given URL's page text. That removes the need
#   for our own BeautifulSoup/trafilatura scraping and CSS-selector
#   parsers entirely.
#   https://pypi.org/project/duckduckgo-mcp-server/
#
# Both are reachable over stdio; "local vs cloud" doesn't matter since
# search inherently needs internet access either way.

def _build_server_config() -> dict:
    return {
        "playwright": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
        },
        "search": {
            "transport": "stdio",
            "command": "uvx",
            "args": ["duckduckgo-mcp-server"],
        },
    }


class MCPManager:

    def __init__(self):
        self.client = None
        self.tools: list = []            # playwright (browser) tools
        self.search_tools: list = []      # DuckDuckGo search MCP tools
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None
        self.session = None
        self.session_context = None
        self._search_session = None
        self._search_session_context = None
        self.initialized: bool = False

    # =====================================================
    # START BACKGROUND LOOP
    # =====================================================

    def start_loop(self):
        if self.loop:
            return

        self.loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.loop_thread = threading.Thread(
            target=runner, daemon=True, name="mcp-event-loop"
        )
        self.loop_thread.start()
        log.info("mcp.loop_started")

    # =====================================================
    # RUN ASYNC FROM SYNC CODE
    # =====================================================

    def run_async(self, coro, timeout: float = TIMEOUTS.MCP_TOOL):
        """Submit *coro* to the background loop and block until done or timeout."""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            log.error("mcp.run_async_timeout", timeout_s=timeout)
            raise

    # =====================================================
    # INITIALIZE MCP
    # =====================================================

    async def initialize(self):
        if self.initialized:
            return

        log.info("mcp.initialize.start")

        server_config = _build_server_config()
        self.client = MultiServerMCPClient(server_config)

        # ---- playwright (browser automation) ----
        with TimedBlock(log, "mcp.session_open", server="playwright"):
            self.session_context = self.client.session("playwright")
            self.session = await async_with_timeout(
                self.session_context.__aenter__(),
                timeout=TIMEOUTS.MCP_TOOL,
                operation="mcp.session_open.playwright",
            )

        with TimedBlock(log, "mcp.load_tools", server="playwright"):
            self.tools = await async_with_timeout(
                load_mcp_tools(self.session),
                timeout=TIMEOUTS.MCP_TOOL,
                operation="mcp.load_tools.playwright",
            )

        log.info("mcp.initialize.playwright_done", tool_count=len(self.tools),
                 tools=[t.name for t in self.tools])

        # ---- search (DuckDuckGo, free, no API key) — replaces parallel_search.py ----
        try:
            with TimedBlock(log, "mcp.session_open", server="search"):
                self._search_session_context = self.client.session("search")
                self._search_session = await async_with_timeout(
                    self._search_session_context.__aenter__(),
                    timeout=TIMEOUTS.MCP_TOOL,
                    operation="mcp.session_open.search",
                )

            with TimedBlock(log, "mcp.load_tools", server="search"):
                self.search_tools = await async_with_timeout(
                    load_mcp_tools(self._search_session),
                    timeout=TIMEOUTS.MCP_TOOL,
                    operation="mcp.load_tools.search",
                )

            log.info("mcp.initialize.search_done", tool_count=len(self.search_tools),
                     tools=[t.name for t in self.search_tools])
        except Exception:
            log.exception("mcp.initialize.search_failed")
            self.search_tools = []

        log.info("mcp.initialize.done",
                 browser_tools=len(self.tools), search_tools=len(self.search_tools))
        self.initialized = True

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):
        log.info("mcp.shutdown.start")
        try:
            self.tools = []
            self.search_tools = []
            self.session = None
            self._search_session = None
            self.client = None
            self.initialized = False
            log.info("mcp.shutdown.done")
        except Exception:
            log.exception("mcp.shutdown.error")

        try:
            if self.loop:
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass


mcp_manager = MCPManager()
