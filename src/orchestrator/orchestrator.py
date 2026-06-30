import asyncio
import concurrent.futures
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langgraph.errors import GraphRecursionError

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS
from src.core.mcp_manager import mcp_manager

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# TASK STATE (NEW - CRITICAL)
# ─────────────────────────────────────────────────────────────

@dataclass
class TaskState:
    task_id: str
    query: str
    agent_type: str = ""
    status: str = "created"   # created → running → done → failed
    events: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None

    def emit(self, event_type: str, data: dict = None):
        event = {
            "type": event_type,
            "data": data or {}
        }
        self.events.append(event)
        log.info("task.event", task_id=self.task_id, event=event)


# ─────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

class SimpleRouterOrchestrator:

    BROWSER_KEYWORDS = [
        "website", "browser", "url", "http", "www",
        "open page", "navigate", "login", "github.com",
    ]

    def __init__(self, general_agent, browser_agent):
        self.general_agent = general_agent
        self.browser_agent = browser_agent

    # ─────────────────────────────────────────────
    # ROUTING (IMPROVED HOOKED)
    # ─────────────────────────────────────────────

    def _select_agent(self, query: str):
        is_browser = any(kw in query.lower() for kw in self.BROWSER_KEYWORDS)
        return (
            self.browser_agent if is_browser else self.general_agent,
            "BROWSER" if is_browser else "GENERAL"
        )

    # ─────────────────────────────────────────────
    # HISTORY
    # ─────────────────────────────────────────────

    @staticmethod
    def _build_messages(history: list, query: str):
        messages = []

        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append(("human" if role == "user" else "ai", content))

        if not messages or messages[-1][0] != "human":
            messages.append(("human", query))

        return messages

    # ─────────────────────────────────────────────
    # MAIN ENTRY
    # ─────────────────────────────────────────────

    def invoke(self, state: dict) -> dict:

        task = TaskState(
            task_id=str(uuid.uuid4()),
            query=state.get("user_goal", "")
        )

        history = state.get("conversation_history", [])

        task.emit("task_started", {"query": task.query})
        log.info("orchestrator.invoke.start", task_id=task.task_id)

        agent, agent_type = self._select_agent(task.query)
        task.agent_type = agent_type
        task.status = "running"

        task.emit("agent_selected", {"agent": agent_type})

        messages = self._build_messages(history, task.query)

        timeout = (
            TIMEOUTS.MCP_TOOL if agent_type == "BROWSER"
            else TIMEOUTS.LLM_INFERENCE
        )

        try:
            with TimedBlock(log, "agent.ainvoke", agent_type=agent_type):

                future = asyncio.run_coroutine_threadsafe(
                    agent.ainvoke(
                        {"messages": messages},
                        config={
                            "recursion_limit": 10,
                            "metadata": {"task_id": task.task_id}
                        },
                    ),
                    mcp_manager.loop,
                )

                task.emit("agent_started")

                result_state = future.result(timeout=timeout)

            messages = result_state.get("messages", []) if isinstance(result_state, dict) else []

            if not messages:
                raise ValueError("Agent returned no messages")

            final_message = messages[-1].content

            task.status = "done"
            task.result = final_message

            task.emit("task_completed", {"result": final_message})

            log.info("orchestrator.invoke.done",
                     task_id=task.task_id,
                     agent_type=agent_type,
                     response_len=len(final_message))

            return {
                "task_id": task.task_id,
                "response": final_message,
                "events": task.events
            }

        # ─────────────────────────────────────────────
        # ERROR HANDLING (IMPROVED)
        # ─────────────────────────────────────────────

        except concurrent.futures.TimeoutError:
            task.status = "failed"
            task.emit("timeout", {"timeout": timeout})

            future.cancel()

            log.error("orchestrator.timeout",
                      task_id=task.task_id,
                      agent_type=agent_type)

            return {
                "task_id": task.task_id,
                "response": f"The {agent_type.lower()} agent timed out.",
                "events": task.events
            }

        except GraphRecursionError:
            task.status = "failed"
            task.emit("recursion_error")

            return {
                "task_id": task.task_id,
                "response": "Agent exceeded recursion limit.",
                "events": task.events
            }

        except Exception as e:
            task.status = "failed"
            task.emit("error", {"message": str(e)})

            log.exception("orchestrator.error", task_id=task.task_id)

            return {
                "task_id": task.task_id,
                "response": "Internal error occurred.",
                "events": task.events
            }