from collections import deque

class ContextMemory:
    def __init__(self, max_items=10):
        # Stores recent interactions as (query, response) tuples
        self.history = deque(maxlen=max_items)
        
    def add_interaction(self, query, response):
        self.history.append((query, response))
        
    def get_context_string(self):
        if not self.history:
            return ""
            
        context = "Recent conversation history:\n"
        for i, (q, r) in enumerate(self.history):
            context += f"User: {q}\nAssistant: {r}\n"
        return context
        
    def clear(self):
        self.history.clear()
