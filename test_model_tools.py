"""Test if model can make tool calls directly with tools bound"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.tools.all_tools import STATIC_TOOLS

model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

print(f"Model: {model}")
print(f"Number of tools: {len(STATIC_TOOLS)}")

# Bind tools to model
model_with_tools = model.bind_tools(STATIC_TOOLS)

print(f"\nModel with tools bound:")

# Invoke with a prompt
response = model_with_tools.invoke("Open Pinterest for me.")

print(f"\nResponse type: {type(response).__name__}")
print(f"Content: {response.content}")
print(f"Tool calls: {response.tool_calls}")

if response.tool_calls:
    print("\n✅ Tools ARE being called by the model when bound directly")
else:
    print("\n❌ Tools are NOT being called even with bind_tools()")
