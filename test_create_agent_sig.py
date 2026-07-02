from langchain.agents import create_agent
import inspect

sig = inspect.signature(create_agent)
print(f"Signature: create_agent{sig}")
print("\nDocstring:")
print(inspect.getdoc(create_agent))
