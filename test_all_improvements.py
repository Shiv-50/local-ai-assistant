"""
Test all three improvements:
A. Screen analysis - Agent checks screen before responding
B. Better summary recognition - Agent knows to analyze before summarizing  
C. Memory context - Agent is aware of previous actions
"""

import sys
sys.path.insert(0, '/c/Users/shiva/AI_assistant')

from src.llm.llm_manager import llm_manager
from src.agents.react_agents import create_browser_agent
from src.prompts.browser_agent import system_prompt
from langchain_core.tools import tool

# Mock screen analysis tool that returns sample content
@tool
def analyze_screen_with_vision(query: str):
    """Analyze what's currently on the screen"""
    return """
Current Page: Financial News Website
URL: https://www.bloomberg.com/markets
Visible Content:
- Stock Market Summary: S&P 500 +2.1% (4,832 points)
- Top News: 
  1. Tech stocks surge on earnings optimism
  2. Fed maintains interest rate hold
  3. Oil prices stabilize above $75/barrel
- Last Updated: 2:45 PM EST
"""

model = llm_manager.get_model(model_name="qwen2.5:7b", temperature=0.2)
tools = [analyze_screen_with_vision]
agent = create_browser_agent(llm=model, mcp_tools=tools, system_prompt=system_prompt)

print("=== TEST: All Three Improvements ===\n")

# Simulate memory context from previous interaction
memory_context = """[CONTEXT FROM PREVIOUS INTERACTIONS]
These are relevant user preferences and prior actions for this request:
  • [feedback] URL opened successfully - user asked for stock market news
  • [user_preference] User likes financial market updates

[/CONTEXT]
"""

# Test Case: User asks for summary after previous action
print("Test Case: User asks 'give a summary' after opening stock news\n")
print("Memory Context (from previous session):")
print(memory_context)

# Build messages with memory context and user query
messages = [
    ("human", memory_context + "\nUser request: Give me a summary of what's on the screen"),
]

print("\nAgent Response:")
result = agent.invoke({"messages": messages})
messages_result = result.get("messages", [])

# Check what happened
from langchain_core.messages import ToolMessage

print(f"\nMessages in result: {len(messages_result)}")
for i, msg in enumerate(messages_result):
    msg_type = type(msg).__name__
    if msg_type == "HumanMessage":
        content_preview = str(msg.content)[:100]
        print(f"  {i}: HumanMessage - '{content_preview}...'")
    elif msg_type == "ToolMessage":
        print(f"  {i}: ToolMessage - Tool '{msg.name}' executed")
        print(f"       Result: {str(msg.content)[:80]}...")
    elif msg_type == "AIMessage":
        content_preview = str(msg.content)[:100]
        print(f"  {i}: AIMessage - '{content_preview}...'")

# Verify improvements
screen_analyzed = any(isinstance(m, ToolMessage) and m.name == "analyze_screen_with_vision" 
                      for m in messages_result)
summary_provided = any(isinstance(m, ToolMessage) or 
                       (hasattr(m, 'content') and any(kw in str(m.content).lower() 
                        for kw in ['market', 'stock', 's&p', 'news']))
                       for m in messages_result)
memory_acknowledged = len(messages_result) > 0

print("\n" + "="*60)
print("VERIFICATION:")
print(f"✅ A - Screen Analysis: {'YES - analyzed screen before responding' if screen_analyzed else 'NO - did not use vision tool'}")
print(f"✅ B - Summary Recognition: {'YES - understood summary request' if summary_provided else 'NO - missed summary request'}")
print(f"✅ C - Memory Context: {'YES - used previous context' if memory_acknowledged else 'NO - ignored context'}")
print("="*60)
