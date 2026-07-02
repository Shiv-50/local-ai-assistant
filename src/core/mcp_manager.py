# src/core/mcp_manager.py

import asyncio
import os
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

MCP_DISABLED = os.getenv("DISABLE_MCP", "0").lower() in ("1", "true", "yes")


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
        self._shutdown_event: asyncio.Event | None = None
        self._session_task: asyncio.Task | None = None
        self._playwright_ready: asyncio.Event | None = None
        self._search_ready: asyncio.Event | None = None

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
        if self.loop is None:
            raise RuntimeError("MCP loop has not been started")

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

        if MCP_DISABLED:
            log.warning("mcp.initialize.disabled", reason="DISABLE_MCP enabled")
            self.tools = []
            self.search_tools = []
            self.initialized = True
            self._playwright_ready = asyncio.Event()
            self._search_ready = asyncio.Event()
            self._playwright_ready.set()
            self._search_ready.set()
            return

        server_config = _build_server_config()
        self.client = MultiServerMCPClient(server_config)
        self._shutdown_event = asyncio.Event()
        self._playwright_ready = asyncio.Event()
        self._search_ready = asyncio.Event()

        self._session_task = asyncio.create_task(self._run_session_manager())

        await async_with_timeout(
            self._playwright_ready.wait(),
            timeout=TIMEOUTS.MCP_TOOL,
            operation="mcp.playwright_ready",
        )

        await async_with_timeout(
            self._search_ready.wait(),
            timeout=TIMEOUTS.MCP_TOOL,
            operation="mcp.search_ready",
        )

        log.info("mcp.initialize.done",
                 browser_tools=len(self.tools), search_tools=len(self.search_tools))
        self.initialized = True

    # =====================================================
    # SESSION MANAGEMENT
    # =====================================================

    async def _run_session_manager(self):
        try:
            async with self.client.session("playwright") as session:
                self.session = session
                with TimedBlock(log, "mcp.load_tools", server="playwright"):
                    self.tools = await load_mcp_tools(self.session)
                self._playwright_ready.set()

                try:
                    async with self.client.session("search") as search_session:
                        self._search_session = search_session
                        with TimedBlock(log, "mcp.load_tools", server="search"):
                            self.search_tools = await load_mcp_tools(self._search_session)
                        self._search_ready.set()
                        await self._shutdown_event.wait()
                except Exception as exc:
                    error_text = str(exc)
                    if isinstance(exc, OSError):
                        log.warning("mcp.initialize.search_blocked", error=error_text)
                    else:
                        log.exception("mcp.initialize.search_failed")
                    self.search_tools = []
                    self._search_ready.set()
                    await self._shutdown_event.wait()
        except OSError as e:
            log.exception("mcp.initialize.blocked", error=str(e))
            self.tools = []
            self.search_tools = []
            self._playwright_ready.set()
            self._search_ready.set()
        except Exception:
            log.exception("mcp.initialize.playwright_failed")
            self.tools = []
            self.search_tools = []
            self._playwright_ready.set()
            self._search_ready.set()

        finally:
            log.info("mcp.session_manager.stopped")

    async def _wait_for_task(self, task: asyncio.Task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):
        log.info("mcp.shutdown.start")

        try:
            if self.loop and self.loop.is_running():
                if self._shutdown_event is not None:
                    self.loop.call_soon_threadsafe(self._shutdown_event.set)

                if self._session_task is not None:
                    future = asyncio.run_coroutine_threadsafe(
                        self._wait_for_task(self._session_task),
                        self.loop,
                    )
                    future.result(timeout=TIMEOUTS.MCP_TOOL)

            self.tools = []
            self.search_tools = []
            self.session = None
            self._search_session = None
            self.session_context = None
            self._search_session_context = None
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
