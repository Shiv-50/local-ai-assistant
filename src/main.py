import sys
import json
import logging
import threading

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


# =========================================================
# BUILD MODELS
# =========================================================

def build_models():
    # Only preload the main model we need
    llm_manager.preload_router("qwen2.5:7b")

    models = {
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
        ),
    }

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
        system_prompt=general_prompt
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

        # SIGNALS
        overlay.user_input_signal.connect(self.handle_user_input)
        overlay.cancel_signal.connect(self.cancel)
        overlay.shutdown_signal.connect(self.shutdown)

    # =====================================================
    # MAIN USER QUERY HANDLER
    # =====================================================

    def handle_user_input(self, text):
        def run():
            try:
                logging.info(f"USER: {text}")
                self.overlay.update_state("thinking")

                self.conversation_history.append({
                    "role": "user",
                    "content": text,
                })

                state = {
                    "user_goal": text,
                    "conversation_history": list(self.conversation_history),
                }

                # Invoke Router -> ReAct Agent
                result = self.orchestrator.invoke(state)
                agent_response_text = result.get("response", "")

                # Build UI Cards
                final_response = self.response_builder.build(
                    query=text,
                    agent_response_text=agent_response_text
                )

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

            except Exception as e:
                logging.exception(e)
                self.overlay.update_state("error")
                self.overlay.populate_cards_external([{
                    "title": "System Error",
                    "content": str(e),
                    "type": "error",
                }])

        threading.Thread(target=run, daemon=True).start()

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
        logging.info("Cancel requested")
        self.overlay.update_state("ready")

    def shutdown(self):
        logging.info("Shutting down...")
        self.running = False
        try:
            mcp_manager.shutdown()
        except Exception:
            logging.exception("MCP shutdown failed")

        llm_manager.unload_all()
        self.overlay.close()
        app = QApplication.instance()
        if app:
            app.quit()


# =========================================================
# MAIN
# =========================================================

def main():
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