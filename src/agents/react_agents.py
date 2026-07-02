# src/agents/react_agents.py

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage, SystemMessage
from src.tools.all_tools import build_general_tools


def create_domain_agent(llm, tools, system_prompt: str):
    """
    Creates a LangChain agent with the specified LLM, tools, and system prompt.
    
    Manually builds the agent graph to ensure proper tool calling:
    - Agent node: Calls the model with bound tools
    - Tool node: Executes any tool calls the model makes
    - Routing: Continues looping if tools were called, returns if not
    
    This avoids the issues with create_react_agent/create_agent where tool
    calling wasn't being triggered properly with local models like Ollama.
    """
    # Bind tools to the model so it knows to call them
    model_with_tools = llm.bind_tools(tools)
    
    # Define the agent node
    def agent_node(state):
        messages = state.get("messages", [])
        
        # Convert tuples to proper Message objects if needed
        converted_messages = []
        for msg in messages:
            if isinstance(msg, tuple):
                role, content = msg
                if role == "human":
                    converted_messages.append(HumanMessage(content=content))
                elif role == "ai":
                    converted_messages.append(AIMessage(content=content))
                else:
                    converted_messages.append(HumanMessage(content=content))
            else:
                converted_messages.append(msg)
        
        # Prepend system prompt as a system message
        system_msg = SystemMessage(content=system_prompt)
        augmented_messages = [system_msg] + converted_messages
        
        # Call the model
        response = model_with_tools.invoke(augmented_messages)
        return {"messages": converted_messages + [response]}
    
    # Create tool node for executing tools
    tool_node = ToolNode(tools)
    
    # Build the graph
    graph_builder = StateGraph(dict)
    graph_builder.add_node("agent", agent_node)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_edge(START, "agent")
    
    # Route: if agent made tool calls, execute tools; otherwise end
    def route_after_agent(state):
        messages = state.get("messages", [])
        last_message = messages[-1] if messages else None
        if isinstance(last_message, AIMessage) and getattr(last_message, 'tool_calls', None):
            return "tools"
        return END
    
    graph_builder.add_conditional_edges("agent", route_after_agent)
    graph_builder.add_edge("tools", "agent")
    
    return graph_builder.compile()


def create_general_agent(llm, system_prompt: str, search_tools: list | None = None):
    """
    Creates a general purpose agent using the static GENERAL_TOOLS plus
    any MCP-provided search tools (e.g. Tavily) loaded by mcp_manager.
    """
    return create_domain_agent(llm, build_general_tools(search_tools), system_prompt)


def create_browser_agent(llm, mcp_tools, system_prompt: str):
    """
    Creates a browser specific agent using Playwright MCP tools.
    """
    return create_domain_agent(llm, mcp_tools, system_prompt)

def create_general_agent(llm, system_prompt: str, search_tools: list | None = None):
    """
    Creates a general purpose ReAct agent using the static GENERAL_TOOLS plus
    any MCP-provided search tools (e.g. Tavily) loaded by mcp_manager.
    """
    return create_domain_agent(llm, build_general_tools(search_tools), system_prompt)

def create_browser_agent(llm, mcp_tools, system_prompt: str):
    """
    Creates a browser specific ReAct agent using Playwright MCP tools.
    """
    return create_domain_agent(llm, mcp_tools, system_prompt)
