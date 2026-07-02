"""
Diagnostic: does Ollama actually do native tool-calling for this model?

Run directly (no LangChain involved) to isolate whether the problem is
Ollama/the model itself, or the langchain_ollama binding layer:

    python test_tool_calling.py

Look at the printed response. You want to see a `tool_calls` key with a
real entry in `message`. If instead the model's plain `content` contains
text like `launch_application({"app_name": "Pinterest"})`, that confirms
the model is not using Ollama's function-calling mechanism at all — it's
just imitating the syntax as text, which is exactly the bug we saw.
"""

import json
import requests

OLLAMA_BASE = "http://127.0.0.1:11434"
MODEL = "qwen2.5:7b"   # match whatever your build_models() uses for "agent"

tools = [
    {
        "type": "function",
        "function": {
            "name": "launch_application",
            "description": "Launch or focus a desktop application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the app to launch"},
                },
                "required": ["app_name"],
            },
        },
    }
]

payload = {
    "model": MODEL,
    "messages": [
        {"role": "user", "content": "Open Pinterest for me."},
    ],
    "tools": tools,
    "stream": False,
}

resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=60)
resp.raise_for_status()
data = resp.json()

print(json.dumps(data, indent=2))

message = data.get("message", {})
if message.get("tool_calls"):
    print("\n✅ Ollama returned structured tool_calls — native tool calling IS working.")
    print("   -> The bug is likely elsewhere (e.g. langchain_ollama version/binding).")
else:
    print("\n❌ No tool_calls in the response — Ollama/this model did NOT do real tool calling.")
    print("   The model just wrote plain text instead. Check:")
    print("   1. `ollama --version` — need a reasonably recent version with tool support.")
    print("   2. `ollama show qwen2.5:7b --modelfile` — confirm the template supports tools")
    print("      (look for {{ .Tools }} / tool_call handling in the TEMPLATE section).")
    print("   3. Try a model documented to support tools reliably in Ollama, e.g. qwen2.5:7b-instruct,")
    print("      llama3.1:8b, or mistral-nemo, and re-run this script against it.")
