import asyncio
import concurrent.futures
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage, SystemMessage

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS
from src.core.mcp_manager import mcp_manager
from src.memory_store import build_memory_context

log = get_logger(__name__)

# =========================================================
# TASK STATE
# =========================================================

@dataclass
class TaskState:
    task_id: str
    query: str
    agent_type: str = ""
    status: str = "created"
    events: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None

    def emit(self, event_type: str, data: dict = None):
        self.events.append({
            "type": event_type,
            "data": data or {}
        })
        log.info("task.event", task_id=self.task_id, event=event_type)


# =========================================================
# ORCHESTRATOR
# =========================================================

class SimpleRouterOrchestrator:

    BROWSER_KEYWORDS = [
        "http", "www", "open site", "browser",
        "navigate", "url", "login", "website"
    ]

    MEMORY_TRIGGER_WORDS = [
        "continue", "resume", "remember",
        "previous", "before", "last time", "again"
    ]

    def __init__(self, general_agent, browser_agent):
        self.general_agent = general_agent
        self.browser_agent = browser_agent

    # -----------------------------------------------------
    # ROUTING
    # -----------------------------------------------------

    def _select_agent(self, query: str):
        q = query.lower()

        is_browser = any(k in q for k in self.BROWSER_KEYWORDS)

        return (
            self.browser_agent if is_browser else self.general_agent,
            "BROWSER" if is_browser else "GENERAL"
        )

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    def _build_messages(self, history: list, query: str):
        messages = []

        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            messages.append((
                "human" if role == "user" else "ai",
                content
            ))

        messages.append(("human", query))
        return messages

    # -----------------------------------------------------
    # MEMORY GATING (CRITICAL FIX)
    # -----------------------------------------------------

    def _should_use_memory(self, query: str) -> bool:
        q = query.lower()

        return any(
            w in q for w in self.MEMORY_TRIGGER_WORDS
        )

    def _get_memory_context(self, query: str) -> str:
        if not self._should_use_memory(query):
            return ""

        return build_memory_context(query)

    # -----------------------------------------------------
    # STREAMING
    # -----------------------------------------------------

    async def _stream_agent(
        self,
        agent,
        messages,
        config,
        task: TaskState,
        on_step: Optional[Callable[[dict], None]] = None
    ):

        seen = 0
        final_messages = []
        tool_calls = 0

        def emit(event_type: str, data: dict = None):
            task.emit(event_type, data or {})
            if on_step:
                try:
                    on_step({"type": event_type, **(data or {})})
                except Exception:
                    pass

        async for chunk in agent.astream(
            {"messages": messages},
            config=config,
            stream_mode="values"
        ):
            msgs = chunk.get("messages", []) if isinstance(chunk, dict) else []
            final_messages = msgs

            for msg in msgs[seen:]:

                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    tool_calls += len(msg.tool_calls)

                    for tc in msg.tool_calls:
                        emit("step_started", {
                            "tool": tc.get("name"),
                            "args": tc.get("args", {})
                        })

                elif isinstance(msg, ToolMessage):
                    emit("step_result", {
                        "tool": getattr(msg, "name", "tool"),
                        "result": str(msg.content)[:200]
                    })

                elif isinstance(msg, AIMessage):
                    if not getattr(msg, "tool_calls", None):
                        emit("finalizing")

            seen = len(msgs)

        return {
            "messages": final_messages,
            "tool_calls": tool_calls
        }

    # -----------------------------------------------------
    # MAIN ENTRY
    # -----------------------------------------------------

    def invoke(self, state: dict, on_step=None):

        task = TaskState(
            task_id=str(uuid.uuid4()),
            query=state.get("user_goal", "")
        )

        history = state.get("conversation_history", [])

        task.emit("task_started", {"query": task.query})

        agent, agent_type = self._select_agent(task.query)
        task.agent_type = agent_type
        task.status = "running"

        task.emit("agent_selected", {"agent": agent_type})

        messages = self._build_messages(history, task.query)

        # -------------------------------
        # MEMORY ONLY FOR GENERAL AGENT
        # -------------------------------
        memory_context = ""

        if agent_type == "GENERAL":
            memory_context = self._get_memory_context(task.query)

        if memory_context:
            messages.insert(
                0,
                ("system", f"Relevant context:\n{memory_context}")
            )

        timeout = (
            TIMEOUTS.MCP_TOOL
            if agent_type == "BROWSER"
            else TIMEOUTS.LLM_INFERENCE
        )

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._stream_agent(
                    agent=agent,
                    messages=messages,
                    config={
                        "recursion_limit": 8,
                        "metadata": {"task_id": task.task_id}
                    },
                    task=task,
                    on_step=on_step
                ),
                mcp_manager.loop
            )

            task.emit("agent_started")

            result = future.result(timeout=timeout)

            msgs = result["messages"]
            tool_calls = result["tool_calls"]

            final = msgs[-1].content if msgs else ""

            # -------------------------------
            # HARD GROUNDING CHECK
            # -------------------------------
            if tool_calls == 0 and (
                "done" in final.lower()
                or "completed" in final.lower()
                or "opened" in final.lower()
            ):
                final = (
                    "I cannot confirm that action was completed because no tool was executed "
                    "during this run. Please retry or clarify the task."
                )

                task.emit("ungrounded_block")

            task.status = "done"
            task.result = final

            task.emit("task_completed", {"result": final})

            return {
                "task_id": task.task_id,
                "response": final,
                "events": task.events,
                "tool_calls_made": tool_calls
            }

        # -------------------------------
        # TIMEOUT HANDLING
        # -------------------------------

        except concurrent.futures.TimeoutError:

            task.status = "failed"
            task.emit("timeout")

            return {
                "task_id": task.task_id,
                "response": "Agent timed out.",
                "events": task.events
            }

        except Exception as e:

            task.status = "failed"
            task.emit("error", {"message": str(e)})

            log.exception("orchestrator.error")

            return {
                "task_id": task.task_id,
                "response": "Internal error.",
                "events": task.events
            }