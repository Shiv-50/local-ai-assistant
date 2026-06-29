import asyncio

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS, async_with_timeout

log = get_logger(__name__)


class SimpleRouterOrchestrator:
    """
    Replaces the complex AgentGraphBuilder.
    A simple router that selects between general desktop and browser ReAct agents,
    and invokes them using LangGraph's native tool-calling loop.
    """

    # Keywords that force the browser agent.
    # IMPROVEMENT: replace with an LLM-based intent classifier so novel
    # phrasings ("visit the page", "check that site") are also caught.
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

        # Ensure the tail is a human turn
        if not messages or messages[-1][0] != "human":
            messages.append(("human", query))

        return messages

    # ─────────────────────────────────────────────────────────
    # INVOKE  (sync entry point called from the UI thread)
    # ─────────────────────────────────────────────────────────

    def invoke(self, state: dict) -> dict:
        query   = state.get("user_goal", "")
        history = state.get("conversation_history", [])

        log.info("orchestrator.invoke.start", query_len=len(query))

        agent, agent_type = self._select_agent(query)
        messages = self._build_messages(history, query)

        try:
            with TimedBlock(log, "agent.ainvoke", agent_type=agent_type):
                result_state = asyncio.run(
                    self._invoke_with_timeout(agent, messages, agent_type)
                )

            final_message = result_state["messages"][-1].content
            log.info("orchestrator.invoke.done",
                     agent_type=agent_type,
                     response_len=len(final_message))

            return {"response": final_message}

        except asyncio.TimeoutError:
            # Already logged inside async_with_timeout
            return {
                "response": (
                    f"The {agent_type.lower()} agent timed out after "
                    f"{TIMEOUTS.MCP_TOOL}s. Please try again."
                )
            }

        except Exception:
            log.exception("orchestrator.invoke.error", agent_type=agent_type)
            return {
                "response": "System encountered an error during execution. "
                            "Check assistant.log for details."
            }

    # ─────────────────────────────────────────────────────────
    # ASYNC HELPER
    # ─────────────────────────────────────────────────────────

    async def _invoke_with_timeout(self, agent, messages: list, agent_type: str):
        timeout = (
            TIMEOUTS.MCP_TOOL if agent_type == "BROWSER"
            else TIMEOUTS.LLM_INFERENCE
        )
        return await async_with_timeout(
            agent.ainvoke({"messages": messages}),
            timeout=timeout,
            operation=f"{agent_type}_agent.ainvoke",
        )
