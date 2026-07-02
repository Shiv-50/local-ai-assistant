from langchain_ollama import ChatOllama
from langchain_core.tools import tool

@tool
def launch_application(app_name: str):
    """Launch an application"""
    return f"Launching {app_name}"


llm = ChatOllama(
    model="qwen2.5:7b",
    temperature=0
)

llm_with_tools = llm.bind_tools([launch_application])

response = llm_with_tools.invoke(
    "Open Pinterest"
)

print("\nCONTENT:")
print(response.content)

print("\nTOOL CALLS:")
print(response.tool_calls)

print("\nRAW:")
print(response)