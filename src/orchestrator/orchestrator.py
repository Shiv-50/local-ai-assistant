import asyncio
import uuid
import concurrent.futures
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import StateGraph, START, END

from src.utils.logger import get_logger
from src.utils.timeout import TIMEOUTS, run_with_timeout
from src.core.mcp_manager import mcp_manager
import json
from json_repair import repair_json
from src.memory_store import add_memory, build_memory_context, record_failed_attempt

log = get_logger(__name__)


# =========================================================
# ORCHESTRATOR ARCHITECTURE
# =========================================================
#
# This used to be "plan once, execute linearly": the router produced a
# fixed task list up front and a plain `for t in tasks: run(t)` loop
# executed it, with no way to react to a task turning out to be
# mis-assigned or unexecutable except crashing out of the whole request.
#
# It's now a small state machine, mirroring the same graph discipline
# already used inside each sub-agent (see src/agents/react_agents.py):
#
#         START
#           |
#           v
#        [router] ----(no tasks / router error)----> END
#           |
#      (tasks planned)
#           |
#           v
#        [execute] <---------------------------+
#           |  |              |                |
#      (more tasks,      (needs replan,   (more tasks
#       success)          under cap)       queued)
#           |                  |                |
#           +------------------+----------------+
#           |
#      (no tasks left, success) --> END
#      (failed / replan cap exceeded) --> [give_up] --> END
#
# Each sub-agent graph now reports a structured status ("success",
# "replan", or "failed" -- see react_agents.py) instead of just a text
# blob, which is what makes the "execute" -> "router" replan edge
# possible: the router gets told *why* a task couldn't be run and can
# produce a corrected plan without restarting the whole request.
#
# Threading model is unchanged from before: this graph's nodes are
# plain sync functions. The "execute" node still bridges into the
# async sub-agent graphs via mcp_manager's dedicated background loop
# (asyncio.run_coroutine_threadsafe(...).result(timeout=...)), exactly
# as the old loop body did. That keeps this refactor scoped to
# orchestration logic and avoids also having to change how AppController
# invokes the orchestrator from main.py.

MAX_REPLANS = 3


def _append(left: Optional[list], right: Optional[list]) -> list:
    """Reducer for list-valued state channels: always append, never
    last-write-wins (see react_agents.py's add_messages comment for why
    this matters -- plain dict/TypedDict list fields are overwritten by
    default when multiple nodes touch them across a run)."""
    return (left or []) + (right or [])


class OrchestrationState(TypedDict, total=False):
    task_id: str
    user_goal: str
    conversation_history: List[dict]

    pending_tasks: List[dict]
    completed_tasks: Annotated[List[dict], _append]
    current_task: Optional[dict]
    last_result: dict

    replan_reason: Optional[str]
    suggested_agent: Optional[str]
    replan_count: int

    status: str            # "running" | "done" | "failed"
    response: str
    events: Annotated[List[dict], _append]


# =========================================================
# ORCHESTRATOR
# =========================================================

class GraphOrchestrator:

    def __init__(
        self,
        router_agent,
        agent_registry: Dict[str, Dict[str, Any]],
    ):
        """
        agent_registry format:

        {
            "browser": {
                "graph": browser_graph,
                "description": "...",
                "use_cases": [...]
            },
            "general": {...}
        }
        """
        self.router_agent = router_agent
        self.agent_registry = agent_registry

        # Set per-invoke in invoke(); read by _execute_node. Orchestrator
        # instances are invoked one request at a time by AppController
        # (it refuses new input while current_future is running), so a
        # plain instance attribute is sufficient here rather than
        # threading on_step through graph config.
        self._on_step: Optional[Callable] = None

        self.graph = self._build_graph()

    # =========================================================
    # GRAPH CONSTRUCTION
    # =========================================================

    def _build_graph(self):
        graph = StateGraph(OrchestrationState)

        graph.add_node("router", self._router_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("give_up", self._give_up_node)

        graph.add_edge(START, "router")

        graph.add_conditional_edges(
            "router",
            self._route_after_router,
            {"execute": "execute", END: END},
        )

        graph.add_conditional_edges(
            "execute",
            self._route_after_execute,
            {
                "execute": "execute",
                "router": "router",
                "give_up": "give_up",
                "done": END,
            },
        )

        graph.add_edge("give_up", END)

        return graph.compile()

    # =========================================================
    # DYNAMIC PROMPT BUILDER
    # =========================================================

    def _build_router_messages(
        self,
        query: str,
        conversation_history: Optional[list] = None,
        completed_tasks: Optional[list] = None,
        replan_reason: Optional[str] = None,
        suggested_agent: Optional[str] = None,
    ):
        registry_block = "\n\nAVAILABLE AGENTS:\n\n"

        for name, meta in self.agent_registry.items():
            registry_block += f"""
                [AGENT: {name}]
                Description:
                {meta.get("description", "")}

                Use cases:
                """
            for uc in meta.get("use_cases", []):
                registry_block += f"- {uc}\n"

            registry_block += "\n---\n"

        system = registry_block

        memory = build_memory_context(query)

        history_block = ""
        if conversation_history:
            recent = conversation_history[-6:]
            lines = [f"{turn.get('role', '?')}: {turn.get('content', '')}" for turn in recent]
            history_block = "\n".join(lines)

        messages = [
            ("system", f"Relevent context from previous conversations:\n{memory}"),
            ("system", f"Recent conversation (most recent last) — use this to resolve follow-ups like 'do it on the browser' or pronouns like 'that site':\n{history_block}"),
            ("system", system),
        ]

        if completed_tasks:
            completed_block = "\n".join(
                f"- [{c.get('agent')}] {c.get('task')} -> {str(c.get('result', ''))[:200]}"
                for c in completed_tasks
            )
            messages.append((
                "system",
                "Tasks already completed successfully earlier in this run "
                f"(do not repeat these):\n{completed_block}",
            ))

        if replan_reason:
            suggestion_line = (
                f"\nThe agent that attempted the failing task suggested trying: {suggested_agent}"
                if suggested_agent else ""
            )
            messages.append((
                "system",
                "REPLAN REQUEST: The previous plan could not be fully executed.\n"
                f"Reason: {replan_reason}{suggestion_line}\n"
                "Produce a NEW task list that avoids repeating the tasks already "
                "completed above, and either fixes the failing task's agent "
                "assignment or rephrases the task so a valid agent can execute "
                "it. Do not simply resend the identical plan.",
            ))

        messages.append(("human", f"User request:\n{query}"))

        return messages

    # =========================================================
    # PARSER
    # =========================================================

    def _parse_tasks(self, text: str) -> List[Dict[str, str]]:
        try:
            data = json.loads(repair_json(text))
            return data.get("tasks", [])
        except Exception:
            log.exception("router.parse.failed")
            return []

    # =========================================================
    # CONTEXT CARRYOVER
    # =========================================================

    def _context_for_agent(self, completed_tasks: List[dict], agent_name: str) -> str:
        lines = [
            f"- Task: {c.get('task')}\n  Result: {c.get('result')}"
            for c in completed_tasks
            if c.get("agent") == agent_name
        ]
        return "\n".join(lines)

    # =========================================================
    # SUB-AGENT EXECUTION (async, bridged onto mcp_manager's loop)
    # =========================================================

    async def _run_graph_agent(
        self,
        agent_graph,
        task_text: str,
        task_id: str,
        on_step: Optional[Callable] = None,
    ) -> dict:
        log.info("agent_task.started", task_id=task_id, task=task_text)

        final_messages = []
        final_state: dict = {}

        async for chunk in agent_graph.astream(
            {"messages": [("human", task_text)]},
            config={
                "recursion_limit": 30,
                "metadata": {"task_id": task_id}
            },
            stream_mode="values"
        ):
            final_state = chunk
            msgs = chunk.get("messages", [])
            final_messages = msgs

            last = msgs[-1] if msgs else None
            if last is not None:
                tool_calls = getattr(last, "tool_calls", None)
                if tool_calls:
                    log.info("agent.tool_call",
                             task_id=task_id,
                             tools=[tc.get("name") for tc in tool_calls])
                else:
                    content = getattr(last, "content", "")
                    log.info("agent.message",
                             task_id=task_id,
                             type=type(last).__name__,
                             preview=str(content)[:200])

            if on_step:
                try:
                    on_step({"messages": msgs})
                except Exception:
                    pass

        final_text = ""
        for msg in reversed(final_messages):
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content.strip():
                final_text = content.strip()
                break

        if not final_text:
            final_text = (
                "The agent stopped without producing a final answer. "
                "This usually means it ran out of steps or a tool call "
                "didn't return usable output — check assistant.log for the "
                "tool calls/results from this task."
            )

        # Structured outcome set by finalize_node / give_up_node in
        # react_agents.py. Defaults to "success" for agents/graphs that
        # somehow never reach those nodes (e.g. zero-chunk stream),
        # matching the previous unconditional behavior.
        status = final_state.get("status", "success")
        status_reason = final_state.get("status_reason", "")
        suggested_agent = final_state.get("suggested_agent")

        add_memory(
            role="assistant",
            content=f"Task: {task_text}\nResult: {final_text}",
            category="task_summary",
            source="orchestrator"
        )

        return {
            "text": final_text,
            "status": status,
            "status_reason": status_reason,
            "suggested_agent": suggested_agent,
        }

    # =========================================================
    # NODE: ROUTER
    # =========================================================

    def _router_node(self, state: OrchestrationState) -> dict:
        query = state.get("user_goal", "")
        events = [{"type": "router_invoke_started", "data": {}}]

        router_messages = self._build_router_messages(
            query,
            conversation_history=state.get("conversation_history", []),
            completed_tasks=state.get("completed_tasks", []),
            replan_reason=state.get("replan_reason"),
            suggested_agent=state.get("suggested_agent"),
        )

        try:
            router_result = run_with_timeout(
                self.router_agent.invoke,
                {"messages": router_messages},
                timeout=TIMEOUTS.LLM_INFERENCE,
                operation="router.invoke",
            )
        except TimeoutError:
            log.error("orchestrator.router_timeout")
            events.append({"type": "router_timeout", "data": {}})
            return {
                "status": "failed",
                "response": "The planning step timed out. Check that the router model is pulled and Ollama is responding.",
                "events": events,
            }
        except Exception as e:
            log.exception("orchestrator.router_error")
            events.append({"type": "router_error", "data": {"message": str(e)}})
            return {
                "status": "failed",
                "response": "The planning step failed. See logs for details.",
                "events": events,
            }

        events.append({"type": "router_invoke_done", "data": {}})

        if isinstance(router_result, dict):
            msgs = router_result.get("messages", [])
            router_text = msgs[-1].content if msgs else ""
        elif isinstance(router_result, AIMessage):
            router_text = router_result.content
        else:
            router_text = str(router_result)

        tasks = self._parse_tasks(router_text)

        events.append({"type": "tasks_created", "data": {"tasks": tasks}})
        add_memory(
            role="system",
            content=f"Decomposed task: {query} → {tasks}",
            category="routing_decision"
        )

        if not tasks:
            return {
                "status": "failed",
                "response": "Router failed to produce tasks.",
                "pending_tasks": [],
                "events": events,
            }

        return {
            "pending_tasks": tasks,
            "replan_reason": None,
            "suggested_agent": None,
            "events": events,
        }

    def _route_after_router(self, state: OrchestrationState) -> str:
        if state.get("status") == "failed":
            return END
        return "execute"

    # =========================================================
    # NODE: EXECUTE
    # =========================================================

    def _execute_node(self, state: OrchestrationState) -> dict:
        pending = list(state.get("pending_tasks", []))

        if not pending:
            # Defensive: router never hands execute an empty list (see
            # _route_after_router), but don't loop forever if it does.
            return {"status": "done"}

        task = pending.pop(0)
        agent_name = task.get("agent")
        task_text = task.get("task")

        events = [{"type": "agent_task_started", "data": {"task": task_text, "agent": agent_name}}]

        agent_meta = self.agent_registry.get(agent_name)

        if not agent_meta:
            events.append({"type": "missing_agent", "data": {"agent": agent_name}})
            return {
                "pending_tasks": pending,
                "current_task": task,
                "last_result": {"status": "replan", "reason": f"Unknown agent '{agent_name}'"},
                "replan_reason": (
                    f"Task '{task_text}' was assigned to agent '{agent_name}', "
                    "which does not exist in the registry."
                ),
                "suggested_agent": None,
                "replan_count": state.get("replan_count", 0) + 1,
                "events": events,
            }

        agent_graph = agent_meta["graph"]

        prior_context = self._context_for_agent(state.get("completed_tasks", []), agent_name)
        if prior_context:
            contextual_task_text = (
                f"Context — steps already completed by you in this session:\n{prior_context}\n\n"
                f"Next step to do now:\n{task_text}"
            )
        else:
            contextual_task_text = task_text

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._run_graph_agent(
                    agent_graph=agent_graph,
                    task_text=contextual_task_text,
                    task_id=state.get("task_id", ""),
                    on_step=self._on_step,
                ),
                mcp_manager.loop
            )
            result = future.result(timeout=TIMEOUTS.LLM_INFERENCE)

        except concurrent.futures.TimeoutError:
            events.append({"type": "timeout", "data": {"task": task_text}})
            return {
                "pending_tasks": pending,
                "current_task": task,
                "last_result": {"status": "failed", "reason": "timed out"},
                "status": "failed",
                "response": "Execution timed out.",
                "events": events,
            }

        except GraphRecursionError:
            events.append({"type": "recursion_error", "data": {"task": task_text}})
            return {
                "pending_tasks": pending,
                "current_task": task,
                "last_result": {"status": "failed", "reason": "recursion limit exceeded"},
                "status": "failed",
                "response": "Task exceeded execution limits.",
                "events": events,
            }

        except Exception as e:
            log.exception("orchestrator.execute_error")
            events.append({"type": "error", "data": {"message": str(e)}})
            record_failed_attempt(
                content=f"Task failed: {state.get('user_goal', '')} | Error: {str(e)}"
            )
            return {
                "pending_tasks": pending,
                "current_task": task,
                "last_result": {"status": "failed", "reason": str(e)},
                "status": "failed",
                "response": "Internal error.",
                "events": events,
            }

        events.append({"type": "agent_task_completed", "data": {"task": task_text, "result": result.get("text", "")}})

        completed_entry = {
            "task": task_text,
            "agent": agent_name,
            "result": result.get("text", ""),
            "status": result.get("status", "success"),
        }

        updates: dict = {
            "pending_tasks": pending,
            "current_task": task,
            "completed_tasks": [completed_entry],
            "last_result": result,
            "events": events,
        }

        if result.get("status") == "replan":
            updates["replan_reason"] = (
                result.get("status_reason")
                or f"The '{agent_name}' agent indicated task '{task_text}' needs a different plan."
            )
            updates["suggested_agent"] = result.get("suggested_agent")
            updates["replan_count"] = state.get("replan_count", 0) + 1
        else:
            updates["replan_reason"] = None
            updates["suggested_agent"] = None

        return updates

    def _route_after_execute(self, state: OrchestrationState) -> str:
        if state.get("status") == "done":
            return "done"
        if state.get("status") == "failed":
            return "give_up"

        last_result = state.get("last_result", {}) or {}
        result_status = last_result.get("status", "success")

        if result_status == "replan":
            if state.get("replan_count", 0) >= MAX_REPLANS:
                return "give_up"
            return "router"

        if result_status == "failed":
            return "give_up"

        # success
        if state.get("pending_tasks"):
            return "execute"
        return "done"

    # =========================================================
    # NODE: GIVE UP
    # =========================================================

    def _give_up_node(self, state: OrchestrationState) -> dict:
        last_result = state.get("last_result", {}) or {}
        reason = last_result.get("reason") or state.get("replan_reason") or "unknown error"

        if state.get("replan_count", 0) >= MAX_REPLANS:
            why = "the router kept proposing plans that couldn't be executed as scoped"
        else:
            why = "a task failed and could not be recovered"

        log.warning("orchestrator.give_up", why=why, reason=reason)

        record_failed_attempt(
            content=f"Task failed: {state.get('user_goal', '')} | Reason: {reason}"
        )

        completed = state.get("completed_tasks", [])
        partial = "\n\n".join(c.get("result", "") for c in completed if c.get("result"))

        # Router/execute failures that already produced a user-facing
        # response (timeouts, internal errors) keep that message as-is;
        # otherwise synthesize one from whatever was salvaged.
        response = state.get("response") or (
            f"I couldn't fully complete this request ({why})."
            + (f" Here's what was completed before stopping:\n\n{partial}" if partial else " No steps completed successfully.")
        )

        return {
            "status": "failed",
            "response": response,
            "events": [{"type": "give_up", "data": {"reason": reason}}],
        }

    # =========================================================
    # PUBLIC ENTRY POINT
    # =========================================================

    def _compose_response(self, final_state: dict) -> str:
        completed = final_state.get("completed_tasks", [])
        return "\n\n".join(c.get("result", "") for c in completed if c.get("result"))

    def invoke(self, state: dict, on_step=None):
        task_id = str(uuid.uuid4())
        self._on_step = on_step

        initial_state: OrchestrationState = {
            "task_id": task_id,
            "user_goal": state.get("user_goal", ""),
            "conversation_history": state.get("conversation_history", []),
            "pending_tasks": [],
            "completed_tasks": [],
            "current_task": None,
            "last_result": {},
            "replan_reason": None,
            "suggested_agent": None,
            "replan_count": 0,
            "status": "running",
            "response": "",
            "events": [{"type": "task_started", "data": {"query": state.get("user_goal", "")}}],
        }

        log.info("task.event", task_id=task_id, event="task_started")

        try:
            final_state = self.graph.invoke(
                initial_state,
                config={"recursion_limit": 50, "metadata": {"task_id": task_id}},
            )
        except GraphRecursionError:
            log.exception("orchestrator.recursion_error")
            return {
                "task_id": task_id,
                "response": "Task exceeded execution limits.",
                "events": initial_state["events"] + [{"type": "recursion_error", "data": {}}],
            }
        finally:
            self._on_step = None

        response = final_state.get("response") or self._compose_response(final_state)

        log.info("task.event", task_id=task_id, event="task_completed")

        return {
            "task_id": task_id,
            "response": response,
            "events": final_state.get("events", []),
            "task_breakdown": final_state.get("completed_tasks", []),
        }