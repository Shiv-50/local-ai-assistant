import os
import sys
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

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
from src.memory_store import record_failed_attempt, record_user_preference, record_feedback, search_memory


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
        overlay.feedback_signal.connect(self.handle_feedback)

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

        if text.strip().lower().startswith("remember") or "prefer" in text.lower():
            record_user_preference(
                content=text,
                metadata_fields={"source": "conversation"},
            )

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
                    on_step=self._on_agent_step,
                )
            except TimeoutError:
                log.error("orchestrator.timeout", timeout_s=TIMEOUTS.ORCHESTRATOR)
                record_failed_attempt(
                    content="Orchestrator timed out while processing the user query.",
                    metadata_fields={
                        "query": text,
                        "timeout_seconds": TIMEOUTS.ORCHESTRATOR,
                    },
                    source="system",
                )
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

            if not result.get("grounded", True):
                # The orchestrator already intercepted a fabricated
                # success claim. Don't hand this to another LLM to
                # paraphrase — that risks losing the caveat. Show it
                # to the user verbatim, as a warning, and record it
                # so future runs can be steered away from repeating it.
                record_failed_attempt(
                    content="Agent claimed a completed action without calling any tool.",
                    metadata_fields={"query": text},
                    source="system",
                )
                cards = [{
                    "title": "No action was actually taken",
                    "content": agent_response_text,
                    "type": "warning",
                }]
                self._finish_turn(text, cards, agent_response_text)
                return

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
                record_failed_attempt(
                    content="Response builder timed out while formatting the assistant response.",
                    metadata_fields={
                        "query": text,
                        "agent_response_preview": agent_response_text[:300],
                        "timeout_seconds": TIMEOUTS.REMOTE_LLM,
                    },
                    source="system",
                )
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
            self._finish_turn(text, cards, agent_response_text)

        except Exception:
            log.exception("user_input.unhandled_error")
            self._display_error(
                "System Error",
                "An unexpected error occurred. Check assistant.log for details.",
            )

        finally:
            with self._lock:
                self.current_future = None

    def _finish_turn(self, text: str, cards: list, agent_response_text: str):
        """Shared tail: record history, show cards, log feedback. Used by
        both the normal (grounded) path and the ungrounded-claim path."""
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

        record_feedback(
            content=assistant_text,
            tags=["assistant_response"],
            metadata_fields={
                "query": text,
                "agent_result": agent_response_text[:300],
            },
        )

    # =====================================================
    # LIVE STEP DISPLAY
    # =====================================================
    #
    # Called (from the mcp event-loop thread) once per step the
    # orchestrator has actually confirmed is happening — either a
    # tool call the agent just decided to make, or the result that
    # came back. Never called for narration alone, so what's shown
    # here can't outrun what the agent is really doing.

    def _on_agent_step(self, event: dict):
        event_type = event.get("type")

        if event_type == "step_started":
            tool = event.get("tool", "tool")
            thought = (event.get("thought") or "").strip()
            label = f"→ {thought}" if thought else f"→ Running {tool}…"
            self.overlay.update_step(label)

        elif event_type == "step_result":
            tool = event.get("tool", "tool")
            result = (event.get("result") or "").strip()
            label = f"✓ {tool} done" + (f": {result}" if result else "")
            self.overlay.update_step(label)

        elif event_type == "finalizing":
            self.overlay.update_step("Preparing the final response…")

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
                cards = result["cards"]
            elif "response" in result:
                cards = [{"title": "Assistant", "content": result["response"], "type": "info"}]
            else:
                cards = [{"title": "Result", "content": str(result), "type": "info"}]
        else:
            cards = [{"title": "Result", "content": str(result), "type": "info"}]

        for card in cards:
            if card.get("type") in {"info", "warning", "error"}:
                card["feedback_payload"] = {
                    "content": card.get("content", ""),
                    "query": self.conversation_history[-1]["content"] if self.conversation_history else "",
                    "card_title": card.get("title", "Assistant"),
                    "card_type": card.get("type", "info"),
                }

        return cards

    def handle_feedback(self, payload: dict):
        if not isinstance(payload, dict):
            return

        feedback_text = payload.get("content", "")
        if not feedback_text:
            return

        record_failed_attempt(
            content=f"User marked assistant response as failed: {feedback_text}",
            metadata_fields={
                "query": payload.get("query"),
                "card_title": payload.get("card_title"),
                "card_type": payload.get("card_type"),
            },
            source="user_feedback",
        )

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
    try:
        mcp_manager.run_async(mcp_manager.initialize())
    except Exception as e:
        log.exception("mcp.initialize.failed", error=str(e))
        log.warning("MCP initialization failed; continuing without MCP tools.")

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
