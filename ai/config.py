# config.py
# Mapping of task categories to corresponding agent classes
# Used by the orchestrator to delegate queries to specialized agents.

AGENT_REGISTRY = {
    "web": "ai.agents.web_search_agent.WebSearchAgent",
    "file": "ai.agents.file_manager_agent.FileManagerAgent",
    "memory": "ai.agents.memory_agent.MemoryAgent",
    "system": "ai.agents.system_agent.SystemAgent",
    "tool": "ai.agents.tool_exec_agent.ToolExecAgent",
}

# Helper to dynamically import a class from a dotted path
def import_agent(class_path: str):
    module_path, class_name = class_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)
