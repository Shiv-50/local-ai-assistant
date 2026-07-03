from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.tools.all_tools import build_general_tools


# =========================================================
# CORE AGENT BUILDER
# =========================================================

def create_domain_agent(llm, tools, system_prompt: str):

    model_with_tools = llm.bind_tools(tools)

    def agent_node(state):
        messages = state.get("messages", [])

        converted_messages = []

        for msg in messages:
            if isinstance(msg, tuple):
                role, content = msg
                if role == "human":
                    converted_messages.append(HumanMessage(content=content))
                else:
                    converted_messages.append(AIMessage(content=content))

            elif isinstance(msg, (HumanMessage, AIMessage, ToolMessage, SystemMessage)):
                converted_messages.append(msg)

            else:
                converted_messages.append(HumanMessage(content=str(msg)))

        system_msg = SystemMessage(content=system_prompt)
        augmented_messages = [system_msg] + converted_messages

        # IMPORTANT: use closure variable correctly
        response = model_with_tools.invoke(augmented_messages)

        return {
            "messages": converted_messages + [response]
        }

    tool_node = ToolNode(tools)

    graph_builder = StateGraph(dict)
    graph_builder.add_node("agent", agent_node)
    graph_builder.add_node("tools", tool_node)

    graph_builder.add_edge(START, "agent")

    def route_after_agent(state):
        messages = state.get("messages", [])
        last = messages[-1] if messages else None

        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph_builder.add_conditional_edges("agent", route_after_agent)
    graph_builder.add_edge("tools", "agent")

    return graph_builder.compile()
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