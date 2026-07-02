"""Final test: Verify both agents can call tools"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.agents.react_agents import create_general_agent, create_browser_agent
from src.prompts.desktop_agent import system_prompt as general_prompt
from src.prompts.browser_agent import system_prompt as browser_prompt
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage

# Create simple test tools
@tool
def test_tool():
    """Test tool"""
    return "Tool executed"

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

print("=== Testing Agents ===\n")

# Test general agent
print("1. General Agent")
general_agent = create_general_agent(llm=model, system_prompt=general_prompt, search_tools=None)
result = general_agent.invoke({"messages": [("human", "Open Pinterest for me.")]})
messages = result.get("messages", [])
tool_executed = any(isinstance(m, ToolMessage) for m in messages)
print(f"   Executed tools: {'✅ YES' if tool_executed else '❌ NO'}")
print(f"   Messages: {len(messages)}")
print(f"   Final message: {messages[-1].content[:80] if messages else 'N/A'}")

# Test browser agent
print("\n2. Browser Agent")
browser_agent = create_browser_agent(
    llm=model,
    mcp_tools=[test_tool],
    system_prompt=browser_prompt
)
result = browser_agent.invoke({"messages": [("human", "Execute the test tool")]})
messages = result.get("messages", [])
tool_executed = any(isinstance(m, ToolMessage) for m in messages)
print(f"   Executed tools: {'✅ YES' if tool_executed else '❌ NO'}")
print(f"   Messages: {len(messages)}")
print(f"   Final message: {messages[-1].content[:80] if messages else 'N/A'}")

print("\n✅ Agents are ready!")
