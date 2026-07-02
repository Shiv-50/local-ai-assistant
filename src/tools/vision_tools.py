import os
import base64
import json
import logging
import re
import time

import pyautogui
import requests
from PIL import Image

from langchain_google_genai import ChatGoogleGenerativeAI

from src.tools.base import safe_tool

logger = logging.getLogger(__name__)

# =========================================================
# VISION PROMPT
# =========================================================

VISION_PROMPT = """
Analyze this screenshot and return ONLY valid JSON. No markdown, no explanation.

Return this exact structure:

{{
  "screen_summary": "one sentence describing what is on screen",
  "elements": [
    {{
      "type": "button | input | text | icon | link | image | unknown",
      "text": "visible label or placeholder",
      "x": 0,
      "y": 0,
      "width": 0,
      "height": 0,
      "confidence": 0.0
    }}
  ]
}}

Focus on: {query}

Rules:
- x, y are the CENTER pixel coordinates of the element
- Include ALL interactive elements (buttons, inputs, links, icons)
- confidence is 0.0 to 1.0
- Return ONLY the JSON object
""".strip()

# =========================================================
# IMAGE ENCODING
# =========================================================

def _encode_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def _encode_image_data_uri(path: str) -> str:
    b64 = _encode_image_b64(path)
    return f"data:image/png;base64,{b64}"

# =========================================================
# JSON EXTRACTION
# =========================================================

def _extract_json(text: str) -> dict | None:
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object from surrounding text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None

# =========================================================
# GEMMA (GOOGLE API)
# =========================================================

def _query_gemma_vision(image_path: str, query: str) -> dict:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Google API key not set")

    data_uri = _encode_image_data_uri(image_path)
    prompt = VISION_PROMPT.replace("{query}", query)

    llm = ChatGoogleGenerativeAI(
        model="gemma-4-31b-it",
        temperature=0.1,
        google_api_key=api_key,
    )

    logger.info("Using Gemma vision (Google API)...")

    response = llm.invoke([
        ("human", [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
    ])

    # response.content is a list of blocks when thinking is enabled:
    # [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}]
    # Extract only the text block.
    content = response.content

    if isinstance(content, list):
        text = next(
            (block["text"] for block in content if block.get("type") == "text"),
            None,
        )
        if text is None:
            raise ValueError(f"No text block in Gemma response: {content}")
    else:
        text = str(content)

    result = _extract_json(text)
    if not result:
        raise ValueError(f"Gemma returned non-JSON text block: {text[:200]}")

    return result

# =========================================================
# LOCAL FALLBACK (OLLAMA)
# =========================================================

def _query_ollama_vision(image_path: str, query: str, retries: int = 2) -> dict:
    b64 = _encode_image_b64(image_path)
    prompt = VISION_PROMPT.format(query=query)

    payload = {
        "model": "llama3.2-vision",
        "prompt": prompt,
        "images": [b64],
        "stream": False,
    }

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            logger.info("Using local llama3.2-vision (attempt %d/%d)...", attempt, retries)

            res = requests.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=120,
            )
            res.raise_for_status()

            raw = res.json().get("response", "")
            result = _extract_json(raw)

            if not result:
                raise ValueError(f"Ollama returned non-JSON: {raw[:200]}")

            return result

        except Exception as e:
            last_error = e
            logger.warning("Ollama vision attempt %d failed: %s", attempt, e)
            if attempt < retries:
                time.sleep(1)

    raise RuntimeError(f"Ollama vision failed after {retries} attempts: {last_error}")

# =========================================================
# GEMMA FAILURE TRACKER
# =========================================================

class _GemmaState:
    failed = False
    fail_count = 0
    MAX_CONSECUTIVE_FAILS = 3

    @classmethod
    def record_failure(cls):
        cls.fail_count += 1
        if cls.fail_count >= cls.MAX_CONSECUTIVE_FAILS:
            cls.failed = True
            logger.warning(
                "Gemma disabled after %d consecutive failures", cls.MAX_CONSECUTIVE_FAILS
            )

    @classmethod
    def record_success(cls):
        cls.fail_count = 0
        cls.failed = False

# =========================================================
# MAIN TOOL
# =========================================================

@safe_tool("Analyze screen")
def analyze_screen_with_vision(query: str):
    """
    Captures the screen and returns structured JSON with UI element coordinates.
    Primary: local llama3.2-vision (Ollama) - fast and local.
    Fallback: Google Gemma - slower but more capable.
    """
    img_path = "temp_screen.png"

    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(img_path)
        logger.info("Screenshot captured: %s", img_path)

        result = None

        # -------------------------
        # PRIMARY: OLLAMA (FAST, LOCAL)
        # -------------------------
        try:
            result = _query_ollama_vision(img_path, query)
            logger.info("Vision analysis completed with local Ollama model (fast)")
        except Exception as e:
            logger.warning("Ollama vision failed: %s", e)

        # -------------------------
        # FALLBACK: GEMMA (SLOWER, API-BASED)
        # -------------------------
        if result is None and not _GemmaState.failed:
            try:
                logger.info("Falling back to Google Gemma API for vision...")
                result = _query_gemma_vision(img_path, query)
                _GemmaState.record_success()
            except Exception as e:
                logger.warning("Gemma vision also failed: %s", e)
                _GemmaState.record_failure()

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error("Vision tool failed: %s", e, exc_info=True)
        return f"Vision error: {e}"

    finally:
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass