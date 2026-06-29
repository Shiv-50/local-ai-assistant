# src/core/mcp_manager.py

import asyncio
import threading
import logging

from langchain_mcp_adapters.client import (
    MultiServerMCPClient
)

from langchain_mcp_adapters.tools import (
    load_mcp_tools
)


class MCPManager:

    def __init__(self):

        self.client = None
        self.tools = []

        self.loop = None
        self.loop_thread = None

        self.session = None
        self.session_context = None

        self.initialized = False

    # =====================================================
    # START BACKGROUND LOOP
    # =====================================================

    def start_loop(self):

        if self.loop:
            return

        self.loop = asyncio.new_event_loop()

        def runner():

            asyncio.set_event_loop(
                self.loop
            )

            self.loop.run_forever()

        self.loop_thread = threading.Thread(
            target=runner,
            daemon=True
        )

        self.loop_thread.start()

        logging.info(
            "[MCP] Async loop started"
        )

    # =====================================================
    # RUN ASYNC FROM SYNC CODE
    # =====================================================

    def run_async(self, coro):

        future = (
            asyncio.run_coroutine_threadsafe(
                coro,
                self.loop
            )
        )

        return future.result()

    # =====================================================
    # INITIALIZE MCP
    # =====================================================

    async def initialize(self):

        if self.initialized:
            return

        logging.info(
            "[MCP] Starting Playwright MCP..."
        )

        self.client = MultiServerMCPClient(
            {
                "playwright": {

                    "transport": "stdio",

                    "command": "npx",

                    "args": [
                        "-y",
                        "@playwright/mcp@latest"
                    ]
                }
            }
        )

        # ---------------------------------
        # Persistent session
        # ---------------------------------

        self.session_context = (
            self.client.session(
                "playwright"
            )
        )

        self.session = (
            await self.session_context.__aenter__()
        )

        self.tools = await load_mcp_tools(
            self.session
        )

        logging.info(
            f"[MCP] Loaded {len(self.tools)} tools"
        )

        for tool in self.tools:

            logging.info(
                f"[MCP TOOL] {tool.name}"
            )

        self.initialized = True

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):

        logging.info(
            "[MCP] Shutting down..."
        )

        try:
            self.tools = []
            self.session = None
            self.client = None
            self.initialized = False

            logging.info(
                "[MCP] Shutdown complete"
            )
        except Exception:
            logging.exception(
                "[MCP shutdown]"
            )

        try:
            if self.loop:
                self.loop.call_soon_threadsafe(
                    self.loop.stop
                )
        except Exception:
            pass


mcp_manager = MCPManager()