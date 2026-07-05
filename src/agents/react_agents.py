import json
import logging
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from src.tools.all_tools import build_general_tools

log = logging.getLogger(__name__)

# =========================================================
# FAILURE-LOOP CIRCUIT BREAKER
# =========================================================
#
# Root cause this fixes: the graph state used to be a plain `dict`, so
# when ToolNode returned {"messages": [<new tool messages>]}, LangGraph
# REPLACED the whole "messages" list instead of appending to it (plain
# dict channels are last-write-wins, they don't know how to merge lists).
# That silently wiped the conversation history after every single tool
# call: the next agent turn only saw a bare ToolMessage with no matching
# AIMessage.tool_calls before it, and no memory of the original request.
#
# Depending on the model backend, that either raised a message-ordering
# error, or just confused the model into re-issuing the same (or a
# similar) tool call turn after turn, since it no longer remembered
# having tried it -- a "task failure loop" that only ever ended when the
# graph's recursion_limit was exhausted and a GraphRecursionError blew up
# the whole run with no useful explanation.
#
# Fix: use a real reducer (`add_messages`) for the messages channel so
# state is merged/appended correctly, and add an explicit circuit
# breaker that watches for repeated failures *inside* the graph so a
# stuck agent stops itself early with an honest explanation instead of
# quietly grinding through retries until it crashes.

# Guaranteed prefix on any tool call that raised an unhandled exception
# (see src/tools/base.py: safe_tool()).
_HARD_ERROR_PREFIX = "Error executing tool"

# Stop after the agent issues the exact same tool call (name + args) this
# many times back-to-back without the outcome changing.
SAME_ACTION_REPEAT_LIMIT = 2

# Stop after this many consecutive rounds where every tool call in the
# round hard-errored (crashed), even if the calls themselves varied.
CONSECUTIVE_HARD_ERROR_LIMIT = 3


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    last_action_signature: Any
    repeat_count: int
    consecutive_hard_errors: int
    empty_count: int

def _is_hard_error(content: Any) -> bool:
    return isinstance(content, str) and content.startswith(_HARD_ERROR_PREFIX)


def _round_signature(ai_message: Optional[AIMessage]):
    """A hashable fingerprint of the tool call(s) a given AIMessage made."""
    if ai_message is None:
        return None

    calls = getattr(ai_message, "tool_calls", None) or []

    try:
        return tuple(
            sorted(
                (c.get("name"), json.dumps(c.get("args", {}), sort_keys=True, default=str))
                for c in calls
            )
        )
    except Exception:
        return None


def _trailing_tool_round(messages: list[BaseMessage]):
    """
    Walk back from the end of `messages` and return
    (triggering_ai_message, [tool_messages]) for the most recent round of
    tool calls, i.e. the AIMessage that requested them plus the
    ToolMessage(s) that came back.
    """
    tool_msgs: list[ToolMessage] = []
    idx = len(messages) - 1

    while idx >= 0 and isinstance(messages[idx], ToolMessage):
        tool_msgs.append(messages[idx])
        idx -= 1

    tool_msgs.reverse()
    triggering_ai = messages[idx] if idx >= 0 and isinstance(messages[idx], AIMessage) else None

    return triggering_ai, tool_msgs


def _summarize_failure(tool_msgs: list[ToolMessage]) -> str:
    parts = []

    for m in tool_msgs:
        name = getattr(m, "name", None) or "tool"
        text = str(m.content)
        snippet = text.splitlines()[0][:180] if text else "(empty result)"
        parts.append(f"{name}: {snippet}")

    return "; ".join(parts) if parts else "the last step"


# =========================================================
# CORE AGENT BUILDER
# =========================================================

def create_domain_agent(llm, tools, system_prompt: str):

    model_with_tools = llm.bind_tools(tools)

    def agent_node(state: AgentState):
        messages = state.get("messages", [])
        system_msg = SystemMessage(content=system_prompt)

        response = model_with_tools.invoke([system_msg] + list(messages))

        # Only the new message is returned -- the `add_messages` reducer
        # on AgentState.messages appends it to history for us. Returning
        # the full accumulated list here (like before) would also work,
        # but returning just the delta is cheaper and is the pattern the
        # reducer is designed for.
        return {"messages": [response]}


    def failure_check_node(state: AgentState):
        """
        Runs after every tool round. Detects two loop patterns:
        1. The exact same tool call repeated back-to-back.
        2. Tool calls that keep hard-crashing, round after round.
        """
        messages = state.get("messages", [])
        triggering_ai, tool_msgs = _trailing_tool_round(messages)

        if not tool_msgs:
            return {}

        all_hard_errors = all(_is_hard_error(m.content) for m in tool_msgs)
        signature = _round_signature(triggering_ai)

        prev_signature = state.get("last_action_signature")
        prev_repeat = state.get("repeat_count", 0)
        prev_hard_errors = state.get("consecutive_hard_errors", 0)

        same_as_last_time = signature is not None and signature == prev_signature

        return {
            "last_action_signature": signature,
            "repeat_count": prev_repeat + 1 if same_as_last_time else 0,
            "consecutive_hard_errors": prev_hard_errors + 1 if all_hard_errors else 0,
        }

    def route_after_failure_check(state: AgentState):
        if state.get("repeat_count", 0) >= SAME_ACTION_REPEAT_LIMIT:
            return "give_up"
        if state.get("consecutive_hard_errors", 0) >= CONSECUTIVE_HARD_ERROR_LIMIT:
            return "give_up"
        return "agent"

    def give_up_node(state: AgentState):
        """
        Ends the run honestly instead of letting it grind on. Per the
        step-discipline rules every agent is prompted with, the model is
        never allowed to claim success it didn't earn -- this node holds
        the orchestrator to the same standard when *it* is the one
        stopping the run.
        """
        messages = state.get("messages", [])
        _, tool_msgs = _trailing_tool_round(messages)
        reason = _summarize_failure(tool_msgs)

        if state.get("repeat_count", 0) >= SAME_ACTION_REPEAT_LIMIT:
            why = "the same action failed repeatedly with no change in outcome"
        else:
            why = "the tool kept crashing on consecutive attempts"

        content = (
            "I couldn't complete this task, so I'm stopping instead of repeating "
            f"a step that isn't working. {why.capitalize()}. Last result: {reason}"
        )

        log.warning("agent.give_up why=%s reason=%s", why, reason)

        return {"messages": [AIMessage(content=content)]}

    def route_after_agent(state: AgentState):
        messages = state.get("messages", [])
        last = messages[-1] if messages else None

        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"

        # Empty-content stop with no tool call: nudge once instead of
        # silently ending the run with nothing to show for it.
        if isinstance(last, AIMessage) and not (last.content or "").strip():
            already_nudged = state.get("_empty_nudge_sent", False)
            if not already_nudged:
                return "nudge_empty"

        return END

    def nudge_empty_node(state: AgentState):
        empty_count = state.get("empty_count", 0) + 1

        if empty_count >= 2:
            return {
                "messages": [
                    AIMessage(content="Agent stopped: repeated empty responses.")
                ],
                "empty_count": empty_count,
            }
        return {
            "messages": [("human",
                "Your last response was empty. Either call a tool to continue, "
                "or write a short explanation of what happened and why you are stopping."
            )],
            "_empty_nudge_sent": True,
        }


    
    tool_node = ToolNode(tools, handle_tool_errors=True)

    graph_builder = StateGraph(AgentState)
    graph_builder.add_node("nudge_empty", nudge_empty_node)
    graph_builder.add_edge("nudge_empty", "agent")
    graph_builder.add_node("agent", agent_node)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("failure_check", failure_check_node)
    graph_builder.add_node("give_up", give_up_node)

    graph_builder.add_edge(START, "agent")
    graph_builder.add_conditional_edges("agent", route_after_agent)
    graph_builder.add_edge("tools", "failure_check")
    graph_builder.add_conditional_edges("failure_check", route_after_failure_check)
    graph_builder.add_edge("give_up", END)

    return graph_builder.compile()

# src/agents/react_agents.py — inside create_domain_agent, modify route_after_agent:




# =========================================================
# GENERAL AGENT
# =========================================================

def create_general_agent(llm, system_prompt: str, search_tools: list | None = None):

    tools = build_general_tools(search_tools)

    return create_domain_agent(
        llm=llm,
        tools=tools,
        system_prompt=system_prompt
    )


# =========================================================
# BROWSER AGENT
# =========================================================

def create_browser_agent(llm, mcp_tools, system_prompt: str):

    return create_domain_agent(
        llm=llm,
        tools=mcp_tools,
        system_prompt=system_prompt
    )

def create_router_agent(llm, system_prompt: str):

    return create_domain_agent(
        llm=llm,
        tools=[],
        system_prompt=system_prompt
    )



