import logging
import asyncio
from langchain_core.messages import HumanMessage

class SimpleRouterOrchestrator:
    """
    Replaces the complex AgentGraphBuilder.
    A simple router that selects between general desktop and browser ReAct agents,
    and invokes them using LangGraph's native tool-calling loop.
    """

    def __init__(self, general_agent, browser_agent):
        self.general_agent = general_agent
        self.browser_agent = browser_agent

    def invoke(self, state: dict):
        query = state.get("user_goal", "")
        history = state.get("conversation_history", [])
        
        logging.info(f"[ROUTER] User Query: {query}")
        
        # Simple keyword based routing
        browser_keywords = ["website", "browser", "url", "http", "www", "open page", "navigate", "login", "github.com"]
        
        is_browser = any(kw in query.lower() for kw in browser_keywords)
        
        agent = self.browser_agent if is_browser else self.general_agent
        agent_type = "BROWSER" if is_browser else "GENERAL"
        
        logging.info(f"[ROUTER] Selected Agent: {agent_type}")
        
        # Construct message list for LangGraph ReAct agent
        messages = []
        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                messages.append(("human", content))
            else:
                messages.append(("ai", content))
                
        # Ensure the last message is a HumanMessage
        if not messages or messages[-1][0] != "human":
            messages.append(("human", query))
            
        try:
            # Invoke the ReAct agent asynchronously to support async MCP tools
            result_state = asyncio.run(agent.ainvoke({"messages": messages}))
            
            # The last message is the agent's final text output
            final_message = result_state["messages"][-1].content
            
            logging.info(f"[ROUTER] Agent finished with final message length: {len(final_message)}")
            
            return {
                "response": final_message
            }
            
        except Exception as e:
            logging.exception("[ROUTER] Agent execution failed")
            return {
                "response": f"System encountered an error during execution: {str(e)}"
            }