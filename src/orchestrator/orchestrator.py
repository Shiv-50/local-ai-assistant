import asyncio
import uuid
import concurrent.futures
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from src.utils.logger import get_logger
from src.utils.timeout import TIMEOUTS
from src.core.mcp_manager import mcp_manager
import json
from json_repair import repair_json
from src.memory_store import add_memory, build_memory_context

log = get_logger(__name__)


# =========================================================
# TASK STATE
# =========================================================

@dataclass
class TaskState:
    task_id: str
    query: str
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
# ROUTER-ONLY ORCHESTRATOR (MODULAR VERSION)
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

    # =========================================================
    # DYNAMIC PROMPT BUILDER (KEY FIX)
    # =========================================================

    def _build_router_messages(self, query: str):

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

        system =  registry_block

        memory = build_memory_context(query)
        return [
            ("system", f"Relevent context from prvious conversations:\n{memory}"),
            ("system", system),
            ("human", f"User request:\n{query}")
        ]

    # =========================================================
    # PARSER
    # =========================================================

    def _parse_tasks(self, text: str) -> List[Dict[str, str]]:
        try:
            data = json.loads(repair_json(text))
            print(data)
            return data.get("tasks", [])
        except Exception:
            log.exception("router.parse.failed")
            return []

    # =========================================================
    # AGENT EXECUTION
    # =========================================================

    async def _run_graph_agent(
        self,
        agent_graph,
        task_text: str,
        task_state: TaskState,
        on_step: Optional[Callable] = None
    ):
        task_state.emit("agent_task_started", {"task": task_text})

        final_messages = []

        async for chunk in agent_graph.astream(
            {"messages": [("human", task_text)]},
            config={
                "recursion_limit": 30,
                "metadata": {"task_id": task_state.task_id}
            },
            stream_mode="values"
        ):
            msgs = chunk.get("messages", [])
            final_messages = msgs

            if on_step:
                try:
                    on_step({"messages": msgs})
                except Exception:
                    pass

        final = final_messages[-1].content if final_messages else ""

        task_state.emit("agent_task_completed", {
            "task": task_text,
            "result": final
        })

        add_memory(
            role="assistant",
            content=f"Task: {task_text}\nResult: {final}",
            category="task_summary",
            source="orchestrator"
        )
        return final

    # =========================================================
    # MAIN ENTRY
    # =========================================================

    def invoke(self, state: dict, on_step=None):

        task = TaskState(
            task_id=str(uuid.uuid4()),
            query=state.get("user_goal", "")
        )

        task.emit("task_started", {"query": task.query})

        # -----------------------------
        # ROUTER PHASE
        # -----------------------------

        router_messages = self._build_router_messages(task.query)

        router_result = self.router_agent.invoke({"messages": router_messages})

        if isinstance(router_result, dict):
            msgs = router_result.get("messages", [])
            router_text = msgs[-1].content if msgs else ""
        elif isinstance(router_result, AIMessage):
            router_text = router_result.content
        else:
            router_text = str(router_result)

        tasks = self._parse_tasks(router_text)

        task.emit("tasks_created", {"tasks": tasks})
        add_memory(
            role="system",
            content=f"Decomposed task: {task.query} → {tasks}",
            category="routing_decision"
        )
        if not tasks:
            task.status = "failed"
            task.result = "Router failed to produce tasks."
            return {
                "task_id": task.task_id,
                "response": task.result,
                "events": task.events
            }

        # -----------------------------
        # EXECUTION PHASE
        # -----------------------------

        results = []
        timeout = TIMEOUTS.LLM_INFERENCE

        try:
            for t in tasks:

                agent_name = t.get("agent")
                task_text = t.get("task")

                agent_meta = self.agent_registry.get(agent_name)

                if not agent_meta:
                    task.emit("missing_agent", {"agent": agent_name})
                    continue

                agent_graph = agent_meta["graph"]

                future = asyncio.run_coroutine_threadsafe(
                    self._run_graph_agent(
                        agent_graph=agent_graph,
                        task_text=task_text,
                        task_state=task,
                        on_step=on_step
                    ),
                    mcp_manager.loop
                )

                result = future.result(timeout=timeout)

                results.append({
                    "task": task_text,
                    "agent": agent_name,
                    "result": result
                })

        # -----------------------------
        # ERROR HANDLING
        # -----------------------------

        except concurrent.futures.TimeoutError:
            task.status = "failed"
            task.emit("timeout")
            return {
                "task_id": task.task_id,
                "response": "Execution timed out.",
                "events": task.events
            }

        except GraphRecursionError:
            task.status = "failed"
            task.emit("recursion_error")
            return {
                "task_id": task.task_id,
                "response": "Task exceeded execution limits.",
                "events": task.events
            }

        except Exception as e:
            task.status = "failed"
            task.emit("error", {"message": str(e)})
            log.exception("orchestrator.error")
            record_failed_attempt(
                    content=f"Task failed: {task.query} | Error: {str(e)}"
                )
            return {
                "task_id": task.task_id,
                "response": "Internal error.",
                "events": task.events
            }

        # -----------------------------
        # FINAL OUTPUT
        # -----------------------------

        task.status = "done"

        final_response = "\n\n".join(
            r["result"] for r in results if r.get("result")
        )

        task.result = final_response

        task.emit("task_completed", {"results": results})

        return {
            "task_id": task.task_id,
            "response": final_response,
            "events": task.events,
            "task_breakdown": tasks
        }