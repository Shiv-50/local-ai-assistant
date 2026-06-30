import sys
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from PyQt6.QtWidgets import QApplication
from src.ui.overlay import create_app

from src.orchestrator.orchestrator import SimpleRouterOrchestrator
from src.agents.react_agents import create_general_agent, create_browser_agent
from src.agents.response_builder_agent import ResponseBuilderAgent

from src.prompts.desktop_agent import system_prompt as general_prompt
from src.prompts.browser_agent import system_prompt as browser_prompt

from src.llm.llm_manager import llm_manager
import asyncio
from src.core.mcp_manager import mcp_manager

# ── logging must be set up before any other import logs ──────────────────────
from src.utils.logger import setup_logging, get_logger

setup_logging(level="INFO", log_file="assistant.log")
log = get_logger(__name__)

from src.utils.timeout import TIMEOUTS, run_with_timeout


# =========================================================
# BUILD MODELS
# =========================================================

def build_models():
    log.info("build_models.start")
    # Only preload the main model we need
    llm_manager.preload_router("qwen2.5-coder:7b")

    models = {
        "core_agent": llm_manager.get_model(
            model_name="qwen2.5-coder:7b",
            temperature=0.2,
            num_predict=1024,
            # NOTE: timeout is applied per-call inside llm_manager; see TIMEOUTS.LLM_INFERENCE
        ),
        "agent": llm_manager.get_model(
            model_name="qwen2.5:7b",
            temperature=0.2,
            num_predict=1024,
            # NOTE: timeout is applied per-call inside llm_manager; see TIMEOUTS.LLM_INFERENCE
        ),
        "response_builder": llm_manager.get_model(
            model_family="google",
            model_name="gemini-3.1-flash-lite",
            temperature=0.2,
            num_predict=2048,
            timeout=TIMEOUTS.REMOTE_LLM,
        ),
    }

    log.info("build_models.done", models=list(models.keys()))
    return models


# =========================================================
# BUILD SYSTEM
# =========================================================

def build_system():
    models = build_models()

    # ---------------------------------
    # REACT AGENTS
    # ---------------------------------

    general_agent = create_general_agent(
        llm=models["agent"],
        system_prompt=general_prompt,
        search_tools=mcp_manager.search_tools,
    )

    browser_tools = mcp_manager.tools if mcp_manager.tools else []
    browser_agent = create_browser_agent(
        llm=models["agent"],
        mcp_tools=browser_tools,
        system_prompt=browser_prompt
    )

    # ---------------------------------
    # ORCHESTRATOR & RESPONSE BUILDER
    # ---------------------------------

    orchestrator = SimpleRouterOrchestrator(
        general_agent=general_agent,
        browser_agent=browser_agent
    )

    response_builder = ResponseBuilderAgent(
        llm=models["response_builder"]
    )

    log.info("build_system.done")
    return orchestrator, response_builder


# =========================================================
# CONTROLLER
# =========================================================

class AppController:
    def __init__(self, overlay, orchestrator, response_builder):
        self.overlay = overlay
        self.orchestrator = orchestrator
        self.response_builder = response_builder
        self.running = True
        self.conversation_history = []
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.current_future: Optional[Future] = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

        # SIGNALS
        overlay.user_input_signal.connect(self.handle_user_input)
        overlay.cancel_signal.connect(self.cancel)
        overlay.shutdown_signal.connect(self.shutdown)

    # =====================================================
    # MAIN USER QUERY HANDLER
    # =====================================================

    def handle_user_input(self, text: str):
        with self._lock:
            if self.current_future and not self.current_future.done():
                self.overlay.populate_cards_external([{
                    "title": "Busy",
                    "content": "A request is already in progress. Please wait or cancel before sending another query.",
                    "type": "warning",
                }])
                return

            self._cancel_event.clear()
            self.overlay.update_state("thinking")
            self.current_future = self.executor.submit(self._process_user_input, text)

    def _process_user_input(self, text: str):
        log.info("user_input.received", text_len=len(text))

        try:
            self.conversation_history.append({
                "role": "user",
                "content": text,
            })

            state = {
                "user_goal": text,
                "conversation_history": list(self.conversation_history),
            }

            try:
                result = run_with_timeout(
                    self.orchestrator.invoke,
                    state,
                    timeout=TIMEOUTS.ORCHESTRATOR,
                    operation="orchestrator.invoke",
                )
            except TimeoutError:
                log.error("orchestrator.timeout", timeout_s=TIMEOUTS.ORCHESTRATOR)
                self._display_error(
                    "Timeout",
                    f"The request took too long to complete (>{TIMEOUTS.ORCHESTRATOR}s). Try a simpler query or check if Ollama is running.",
                )
                return

            if self._cancel_event.is_set():
                log.info("user_input.cancelled_before_response")
                return

            agent_response_text = result.get("response", "")
            log.info("orchestrator.response", response_len=len(agent_response_text))

            try:
                final_response = run_with_timeout(
                    self.response_builder.build,
                    text,
                    agent_response_text,
                    timeout=TIMEOUTS.REMOTE_LLM,
                    operation="response_builder.build",
                )
            except TimeoutError:
                log.warning("response_builder.timeout – falling back to plain card")
                final_response = {
                    "cards": [{
                        "title": "Assistant",
                        "content": agent_response_text,
                        "type": "info",
                        "url": None,
                    }]
                }

            if self._cancel_event.is_set():
                log.info("user_input.cancelled_after_response")
                return

            cards = self._to_cards(final_response)
            assistant_text = " | ".join(
                c.get("content", "") for c in cards if c.get("content")
            )
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_text,
            })

            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            self.overlay.populate_cards_external(cards)
            self.overlay.update_state("ready")
            log.info("user_input.handled", cards=len(cards))

        except Exception:
            log.exception("user_input.unhandled_error")
            self._display_error(
                "System Error",
                "An unexpected error occurred. Check assistant.log for details.",
            )

        finally:
            with self._lock:
                self.current_future = None

    def _display_error(self, title: str, message: str):
        self.overlay.update_state("error")
        self.overlay.populate_cards_external([{
            "title": title,
            "content": message,
            "type": "error",
        }])

    # =====================================================
    # BACKEND → UI CARD FORMAT
    # =====================================================

    def _to_cards(self, result):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                return [{"title": "Response", "content": result, "type": "info"}]

        if isinstance(result, dict):
            if "cards" in result:
                return result["cards"]
            if "response" in result:
                return [{"title": "Assistant", "content": result["response"], "type": "info"}]

        return [{"title": "Result", "content": str(result), "type": "info"}]

    def cancel(self):
        log.info("cancel.requested")
        self._cancel_event.set()
        if self.current_future and not self.current_future.done():
            self.current_future.cancel()
        self.overlay.update_state("ready")
        self.overlay.populate_cards_external([{
            "title": "Cancelled",
            "content": "The current request has been cancelled.",
            "type": "warning",
        }])

    def shutdown(self):
        log.info("shutdown.start")
        self.running = False
        try:
            mcp_manager.shutdown()
        except Exception:
            log.exception("mcp.shutdown_failed")

        try:
            self.executor.shutdown(wait=False)
        except Exception:
            log.exception("executor.shutdown_failed")

        llm_manager.unload_all()
        self.overlay.close()
        app = QApplication.instance()
        if app:
            app.quit()
        log.info("shutdown.complete")


# =========================================================
# MAIN
# =========================================================

def main():
    log.info("app.starting")
    mcp_manager.start_loop()
    mcp_manager.run_async(mcp_manager.initialize())

    app, overlay = create_app()
    orchestrator, response_builder = build_system()

    controller = AppController(
        overlay=overlay,
        orchestrator=orchestrator,
        response_builder=response_builder
    )

    overlay.show()
    log.info("app.ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
