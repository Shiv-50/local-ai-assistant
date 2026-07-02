"""Debug: Check create_react_agent behavior"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from langgraph.prebuilt import create_react_agent
from src.llm.llm_manager import llm_manager
from src.tools.all_tools import STATIC_TOOLS
from langchain_core.messages import SystemMessage

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

# Try without binding tools
print("=== Test 1: Pass model WITHOUT bound tools ===")
agent1 = create_react_agent(
    model=model,
    tools=STATIC_TOOLS,
    prompt=SystemMessage(content="You are a helpful assistant.")
)

result1 = agent1.invoke({"messages": [("human", "Open Pinterest for me.")]})
msg1 = result1["messages"][-1]
print(f"Tool calls: {msg1.tool_calls if hasattr(msg1, 'tool_calls') else 'N/A'}")

# Try WITH binding tools
print("\n=== Test 2: Pass model WITH bound tools ===")
model_with_tools = model.bind_tools(STATIC_TOOLS)
agent2 = create_react_agent(
    model=model_with_tools,
    tools=STATIC_TOOLS,
    prompt=SystemMessage(content="You are a helpful assistant.")
)

result2 = agent2.invoke({"messages": [("human", "Open Pinterest for me.")]})
msg2 = result2["messages"][-1]
print(f"Tool calls: {msg2.tool_calls if hasattr(msg2, 'tool_calls') else 'N/A'}")

# Try with empty tools list
print("\n=== Test 3: Pass model WITH bound tools, but tools=[] ===")
agent3 = create_react_agent(
    model=model_with_tools,
    tools=[],
    prompt=SystemMessage(content="You are a helpful assistant.")
)

result3 = agent3.invoke({"messages": [("human", "Open Pinterest for me.")]})
msg3 = result3["messages"][-1]
print(f"Tool calls: {msg3.tool_calls if hasattr(msg3, 'tool_calls') else 'N/A'}")
print(f"Content: {msg3.content}")
