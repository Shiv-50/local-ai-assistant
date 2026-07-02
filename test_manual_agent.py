"""Try manually building an agent with LangGraph StateGraph"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from src.llm.llm_manager import llm_manager

# Simple test tool
@tool
def launch_application(app_name: str):
    """Launch an application"""
    return f"Launching {app_name}"

# Create model
model = llm_manager.get_model(
    model_name="qwen2.5:7b",
    temperature=0.2,
)

# Bind tools to model
tools = [launch_application]
model_with_tools = model.bind_tools(tools)

print("=== Manually Building Agent with StateGraph ===\n")

# Define the state
class AgentState:
    messages: list

# Define the agent node
def agent_node(state):
    messages = state.get("messages", [])
    response = model_with_tools.invoke(messages)
    return {"messages": messages + [response]}

# Define the tool execution node
tool_node = ToolNode(tools)

# Build the graph
graph_builder = StateGraph(dict)
graph_builder.add_node("agent", agent_node)
graph_builder.add_node("tools", tool_node)
graph_builder.add_edge(START, "agent")

def route_tools(state):
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        return "tools"
    return END

graph_builder.add_conditional_edges("agent", route_tools)
graph_builder.add_edge("tools", "agent")

agent = graph_builder.compile()

print("Agent created")
print("\nInvoking agent...")

result = agent.invoke({
    "messages": [HumanMessage(content="Open Pinterest for me.")]
})

messages = result.get("messages", [])
print(f"\nFinal messages count: {len(messages)}")
for i, msg in enumerate(messages):
    print(f"  {i}: {type(msg).__name__}")
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        print(f"      Tool calls: {msg.tool_calls}")
    else:
        print(f"      Content: {str(msg.content)[:80]}")

# Check if any tool calls were made (look for ToolMessage which is the result of a tool execution)
tool_calls_made = any(isinstance(msg, ToolMessage) for msg in messages)

if tool_calls_made:
    print("\n✅ Manual agent WORKS - tools were called!")
else:
    print("\n❌ Manual agent FAILED - tools were not called")
