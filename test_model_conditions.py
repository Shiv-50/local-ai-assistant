"""Debug: Test model with system prompt and many tools"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.tools.all_tools import STATIC_TOOLS
from langchain_core.messages import SystemMessage, HumanMessage

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

# Test 1: Direct invoke with bound tools and NO system prompt
print("=== Test 1: Direct with tools, NO system prompt ===")
model_with_tools = model.bind_tools(STATIC_TOOLS)
response1 = model_with_tools.invoke("Open Pinterest")
print(f"Tool calls: {response1.tool_calls[:1] if response1.tool_calls else 'None'}")

# Test 2: Direct invoke WITH system prompt
print("\n=== Test 2: Direct with tools AND system prompt ===")
system_msg = SystemMessage(content="You are a helpful assistant.")
human_msg = HumanMessage(content="Open Pinterest for me.")
response2 = model_with_tools.invoke([system_msg, human_msg])
print(f"Content: {response2.content}")
print(f"Tool calls: {response2.tool_calls}")

# Test 3: Direct invoke with many tools and system prompt (like agent does)
print("\n=== Test 3: With ALL 17 tools and system prompt ===")
from src.prompts.desktop_agent import system_prompt
system_msg = SystemMessage(content=system_prompt)
human_msg = HumanMessage(content="Open Pinterest for me.")
response3 = model_with_tools.invoke([system_msg, human_msg])
print(f"Content: {response3.content}")
print(f"Tool calls: {response3.tool_calls}")
