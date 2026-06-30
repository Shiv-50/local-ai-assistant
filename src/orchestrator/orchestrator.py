import asyncio
import concurrent.futures

from langgraph.errors import GraphRecursionError

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS
from src.core.mcp_manager import mcp_manager

log = get_logger(__name__)


class SimpleRouterOrchestrator:
    """
    Routes queries to the correct ReAct agent and invokes it on the
    shared persistent event loop (mcp_manager.loop) so that httpx /
    anyio async resources are never closed from the wrong loop.
    """

    BROWSER_KEYWORDS = [
        "website", "browser", "url", "http", "www",
        "open page", "navigate", "login", "github.com",
    ]

    def __init__(self, general_agent, browser_agent):
        self.general_agent = general_agent
        self.browser_agent = browser_agent

    # ─────────────────────────────────────────────────────────
    # ROUTING
    # ─────────────────────────────────────────────────────────

    def _select_agent(self, query: str):
        is_browser = any(kw in query.lower() for kw in self.BROWSER_KEYWORDS)
        agent_type = "BROWSER" if is_browser else "GENERAL"
        agent = self.browser_agent if is_browser else self.general_agent
        log.info("router.selected", agent_type=agent_type, query_snippet=query[:80])
        return agent, agent_type

    # ─────────────────────────────────────────────────────────
    # HISTORY BUILDER
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_messages(history: list, query: str) -> list:
        messages = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append(("human" if role == "user" else "ai", content))

        if not messages or messages[-1][0] != "human":
            messages.append(("human", query))

        return messages

    # ─────────────────────────────────────────────────────────
    # INVOKE  (called from a plain background thread in main.py)
    # ─────────────────────────────────────────────────────────

    def invoke(self, state: dict) -> dict:
        query   = state.get("user_goal", "")
        history = state.get("conversation_history", [])

        log.info("orchestrator.invoke.start", query_len=len(query))

        agent, agent_type = self._select_agent(query)
        messages = self._build_messages(history, query)

        timeout = (
            TIMEOUTS.MCP_TOOL      if agent_type == "BROWSER"
            else TIMEOUTS.LLM_INFERENCE
        )

        try:
            with TimedBlock(log, "agent.ainvoke", agent_type=agent_type):
                # ── KEY FIX ──────────────────────────────────────────
                # Submit to the already-running loop instead of asyncio.run().
                # This keeps httpx/anyio connections on a single live loop,
                # preventing "Event loop is closed" on cleanup.
                future = asyncio.run_coroutine_threadsafe(
                    agent.ainvoke(
                        {"messages": messages},
                        config={"recursion_limit": 10},
                    ),
                    mcp_manager.loop,
                )
                result_state = future.result(timeout=timeout)

            final_message = result_state["messages"][-1].content
            log.info("orchestrator.invoke.done",
                     agent_type=agent_type,
                     response_len=len(final_message))

            return {"response": final_message}

        except GraphRecursionError:
            log.warning("orchestrator.recursion_limit", agent_type=agent_type)
            return {
                "response": (
                    "I stopped because the desktop agent repeated too many "
                    "steps. Please try again with a more specific target."
                )
            }

        except concurrent.futures.TimeoutError:
            future.cancel()
            log.error("orchestrator.agent_timeout",
                      agent_type=agent_type, timeout_s=timeout)
            return {
                "response": (
                    f"The {agent_type.lower()} agent timed out after {timeout}s. "
                    "Please try again."
                )
            }

        except Exception:
            log.exception("orchestrator.invoke.error", agent_type=agent_type)
            return {
                "response": "An error occurred. Check assistant.log for details."
            }
