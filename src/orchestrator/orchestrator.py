import asyncio
import concurrent.futures
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS
from src.core.mcp_manager import mcp_manager
from src.memory_store import search_memory

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# GROUNDING CHECK
# ─────────────────────────────────────────────────────────────
#
# The step feed only ever reports a step for a tool call that
# actually happened — which also means it's the source of truth for
# whether the agent did anything at all. If the model's final text
# asserts a completed action ("Pinterest has been successfully
# opened...") but the run made zero tool calls, that claim is false
# and must never reach the user as-is. This is a deterministic,
# code-level backstop — prompt instructions alone are not reliable
# enough to stop a local model from writing this kind of text.

_ACTION_CLAIM_PATTERNS = [
    re.compile(r"\bsuccessfully\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bhas\s+been\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bhave\s+been\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bi(?:'ve| have)\s+(opened|launched|closed|clicked|typed|installed|"
               r"created|deleted|removed|sent|saved|downloaded|navigated|searched|"
               r"completed|finished|updated|moved|renamed|copied)\b", re.IGNORECASE),
    re.compile(r"\b(opened|launched|installed|closed)\s+(on|in)\s+your\s+(desktop|screen|browser|computer)\b",
               re.IGNORECASE),
    re.compile(r"\btask\s+(is\s+)?complete[d]?\b", re.IGNORECASE),
]


def _claims_completed_action(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _ACTION_CLAIM_PATTERNS)


# ─────────────────────────────────────────────────────────────
# STEP FORMATTING
# ─────────────────────────────────────────────────────────────
#
# A "step" event is only ever emitted for something that is
# guaranteed to actually happen: a tool call the model has just
# made (already decided, already in the graph state — the tools
# node is about to execute it), or the observation that tool call
# produced. We never emit a step from free-text narration alone,
# because free text can claim things the model doesn't follow
# through on. This keeps the live step feed honest by construction:
# whatever the user sees as "the next step" is a step that is either
# already running or already ran.

def _format_tool_args(args: dict, limit: int = 120) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        text = str(args)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _format_observation(content, limit: int = 200) -> str:
    text = content if isinstance(content, str) else str(content)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


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
    def _build_memory_context(self, query: str) -> str:
        memories = search_memory(
            query=query,
            top_k=5,
            categories=["user_preference", "failed_attempt", "feedback"],
        )

        if not memories:
            return ""

        lines = [
            "Relevant user preferences and prior assistant failures for this request:",
        ]

        for memory in memories:
            category = memory.get("category", "generic")
            content = memory.get("content", "").strip()
            if content:
                lines.append(f"- [{category}] {content}")

        lines.append(
            "Use these details to personalize the assistant response and avoid repeating prior mistakes."
        )

        return "\n".join(lines)

    # ─────────────────────────────────────────────
    # STREAMING: emit one event per ACTUAL step
    # ─────────────────────────────────────────────
    #
    # Walks the agent's message list after every graph node runs
    # (stream_mode="values" gives us the full running state each
    # time). Anything beyond `seen` is new since the last chunk.
    # We turn each new AIMessage tool_call into a "step_started"
    # event (the step is already decided and about to execute —
    # never a step that's merely been talked about) and each new
    # ToolMessage into a "step_result" event with its outcome.

    async def _stream_agent(self, agent, messages, config, task: TaskState,
                             on_step: Optional[Callable[[dict], None]]):
        seen = 0
        final_messages: list = []
        tool_calls_made = 0

        def _emit(event_type: str, data: dict):
            task.emit(event_type, data)
            if on_step:
                try:
                    on_step({"type": event_type, **data})
                except Exception:
                    log.exception("orchestrator.on_step_callback_failed")

        async for chunk in agent.astream(
            {"messages": messages}, config=config, stream_mode="values"
        ):
            chunk_messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
            final_messages = chunk_messages

            for msg in chunk_messages[seen:]:
                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    thought = msg.content if isinstance(msg.content, str) else ""
                    thought = thought.strip()
                    for tc in msg.tool_calls:
                        tool_calls_made += 1
                        _emit("step_started", {
                            "tool": tc.get("name", "unknown_tool"),
                            "args": _format_tool_args(tc.get("args", {})),
                            "thought": thought,
                        })
                elif isinstance(msg, ToolMessage):
                    _emit("step_result", {
                        "tool": getattr(msg, "name", None) or "tool",
                        "result": _format_observation(msg.content),
                    })
                elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    # Final answer turn — nothing further will run.
                    _emit("finalizing", {})

            seen = len(chunk_messages)

        return {"messages": final_messages, "tool_calls_made": tool_calls_made}

    # ─────────────────────────────────────────────
    # MAIN ENTRY
    # ─────────────────────────────────────────────

    def invoke(self, state: dict, on_step: Optional[Callable[[dict], None]] = None) -> dict:

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

        memory_context = self._build_memory_context(task.query)
        messages = self._build_messages(history, task.query)

        if memory_context:
            messages.insert(0, ("human", memory_context))

        timeout = (
            TIMEOUTS.MCP_TOOL if agent_type == "BROWSER"
            else TIMEOUTS.LLM_INFERENCE
        )

        try:
            with TimedBlock(log, "agent.astream", agent_type=agent_type):

                future = asyncio.run_coroutine_threadsafe(
                    self._stream_agent(
                        agent,
                        messages,
                        config={
                            "recursion_limit": 10,
                            "metadata": {"task_id": task.task_id}
                        },
                        task=task,
                        on_step=on_step,
                    ),
                    mcp_manager.loop,
                )

                task.emit("agent_started")

                result_state = future.result(timeout=timeout)

            messages = result_state.get("messages", []) if isinstance(result_state, dict) else []
            tool_calls_made = result_state.get("tool_calls_made", 0) if isinstance(result_state, dict) else 0

            if not messages:
                raise ValueError("Agent returned no messages")

            final_message = messages[-1].content

            grounded = True
            if tool_calls_made == 0 and _claims_completed_action(final_message):
                grounded = False
                task.emit("ungrounded_claim_blocked", {
                    "original_response": final_message,
                    "agent": agent_type,
                })
                log.warning(
                    "orchestrator.ungrounded_claim_blocked",
                    task_id=task.task_id,
                    agent_type=agent_type,
                    original_response=final_message[:300],
                )
                final_message = (
                    "I didn't actually do that — no tool ran during this turn, so my draft "
                    "response claiming it was done would have been false. Either the request "
                    "needs a tool I didn't call, or I need clearer instructions. Can you "
                    "rephrase, or tell me exactly what you'd like me to try?"
                )

            task.status = "done"
            task.result = final_message

            task.emit("task_completed", {"result": final_message})

            log.info("orchestrator.invoke.done",
                     task_id=task.task_id,
                     agent_type=agent_type,
                     response_len=len(final_message),
                     grounded=grounded,
                     tool_calls_made=tool_calls_made)

            return {
                "task_id": task.task_id,
                "response": final_message,
                "events": task.events,
                "grounded": grounded,
                "tool_calls_made": tool_calls_made,
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
