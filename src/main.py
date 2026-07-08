# src/main.py

import sys
import logging
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional, Dict, Any

from PyQt6.QtWidgets import QApplication

from src.ui.overlay import create_app

from src.orchestrator.orchestrator import GraphOrchestrator

from src.agents.react_agents import (
    create_router_agent,
    create_general_agent,
    create_browser_agent,
    create_domain_agent,
)

from src.prompts.desktop_agent import system_prompt as general_prompt
from src.prompts.browser_agent import system_prompt as browser_prompt
from src.prompts.router_agent import system_prompt as router_prompt
from src.prompts.web_agent import system_prompt as web_prompt
from src.prompts.shell_agent import system_prompt as shell_prompt
from src.prompts.vision_agent import system_prompt as vision_prompt
from src.prompts.desktop_browser_agent import system_prompt as desktop_browser_prompt

from src.tools.system_tools import execute_shell_command
from src.tools.vision_tools import analyze_screen_with_vision
from src.tools.browser.browser_tool import BrowserTool, get_tools as get_browser_tools
from src.tools.desktop.desktop_browser_tool import (
    DesktopAppTool,
    get_tools as get_desktop_browser_tools,
)

from src.llm.llm_manager import llm_manager
from src.core.mcp_manager import mcp_manager


from src.utils.logger import setup_logging, get_logger
from src.utils.timeout import TIMEOUTS

setup_logging(level="INFO", log_file="assistant.log")
log = get_logger(__name__)


def build_models():
    # NOTE: preload target must match what's actually requested below,
    # or you silently double-load two 7B models.
    llm_manager.preload_router("qwen2.5:7b-instruct")

    return {
        "router": llm_manager.get_model(
            model_name="qwen2.5:7b-instruct",
            temperature=0.2,
            num_predict=1024,
        ),
        "general": llm_manager.get_model(
            model_name="qwen2.5:7b",
            temperature=0.2,
            num_predict=1024,
        ),
        "browser": llm_manager.get_model(
            model_name="qwen2.5:7b-instruct",
            temperature=0.2,
            num_predict=1024,
        ),
    }


def build_system():
    models = build_models()

    router_agent = create_router_agent(models["router"], system_prompt=router_prompt)

    general_agent = create_general_agent(
        llm=models["general"],
        system_prompt=general_prompt,
        search_tools=[],  # search now lives in the dedicated web_agent below
    )

    browser_tool = BrowserTool()

    browser_agent = create_browser_agent(
        llm=models["browser"],
        mcp_tools=get_browser_tools(browser_tool),
        system_prompt=browser_prompt,
        state_provider=browser_tool.describe_state,
    )

    desktop_app_tool = DesktopAppTool()

    desktop_browser_agent = create_browser_agent(
        llm=models["browser"],
        mcp_tools=get_desktop_browser_tools(desktop_app_tool),
        system_prompt=desktop_browser_prompt,
        state_provider=desktop_app_tool.describe_state,
    )

    web_agent = create_domain_agent(
        llm=models["general"],
        tools=list(mcp_manager.search_tools),
        system_prompt=web_prompt,
    )

    shell_agent = create_domain_agent(
        llm=models["general"],
        tools=[execute_shell_command],
        system_prompt=shell_prompt,
    )

    vision_agent = create_domain_agent(
        llm=models["general"],
        tools=[analyze_screen_with_vision],
        system_prompt=vision_prompt,
    )

    orchestrator = GraphOrchestrator(
        router_agent=router_agent,
        agent_registry={
            "general": {
                "graph": general_agent,
                "description": "General reasoning, coding, writing, and desktop UI actions (launch apps, click, type) that don't need search, shell, or screen analysis.",
                "use_cases": [
                    "reasoning", "coding", "writing", "summarization",
                    "analysis", "launching/focusing apps", "typing/clicking",
                ],
            },
            "browser": {
                "graph": browser_agent,
                "description": "Full browser automation via Playwright MCP.",
                "use_cases": [
                    "web navigation", "clicking UI elements",
                    "login flows", "form filling", "scraping a live page",
                ],
            },
            "desktop_app": {
                "graph": desktop_browser_agent,
                "description": (
                    "Automates Electron-based desktop apps (Slack, VS Code, "
                    "Discord, Figma, Notion, Spotify, and similar Chromium-shell "
                    "apps) via agent-browser over the Chrome DevTools Protocol. "
                    "Use ONLY for Electron apps -- for any other native Windows "
                    "app, use 'general' (launch/click/type) or 'vision' instead."
                ),
                "use_cases": [
                    "check Slack unreads or send a Slack message",
                    "click/type inside VS Code, Discord, Figma, Notion, or Spotify",
                    "navigate within an Electron desktop app",
                    "read structured UI state from an Electron app",
                ],
            },
            "web": {
                "graph": web_agent,
                "description": "Web search for facts/information via the DuckDuckGo MCP server.",
                "use_cases": [
                    "answer a factual question from the web",
                    "look up current information",
                    "find a URL or source",
                ],
            },
            "shell": {
                "graph": shell_agent,
                "description": "Run Windows shell/PowerShell commands.",
                "use_cases": [
                    "run a CLI/PowerShell command",
                    "query system state via shell",
                ],
            },
            "vision": {
                "graph": vision_agent,
                "description": "Analyze the current screen visually to locate UI elements or describe what's shown.",
                "use_cases": [
                    "what's on screen right now",
                    "find coordinates of a button/element",
                    "verify a UI state visually",
                ],
            },
        },
    )

    log.info("system.initialized")

    return orchestrator


# AppController class and main() stay unchanged.

# =========================================================
# CONTROLLER (UI INTEGRATION LAYER)
# =========================================================

class AppController:

    def __init__(self, overlay, orchestrator):
        self.overlay = overlay
        self.orchestrator = orchestrator

        self.executor = ThreadPoolExecutor(max_workers=2)
        self.current_future: Optional[Future] = None

        self.conversation_history = []

        overlay.user_input_signal.connect(self.handle_user_input)
        overlay.cancel_signal.connect(self.cancel)
        overlay.shutdown_signal.connect(self.shutdown)

    # -----------------------------------------------------
    # INPUT HANDLER
    # -----------------------------------------------------

    def handle_user_input(self, text: str):

        if self.current_future and not self.current_future.done():
            self.overlay.update_state("busy")
            self.overlay.populate_cards_external([
                {
                    "title": "Busy",
                    "content": "Wait for current task or cancel it.",
                    "type": "warning",
                }
            ])
            return

        self.overlay.update_state("thinking")
        self.current_future = self.executor.submit(self._run, text)

    # -----------------------------------------------------
    # CORE EXECUTION
    # -----------------------------------------------------

# src/main.py — wire the overlay's step indicator into the run, in AppController._run:

    def _run(self, text: str):

        log.info("user.request", text=text)

        self.conversation_history.append({
            "role": "user",
            "content": text
        })

        state = {
            "user_goal": text,
            "conversation_history": self.conversation_history[-10:]
        }

        def _on_step(chunk):
            msgs = chunk.get("messages", [])
            if not msgs:
                return
            last = msgs[-1]
            tool_calls = getattr(last, "tool_calls", None)
            if tool_calls:
                names = ", ".join(tc.get("name", "?") for tc in tool_calls)
                self.overlay.update_step(f"Calling: {names}")
            else:
                content = getattr(last, "content", "")
                if isinstance(content, str) and content.strip():
                    self.overlay.update_step(content.strip()[:120])

        result = self.orchestrator.invoke(state, on_step=_on_step)

        response = result.get("response", "")

        self._finish(text, response)

        return result

    # -----------------------------------------------------
    # FINALIZATION
    # -----------------------------------------------------

    def _finish(self, query: str, response: str):

        self.conversation_history.append({
            "role": "assistant",
            "content": response
        })

        self.conversation_history = self.conversation_history[-20:]

        self.overlay.populate_cards_external([
            {
                "title": "Result",
                "content": response,
                "type": "info",
            }
        ])

        self.overlay.update_state("ready")

    # -----------------------------------------------------
    # CONTROL
    # -----------------------------------------------------

    def cancel(self):
        self.overlay.update_state("ready")

    def shutdown(self):
        try:
            mcp_manager.shutdown()
        except:
            pass

        self.executor.shutdown(wait=False)

        llm_manager.unload_all()

        self.overlay.close()
        app = QApplication.instance()
        if app:
            app.quit()


# =========================================================
# MAIN ENTRYPOINT
# =========================================================

def main():
    log.info("app.start")

    mcp_manager.start_loop()
    mcp_manager.run_async(mcp_manager.initialize())

    app, overlay = create_app()

    orchestrator = build_system()

    controller = AppController(
        overlay=overlay,
        orchestrator=orchestrator
    )

    overlay.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
