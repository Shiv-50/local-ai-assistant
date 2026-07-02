"""Test agents with streaming (like orchestrator does)"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

import asyncio
from src.llm.llm_manager import llm_manager
from src.agents.react_agents import create_general_agent
from src.prompts.desktop_agent import system_prompt
from src.tools.all_tools import STATIC_TOOLS

async def test_agent_streaming():
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

    print("--- Streaming Agent Output ---\n")
    
    async for chunk in agent.astream(test_input, stream_mode="values"):
        messages = chunk.get("messages", [])
        if messages:
            latest = messages[-1]
            print(f"Message: {type(latest).__name__}")
            if hasattr(latest, 'content') and latest.content:
                print(f"  Content: {latest.content[:100]}")
            if hasattr(latest, 'tool_calls') and latest.tool_calls:
                print(f"  Tool calls: {latest.tool_calls}")
            print()

if __name__ == "__main__":
    asyncio.run(test_agent_streaming())
