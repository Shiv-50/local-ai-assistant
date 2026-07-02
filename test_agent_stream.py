"""Test agents with streaming to see full execution"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.agents.react_agents import create_general_agent
from src.prompts.desktop_agent import system_prompt

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
    num_predict=1024,
)

agent = create_general_agent(
    llm=model,
    system_prompt=system_prompt,
    search_tools=None,
)

test_input = {
    "messages": [("human", "Open Pinterest for me.")]
}

print("--- Streaming Agent with Full Execution ---\n")

for step, chunk in enumerate(agent.stream(test_input)):
    print(f"Step {step}: {chunk}")
    if isinstance(chunk, dict) and "messages" in chunk:
        messages = chunk["messages"]
        if messages:
            msg = messages[-1]
            print(f"  Latest message type: {type(msg).__name__}")
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                print(f"  Tool calls: {msg.tool_calls}")
            elif hasattr(msg, 'content'):
                print(f"  Content: {str(msg.content)[:80]}")
    print()
