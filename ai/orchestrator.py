# orchestrator.py

import json
import logging
import re
import time

from datetime import datetime
from json import JSONDecoder
from typing import List, Tuple, Optional

from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    AIMessage,
    ToolMessage,
)

from langgraph.prebuilt import create_react_agent
from langgraph.errors import GraphRecursionError

from ai.tools import ALL_TOOLS
from ai.llm_manager import LLMManager
from ai.memory_store import add_memory

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# =========================================================
# DATE CONTEXT
# =========================================================

CURRENT_DATE = datetime.now()

CURRENT_DATE_CONTEXT = f"""
You are running locally in realtime on the user's PC.

Current date and time:
{CURRENT_DATE.strftime('%A, %B %d, %Y %H:%M')}

IMPORTANT:
- You HAVE realtime tool access.
- Never mention training cutoffs.
- Assume the current year is {CURRENT_DATE.year}.
"""

# =========================================================
# AGENT PROMPT
# =========================================================

AGENT_SYSTEM_PROMPT = """
You are an autonomous desktop AI assistant.

You are ONLY responsible for:
- deciding which tools to use
- executing actions
- gathering information
- determining task completion

You are NOT responsible for JSON formatting.

CRITICAL RULES

- NEVER loop on identical tool calls.
- NEVER repeatedly retry failed tools.
- NEVER repeat identical searches.
- Stop once sufficient information exists.
- Prefer concise execution.

WEB SEARCH RULES

- Preserve URLs from web searches.
- Include important URLs in summaries.

URL SAFETY RULES

- Never invent URLs.
- Never guess URLs.
- Only use URLs returned by tools.
- If a URL was not returned by a tool:
  use search_web first.
- Preserve URLs exactly as returned.
- Never modify URLs.
APPLICATION RULES

- If the user asks to open/run/start something:
  immediately use launch_application.

SHELL SAFETY

Never execute destructive commands:
- rm -rf
- shutdown
- reboot
- format
- mkfs
- powershell encoded commands

FINALIZATION RULE

Once complete:
- summarize actions
- summarize results
- include URLs
- stop
"""

# =========================================================
# FORMATTER PROMPT
# =========================================================

FORMATTER_PROMPT = """
You are a JSON response formatter.

Convert execution summaries into STRICT JSON.

Return ONLY raw JSON.

VALID FORMAT:

{
  "cards": [
    {
      "title": "Task Complete",
      "content": "Chrome launched successfully.",
      "url": null,
      "media": null,
      "type": "success"
    }
  ]
}

RULES

- Never use placeholder text like:
  "string", "example", "title"

- cards length: 1-5

- type must be:
  - info
  - success
  - warning
  - error

- Preserve URLs in card.url
"""

# =========================================================
# JSON HELPERS
# =========================================================

def safe_json_response(
    title: str,
    content: str,
    response_type: str = "info",
    url: Optional[str] = None,
):

    return json.dumps({
        "cards": [{
            "title": title,
            "content": content,
            "url": url,
            "media": None,
            "type": response_type,
        }]
    }, ensure_ascii=False)


def extract_json(text: str):

    if not text:
        raise ValueError("Empty response")

    text = text.strip()

    text = re.sub(
        r"<think>.*?(</think>|$)",
        "",
        text,
        flags=re.DOTALL,
    )

    decoder = JSONDecoder()

    for i, ch in enumerate(text):

        if ch == "{":

            try:
                obj, _ = decoder.raw_decode(
                    text[i:]
                )

                return obj

            except Exception:
                pass

    raise ValueError(
        "No valid JSON found"
    )

# =========================================================
# OCR SANITIZATION
# =========================================================

def sanitize_ocr_text(
    ocr_text: str,
    limit: int = 1000,
):

    cleaned = ocr_text.strip()[:limit]

    return (
        "UNTRUSTED SCREEN CONTENT\n\n"
        "<UNTRUSTED_OCR>\n"
        f"{cleaned}\n"
        "</UNTRUSTED_OCR>"
    )

# =========================================================
# URL EXTRACTION
# =========================================================

URL_REGEX = re.compile(
    r"https?://[^\s]+"
)

# =========================================================
# OUTPUT VALIDATION
# =========================================================

VALID_TYPES = {
    "info",
    "success",
    "warning",
    "error",
}


def validate_output(
    parsed: dict,
    fallback_urls=None,
):

    fallback_urls = fallback_urls or []

    cards = parsed.get(
        "cards",
        [],
    )

    cleaned = []

    for card in cards:

        if not isinstance(card, dict):
            continue

        title = str(
            card.get(
                "title",
                "Response",
            )
        )

        content = str(
            card.get(
                "content",
                "",
            )
        )

        if (
            title == "string"
            or content == "string"
        ):
            continue

        card_type = str(
            card.get(
                "type",
                "info",
            )
        ).lower()

        if card_type not in VALID_TYPES:
            card_type = "info"

        url = card.get("url")

        if (
            not url
            and fallback_urls
        ):
            url = fallback_urls[0]

        cleaned.append({
            "title": title[:80],
            "content": content[:2500],
            "url": url,
            "media": None,
            "type": card_type,
        })

    if not cleaned:

        cleaned = [{
            "title": "Assistant",
            "content": "Task completed.",
            "url": (
                fallback_urls[0]
                if fallback_urls
                else None
            ),
            "media": None,
            "type": "info",
        }]

    return {
        "cards": cleaned[:5]
    }

# =========================================================
# SUMMARY EXTRACTION
# =========================================================

def summarize_agent_result(
    query: str,
    messages,
):

    actions = []
    tool_results = []
    urls = []

    final_response = ""

    for msg in messages:

        # TOOL OUTPUTS
        if isinstance(
            msg,
            ToolMessage,
        ):

            content = str(
                msg.content
            ).strip()

            if content:

                urls.extend(
                    URL_REGEX.findall(
                        content
                    )
                )

                tool_results.append(
                    content[:500]
                )

        # AI OUTPUTS
        elif isinstance(
            msg,
            AIMessage,
        ):

            content = str(
                msg.content
            ).strip()

            if not content:
                continue

            urls.extend(
                URL_REGEX.findall(
                    content
                )
            )

            if len(content) < 300:
                actions.append(content)

            final_response = content

    urls = list(
        dict.fromkeys(urls)
    )

    summary = f"""
Task:
{query}

Actions:
{chr(10).join(f"- {a}" for a in actions[:6])}

Tool Results:
{chr(10).join(f"- {r}" for r in tool_results[:6])}

URLs:
{chr(10).join(urls[:8])}

Outcome:
{final_response}
"""

    return summary[-3500:], urls

# =========================================================
# ORCHESTRATOR
# =========================================================

class AgentOrchestrator:

    SIMPLE_QUERIES = {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
    }

    def __init__(self):

        self._llm_manager = (
            LLMManager()
        )

        # =================================================
        # TOOL ORCHESTRATOR MODEL
        # =================================================

        self._agent_llm = (
            self._llm_manager.get_model(
                model_name="qwen2.5:7b",
                temperature=0.0,
                timeout=45,
                num_predict=1024,
            )
        )

        # =================================================
        # JSON FORMATTER MODEL
        # =================================================

        self._formatter_llm = (
            self._llm_manager.get_model(
                model_name="qwen2.5:7b",
                temperature=0.0,
                timeout=45,
                num_predict=768,
            )
        )

        # =================================================
        # AGENT
        # =================================================

        self._agent = create_react_agent(
            self._agent_llm,
            ALL_TOOLS,
        )

    # =====================================================
    # SIMPLE QUERY
    # =====================================================

    def _is_simple_query(
        self,
        query: str,
    ):

        return (
            query.lower().strip()
            in self.SIMPLE_QUERIES
        )

    # =====================================================
    # BUILD MESSAGES
    # =====================================================

    def _build_messages(
        self,
        query: str,
        ocr_text: str = "",
        chat_history=None,
    ):

        messages = [
            SystemMessage(
                content=(
                    CURRENT_DATE_CONTEXT
                    + "\n"
                    + AGENT_SYSTEM_PROMPT
                )
            )
        ]



        user_content = query

        if ocr_text:

            user_content += (
                "\n\nSCREEN CONTEXT:\n"
                + sanitize_ocr_text(
                    ocr_text
                )
            )

        messages.append(
            HumanMessage(
                content=user_content
            )
        )

        return messages

    # =====================================================
    # RUN AGENT
    # =====================================================

    def _run_agent(
        self,
        messages,
    ):

        try:

            return self._agent.invoke(
                {"messages": messages},
                config={
                    "recursion_limit": 10
                },
            )

        except GraphRecursionError:

            logging.warning(
                "Recursion limit reached."
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Task partially completed. "
                            "Agent stopped because "
                            "the recursion limit "
                            "was reached."
                        )
                    )
                ]
            }

    # =====================================================
    # FORMAT RESPONSE
    # =====================================================

    def _format_response(
        self,
        summary: str,
        urls: List[str],
    ):

        response = (
            self._formatter_llm.invoke([
                SystemMessage(
                    content=FORMATTER_PROMPT
                ),
                HumanMessage(
                    content=summary
                ),
            ])
        )

        parsed = extract_json(
            str(response.content)
        )

        return validate_output(
            parsed,
            fallback_urls=urls,
        )

    # =====================================================
    # MAIN ENTRYPOINT
    # =====================================================

    def process_query(
        self,
        query: str,
        ocr_text: str = "",
        chat_history=None,
    ):

        query = query.strip()

        try:

            logging.info(
                f"Processing: {query}"
            )

            # SIMPLE PATH
            if self._is_simple_query(
                query
            ):

                return safe_json_response(
                    "Assistant",
                    "Hello! How can I help?",
                    "info",
                )

            # BUILD INPUT
            messages = (
                self._build_messages(
                    query=query,
                    ocr_text=ocr_text,
                    chat_history=chat_history,
                )
            )

            # RUN AGENT
            result = self._run_agent(
                messages
            )

            result_messages = result.get(
                "messages",
                [],
            )

            # SUMMARY
            summary, urls = (
                summarize_agent_result(
                    query=query,
                    messages=result_messages,
                )
            )

            logging.info(
                f"Summary:\n{summary[:700]}"
            )

            # FORMAT
            formatted = (
                self._format_response(
                    summary,
                    urls,
                )
            )

            add_memory(
                "user",
                query,
            )

            add_memory(
                "assistant",
                summary
            )
            return json.dumps(
                formatted,
                ensure_ascii=False,
            )

        except Exception as e:

            logging.error(
                f"Processing failed: {e}",
                exc_info=True,
            )

            return safe_json_response(
                "Execution Error",
                str(e),
                "error",
            )