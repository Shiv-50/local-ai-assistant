"""
Verify that required Ollama models are available for vision analysis.
Run this to check if llama3.2-vision is installed.
"""

import requests
import json

OLLAMA_BASE = "http://127.0.0.1:11434"

print("=" * 70)
print("CHECKING OLLAMA MODELS")
print("=" * 70)

try:
    # Get list of installed models
    response = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
    response.raise_for_status()
    data = response.json()
    
    models = data.get("models", [])
    model_names = [m.get("name", "") for m in models]
    
    print(f"\nInstalled Ollama models ({len(model_names)}):")
    for name in sorted(model_names):
        print(f"  ✓ {name}")
    
    # Check for vision model
    vision_models_needed = ["llama3.2-vision"]
    vision_available = any(vm in str(model_names) for vm in vision_models_needed)
    
    print(f"\n{'='*70}")
    if vision_available:
        print("✅ VISION MODEL AVAILABLE: llama3.2-vision found")
        print("   Screen analysis will be FAST (local, no API calls)")
    else:
        print("⚠️  VISION MODEL MISSING: llama3.2-vision not installed")
        print("   To install: ollama pull llama3.2-vision")
        print("   Without it, will fall back to Google Gemma API (slow)")
        print("   With it, screen analysis will be ~30-40 seconds instead of 100+ seconds")
    
    print("=" * 70)
    
except requests.exceptions.ConnectionError:
    print("❌ ERROR: Cannot connect to Ollama at", OLLAMA_BASE)
    print("   Make sure Ollama is running: ollama serve")
except Exception as e:
    print(f"❌ ERROR: {e}")
