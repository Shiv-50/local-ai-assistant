# src/agents/react_agents.py

from langgraph.prebuilt import create_react_agent
from src.tools.all_tools import build_general_tools
from langchain_core.messages import SystemMessage

def create_domain_agent(llm, tools, system_prompt: str):
    """
    Creates a LangChain ReAct agent with the specified LLM, tools, and system prompt.
    Uses LangGraph's create_react_agent under the hood.
    """
    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=SystemMessage(content=system_prompt)
    )

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
