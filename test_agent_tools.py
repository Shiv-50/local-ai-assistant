"""Test if agents are actually calling tools"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.agents.react_agents import create_general_agent
from src.prompts.desktop_agent import system_prompt
from src.tools.all_tools import STATIC_TOOLS

# Load models using venv
model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
    num_predict=1024,
)

print(f"Model: {model}")
print(f"Available tools: {len(STATIC_TOOLS)}")
for tool in STATIC_TOOLS[:3]:
    print(f"  - {tool.name}: {tool.description}")

# Create agent
agent = create_general_agent(
    llm=model,
    system_prompt=system_prompt,
    search_tools=None,
)

print(f"\nAgent: {agent}")
print(f"Agent type: {type(agent)}")

# Test invoke
test_input = {
    "messages": [("human", "Open Pinterest for me.")]
}

print("\n--- Invoking Agent ---")
result = agent.invoke(test_input)

print(f"\nResult type: {type(result)}")
print(f"Result keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")

if isinstance(result, dict) and "messages" in result:
    messages = result["messages"]
    print(f"Number of messages: {len(messages)}")
    for i, msg in enumerate(messages):
        print(f"\nMessage {i}: {type(msg).__name__}")
        print(f"  Content: {msg.content[:100] if msg.content else '(empty)'}")
        if hasattr(msg, 'tool_calls'):
            print(f"  Tool calls: {msg.tool_calls}")
