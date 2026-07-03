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
from src.agents.response_builder_agent import ResponseBuilderAgent

from src.prompts.desktop_agent import system_prompt as general_prompt
from src.prompts.browser_agent import system_prompt as browser_prompt

from src.llm.llm_manager import llm_manager
import asyncio
from src.core.mcp_manager import mcp_manager

from src.utils.logger import setup_logging, get_logger
setup_logging(level="INFO", log_file="assistant.log")
log = get_logger(__name__)

from src.utils.timeout import TIMEOUTS, run_with_timeout
from src.memory_store import (
    record_failed_attempt,
    record_user_preference,
    record_feedback,
)

from src.agents.react_agents import (
    create_general_agent,
    create_browser_agent,
)

# =========================================================
# HISTORY COMPRESSION (NEW CRITICAL FIX)
# =========================================================

def compress_history(history, max_items=6):
    """
    Keeps only recent + relevant signal.
    Removes long conversational drift that destroys reasoning.
    """

    compressed = []

    for msg in history[-max_items:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()

        if len(content) > 300:
            content = content[:300] + "..."

        compressed.append({
            "role": role,
            "content": content
        })

    return compressed


# =========================================================
# MODEL BUILDER
# =========================================================

def build_models():
    log.info("build_models.start")

    llm_manager.preload_router("qwen2.5:7b")

    models = {
        "core_agent": llm_manager.get_model(
            model_name="qwen2.5:7b-instruct",
            temperature=0.2,
            num_predict=1024,
        ),
        "agent": llm_manager.get_model(
            model_name="qwen2.5:7b",
            temperature=0.2,
            num_predict=1024,
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
# SYSTEM BUILDER
# =========================================================

def build_system():
    models = build_models()

    general_agent = create_general_agent(
        llm=models["core_agent"],
        system_prompt=general_prompt,
        search_tools=mcp_manager.search_tools,
    )

    browser_agent = create_browser_agent(
        llm=models["agent"],
        mcp_tools=mcp_manager.tools or [],
        system_prompt=browser_prompt
    )

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

        self.conversation_history = []
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.current_future: Optional[Future] = None
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

        overlay.user_input_signal.connect(self.handle_user_input)
        overlay.cancel_signal.connect(self.cancel)
        overlay.shutdown_signal.connect(self.shutdown)
        overlay.feedback_signal.connect(self.handle_feedback)

    # =====================================================
    # INPUT HANDLER
    # =====================================================

    def handle_user_input(self, text: str):
        with self._lock:
            if self.current_future and not self.current_future.done():
                self.overlay.populate_cards_external([{
                    "title": "Busy",
                    "content": "Wait for current task to finish or cancel it.",
                    "type": "warning",
                }])
                return

            self._cancel_event.clear()
            self.overlay.update_state("thinking")
            self.current_future = self.executor.submit(self._process_user_input, text)
        
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

    # =====================================================
    # CORE PIPELINE
    # =====================================================

    def _process_user_input(self, text: str):
        log.info("user_input.received", text_len=len(text))

        if "remember" in text.lower() or "prefer" in text.lower():
            record_user_preference(content=text)

        try:
            self.conversation_history.append({"role": "user", "content": text})

            # -------------------------------
            # TASK FOCUS HEADER (CRITICAL)
            # -------------------------------
            task_prompt = f"""
[TASK]
{text}

[INSTRUCTION]
Focus only on completing this task. Ignore unrelated history unless necessary.
"""

            state = {
                "user_goal": text,
                "conversation_history": compress_history(self.conversation_history),
            }

            state["conversation_history"].insert(0, {
                "role": "user",
                "content": task_prompt
            })

            result = run_with_timeout(
                self.orchestrator.invoke,
                state,
                timeout=TIMEOUTS.ORCHESTRATOR,
                operation="orchestrator.invoke",
                on_step=self._on_agent_step,
            )

            if self._cancel_event.is_set():
                return

            agent_response_text = result.get("response", "")

            if not result.get("grounded", True):
                record_failed_attempt(
                    content="Ungrounded tool-free success claim detected.",
                    metadata_fields={"query": text},
                )

                cards = [{
                    "title": "No action was actually taken",
                    "content": agent_response_text,
                    "type": "warning",
                }]

                self._finish_turn(text, cards, agent_response_text)
                return

            final_response = run_with_timeout(
                self.response_builder.build,
                text,
                agent_response_text,
                timeout=TIMEOUTS.REMOTE_LLM,
                operation="response_builder.build",
            )

            cards = self._to_cards(final_response)
            self._finish_turn(text, cards, agent_response_text)

        except Exception:
            log.exception("user_input.error")
            self._display_error("System Error", "Unexpected failure occurred.")

        finally:
            with self._lock:
                self.current_future = None

    # =====================================================
    # FINALIZATION
    # =====================================================

    def _finish_turn(self, text: str, cards: list, agent_response_text: str):

        assistant_text = " | ".join(
            c.get("content", "") for c in cards if c.get("content")
        )

        self.conversation_history.append({
            "role": "assistant",
            "content": assistant_text
        })

        self.conversation_history = self.conversation_history[-20:]

        self.overlay.populate_cards_external(cards)
        self.overlay.update_state("ready")

        record_feedback(
            content=assistant_text,
            metadata_fields={
                "query": text,
                "agent_response": agent_response_text[:300],
            }
        )

    # =====================================================
    # LIVE STEPS
    # =====================================================

    def _on_agent_step(self, event: dict):
        if event["type"] == "step_started":
            self.overlay.update_step(f"→ {event.get('tool', '')}")

        elif event["type"] == "step_result":
            self.overlay.update_step("✓ step complete")

        elif event["type"] == "finalizing":
            self.overlay.update_step("Finalizing...")

    # =====================================================
    # UI HELPERS
    # =====================================================

    def _display_error(self, title, msg):
        self.overlay.populate_cards_external([{
            "title": title,
            "content": msg,
            "type": "error"
        }])

    def _to_cards(self, result):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                return [{"title": "Response", "content": result, "type": "info"}]

        if isinstance(result, dict) and "cards" in result:
            return result["cards"]

        return [{
            "title": "Result",
            "content": str(result),
            "type": "info"
        }]

    # =====================================================
    # CONTROL
    # =====================================================

    def cancel(self):
        self._cancel_event.set()
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
# MAIN
# =========================================================

def main():
    log.info("app.start")

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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()