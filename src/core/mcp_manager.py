# src/core/mcp_manager.py

import asyncio
import threading

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS, async_with_timeout

log = get_logger(__name__)


class MCPManager:

    def __init__(self):
        self.client = None
        self.tools: list = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None
        self.session = None
        self.session_context = None
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

        self.client = MultiServerMCPClient(
            {
                "playwright": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp@latest"],
                }
            }
        )

        with TimedBlock(log, "mcp.session_open"):
            self.session_context = self.client.session("playwright")
            self.session = await async_with_timeout(
                self.session_context.__aenter__(),
                timeout=TIMEOUTS.MCP_TOOL,
                operation="mcp.session_open",
            )

        with TimedBlock(log, "mcp.load_tools"):
            self.tools = await async_with_timeout(
                load_mcp_tools(self.session),
                timeout=TIMEOUTS.MCP_TOOL,
                operation="mcp.load_tools",
            )

        log.info("mcp.initialize.done", tool_count=len(self.tools),
                 tools=[t.name for t in self.tools])
        self.initialized = True

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):
        log.info("mcp.shutdown.start")
        try:
            self.tools = []
            self.session = None
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
