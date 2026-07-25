import json
import logging
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from src.tools.all_tools import build_general_tools
from src.prompts.replan_prompt import replan_prompt
from src.prompts.output_prompt import output_prompt

log = logging.getLogger(__name__)


_HARD_ERROR_PREFIX = "Error executing tool"


SAME_ACTION_REPEAT_LIMIT = 2

# Stop after this many consecutive rounds where every tool call in the
# round hard-errored (crashed), even if the calls themselves varied.
CONSECUTIVE_HARD_ERROR_LIMIT = 3

# How many rounds of action signatures to keep for cycle detection.
ACTION_HISTORY_WINDOW = 4

# Stop after this many detected A, B, A, B, ... alternations.
CYCLE_REPEAT_LIMIT = 2

# =========================================================
# STRUCTURED OUTCOME DETECTION (for the outer orchestrator)
# =========================================================
#
# The outer orchestrator (src/orchestrator/orchestrator.py) is now a
# state machine that branches on what happened to a task: did it
# succeed, does it need a different plan/agent (replan), or did it
# fail outright. Previously the only signal available was a free-text
# final message, which the orchestrator would have had to string-sniff
# to make routing decisions -- fragile and impossible to reason about.
#
# Every domain agent built with create_domain_agent() now ends its run
# through `finalize_node` (normal completion) or `give_up_node` (circuit
# breaker tripped), both of which set state["status"] to one of:
#   "success" - task was completed (or at least attempted normally)
#   "replan"  - the agent believes this task doesn't belong to it / is
#               not executable as specified, and a different plan or
#               agent assignment is needed
#   "failed"  - the agent got stuck (repeated identical failures, or
#               repeated hard crashes) and gave up
#
# Replan detection here is a cheap heuristic on purpose (see the
# orchestrator docstring for why): if the agent never called a single
# tool AND its final message reads like a "this isn't something I can
# do" refusal, treat it as a replan signal rather than a hard failure.
# A missed detection just falls through to "success" and the
# orchestrator treats the (possibly unhelpful) text as the answer --
# safe default, not a crash.

REPLAN_SIGNAL_PATTERNS = (
    "not something i can do",
    "not something i'm able to do",
    "outside my scope",
    "outside of my scope",
    "cannot perform this",
    "can't perform this",
    "not able to complete this",
    "isn't something i can do",
    "wrong agent",
    "not the right agent",
    "i don't have the tools",
    "i do not have the tools",
    "not equipped to",
    "this isn't a task for me",
    "not a task i can handle",
    "not within my capabilities",
)


def _looks_like_replan(content: str) -> bool:
    text = (content or "").lower()
    return any(pattern in text for pattern in REPLAN_SIGNAL_PATTERNS)


def _has_any_tool_message(messages: list[BaseMessage]) -> bool:
    return any(isinstance(m, ToolMessage) for m in messages)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    last_action_signature: Any
    repeat_count: int
    consecutive_hard_errors: int
    action_history: list
    cycle_repeat_count: int
    empty_count: int
    status: str            # "success" | "replan" | "failed"
    status_reason: str

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

# src/agents/react_agents.py

def create_domain_agent(llm, tools, system_prompt: str, state_provider=None):

    model_with_tools = llm.bind_tools(tools)

    def agent_node(state: AgentState):
        messages = state.get("messages", [])
        prompt = system_prompt + "\n\n" + replan_prompt + "\n\n" + output_prompt
        if state_provider:
            try:
                prompt = f"{prompt}\n\n{state_provider()}"
            except Exception:
                log.exception("state_provider failed")
        system_msg = SystemMessage(content=prompt)
        response = model_with_tools.invoke([system_msg] + list(messages))
        return {"messages": [response]}

    def failure_check_node(state: AgentState):
        """
        Runs after every tool round. Detects three loop patterns:
        1. The exact same tool call repeated back-to-back (A, A).
        2. Tool calls that keep hard-crashing, round after round.
        3. A short alternating cycle (A, B, A, B, ...) that never shows
           up as an immediate repeat but is just as stuck.
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
        prev_cycle_repeat = state.get("cycle_repeat_count", 0)
        history = list(state.get("action_history", []))

        same_as_last_time = signature is not None and signature == prev_signature

        # Period-2 cycle: two rounds back was this exact same action,
        # but it's NOT an immediate repeat (that's already covered by
        # same_as_last_time above) -- i.e. A, B, A.
        cycle_detected = (
            not same_as_last_time
            and signature is not None
            and len(history) >= 2
            and history[-2] == signature
        )

        history.append(signature)
        history = history[-ACTION_HISTORY_WINDOW:]

        return {
            "last_action_signature": signature,
            "repeat_count": prev_repeat + 1 if same_as_last_time else 0,
            "consecutive_hard_errors": prev_hard_errors + 1 if all_hard_errors else 0,
            "cycle_repeat_count": prev_cycle_repeat + 1 if cycle_detected else 0,
            "action_history": history,
        }

    def route_after_failure_check(state: AgentState):
        if state.get("repeat_count", 0) >= SAME_ACTION_REPEAT_LIMIT:
            return "give_up"
        if state.get("consecutive_hard_errors", 0) >= CONSECUTIVE_HARD_ERROR_LIMIT:
            return "give_up"
        if state.get("cycle_repeat_count", 0) >= CYCLE_REPEAT_LIMIT:
            return "give_up"

        # One corrective nudge per streak, right before it would trip the
        # limit above. repeat_count/cycle_repeat_count only ever equal 1
        # on the single round right after the pattern first emerges, so
        # this fires exactly once per streak -- not on every round.
        if state.get("repeat_count", 0) == 1:
            return "retry_nudge"
        if state.get("cycle_repeat_count", 0) == 1:
            return "retry_nudge"

        return "agent"

    def retry_nudge_node(state: AgentState):
        """
        Gives the model one chance to self-correct before the circuit
        breaker gives up: names exactly what failed (or what pattern
        it's stuck in) and asks for a different action, instead of
        either silently retrying it again or killing the run outright.
        """
        messages = state.get("messages", [])
        _, tool_msgs = _trailing_tool_round(messages)
        reason = _summarize_failure(tool_msgs)

        if state.get("cycle_repeat_count", 0) == 1:
            nudge_text = (
                "You appear to be alternating between the same actions "
                f"without making progress (last result: {reason}). Stop "
                "repeating this pattern -- either try a genuinely "
                "different action, verify the current state first, or "
                "explain why you cannot proceed."
            )
        else:
            nudge_text = (
                f"That exact action just failed or made no progress "
                f"(last result: {reason}). Do not repeat it unchanged -- "
                "try a different action, verify the current state first, "
                "or explain why you cannot proceed if nothing else will "
                "work."
            )

        log.info(
            "agent.retry_nudge repeat_count=%s cycle_repeat_count=%s reason=%s",
            state.get("repeat_count", 0),
            state.get("cycle_repeat_count", 0),
            reason,
        )

        return {"messages": [("human", nudge_text)]}

    def give_up_node(state: AgentState):
        """
        Ends the run honestly instead of letting it grind on. Per the
        step-discipline rules every agent is prompted with, the model is
        never allowed to claim success it didn't earn -- this node holds
        the orchestrator to the same standard when *it* is the one
        stopping the run.

        Sets status="failed" so the outer orchestrator's state machine
        can route this task to its own give-up handling rather than
        treating the explanation text as a successful result.
        """
        messages = state.get("messages", [])
        _, tool_msgs = _trailing_tool_round(messages)
        reason = _summarize_failure(tool_msgs)

        if state.get("repeat_count", 0) >= SAME_ACTION_REPEAT_LIMIT:
            why = "the same action failed repeatedly with no change in outcome"
        elif state.get("cycle_repeat_count", 0) >= CYCLE_REPEAT_LIMIT:
            why = "the agent kept alternating between the same actions without progress"
        else:
            why = "the tool kept crashing on consecutive attempts"

        content = (
            "I couldn't complete this task, so I'm stopping instead of repeating "
            f"a step that isn't working. {why.capitalize()}. Last result: {reason}"
        )

        log.warning("agent.give_up why=%s reason=%s", why, reason)

        return {
            "messages": [AIMessage(content=content)],
            "status": "failed",
            "status_reason": f"{why}. Last result: {reason}",
        }

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

        return "finalize"

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

    def finalize_node(state: AgentState):
        """
        Runs on normal (non-circuit-breaker) completion. Classifies the
        outcome as "success" or "replan":

        - "replan": the agent never called a single tool AND its final
          message reads like a refusal/scope mismatch (see
          REPLAN_SIGNAL_PATTERNS). Signals to the outer orchestrator
          that this task needs a different plan or agent, not that it
          crashed.
        - "success": everything else -- normal completion, including
          cases where tools were used. This is the safe default: a
          missed replan detection just means the text is passed through
          as the answer, exactly as before this change.
        """
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        content = getattr(last, "content", "") if last is not None else ""

        if not _has_any_tool_message(messages) and _looks_like_replan(content):
            log.info("agent.finalize.replan_detected preview=%s", str(content)[:200])
            return {
                "status": "replan",
                "status_reason": (content or "").strip()[:300],
            }

        return {"status": "success", "status_reason": ""}

    tool_node = ToolNode(tools, handle_tool_errors=True)

    graph_builder = StateGraph(AgentState)
    graph_builder.add_node("nudge_empty", nudge_empty_node)
    graph_builder.add_edge("nudge_empty", "agent")
    graph_builder.add_node("agent", agent_node)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("failure_check", failure_check_node)
    graph_builder.add_node("retry_nudge", retry_nudge_node)
    graph_builder.add_node("give_up", give_up_node)
    graph_builder.add_node("finalize", finalize_node)

    graph_builder.add_edge(START, "agent")
    graph_builder.add_conditional_edges("agent", route_after_agent)
    graph_builder.add_edge("tools", "failure_check")
    graph_builder.add_conditional_edges("failure_check", route_after_failure_check)
    graph_builder.add_edge("retry_nudge", "agent")
    graph_builder.add_edge("give_up", END)
    graph_builder.add_edge("finalize", END)

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

def create_browser_agent(llm, mcp_tools, system_prompt: str, state_provider=None):

    return create_domain_agent(
        llm=llm,
        tools=mcp_tools,
        system_prompt=system_prompt,
        state_provider=state_provider
    )

def create_router_agent(llm, system_prompt: str):

    return create_domain_agent(
        llm=llm,
        tools=[],
        system_prompt=system_prompt
    )