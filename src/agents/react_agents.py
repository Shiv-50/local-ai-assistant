# src/agents/react_agents.py

from langgraph.prebuilt import create_react_agent
from src.tools.all_tools import GENERAL_TOOLS
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

def create_general_agent(llm, system_prompt: str):
    """
    Creates a general purpose ReAct agent using the standard GENERAL_TOOLS list.
    """
    return create_domain_agent(llm, GENERAL_TOOLS, system_prompt)

def create_browser_agent(llm, mcp_tools, system_prompt: str):
    """
    Creates a browser specific ReAct agent using Playwright MCP tools.
    """
    return create_domain_agent(llm, mcp_tools, system_prompt)
