"""Test using create_react_agent with explicit tool binding - detailed debugging"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from langgraph.prebuilt import create_react_agent
from src.llm.llm_manager import llm_manager
from src.tools.system_tools import launch_application
from langchain_core.messages import SystemMessage

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

tools = [launch_application]

print("=== Test: Create agent with a single tool ===\n")

# Step 1: Check if model can call the tool directly
print("Step 1: Direct model test")
model_with_tools = model.bind_tools(tools)
response = model_with_tools.invoke("Open Pinterest")
print(f"  Tool calls: {response.tool_calls}")
if response.tool_calls:
    print("  ✅ Direct model tool calling WORKS")
else:
    print("  ❌ Direct model tool calling FAILED")

# Step 2: Create agent
print("\nStep 2: Create agent with create_react_agent")
agent = create_react_agent(
    model=model,
    tools=tools,
    prompt=SystemMessage(content="You are a helpful assistant.")
)
print(f"  Agent created: {agent}")

# Step 3: Invoke agent
print("\nStep 3: Invoke agent")
result = agent.invoke({"messages": [("human", "Open Pinterest for me.")]})
msg = result["messages"][-1]
print(f"  Content: {msg.content}")
print(f"  Tool calls: {msg.tool_calls if hasattr(msg, 'tool_calls') else 'N/A'}")
if msg.tool_calls:
    print("  ✅ Agent tool calling WORKS")
else:
    print("  ❌ Agent tool calling FAILED - model generated text instead")

# Step 4: Stream agent
print("\nStep 4: Stream agent")
count = 0
for chunk in agent.stream({"messages": [("human", "Open Pinterest for me.")]}):
    count += 1
    if isinstance(chunk, dict) and "messages" in chunk:
        msg = chunk["messages"][-1] if chunk["messages"] else None
        if msg and hasattr(msg, 'tool_calls') and msg.tool_calls:
            print(f"  Chunk {count}: Tool calls found!")
            print(f"    {msg.tool_calls}")
            break
    if count > 10:
        print(f"  No tool calls found in first {count} chunks")
        break
