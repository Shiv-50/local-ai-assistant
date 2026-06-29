import json

from langchain_core.messages import HumanMessage
from src.prompts.response_builder import system_prompt
from json_repair import repair_json

from src.utils.logger import get_logger, TimedBlock

log = get_logger(__name__)


class ResponseBuilderAgent:

    def __init__(self, llm):
        self.llm = llm

    # =====================================================
    # EXTRACT TEXT FROM LLM RESPONSE
    # =====================================================

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)

        return str(content)

    # =====================================================
    # BUILD RESPONSE
    # =====================================================

    def build(self, query: str, agent_response_text: str):
        log.info("response_builder.build.start",
                 query_len=len(query),
                 agent_response_len=len(agent_response_text))

        input_prompt = f"""
            USER QUERY:
            {query}

            AGENT RESPONSE:
            {agent_response_text}
        """
        prompt = system_prompt + "\n" + input_prompt

        # ── LLM call ─────────────────────────────────────────
        with TimedBlock(log, "response_builder.llm_invoke"):
            response = self.llm.invoke([HumanMessage(content=prompt)])

        log.debug("response_builder.raw_output", content=str(response.content)[:300])

        raw_text = self._extract_text(response.content)
        raw_text = (
            raw_text
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        # ── Parse JSON ────────────────────────────────────────
        try:
            parsed = json.loads(repair_json(raw_text))
            cards = parsed.get("cards", [])

            validated_cards = [
                {
                    "title": str(c.get("title", "Response")),
                    "content": str(c.get("content", "")),
                    "type": str(c.get("type", "info")),
                    "url": c.get("url"),
                }
                for c in cards
            ]

            log.info("response_builder.build.done", card_count=len(validated_cards))
            return {"cards": validated_cards}

        except Exception:
            log.exception("response_builder.json_parse_failed",
                          raw_preview=raw_text[:200])
            return {
                "cards": [{
                    "title": "Assistant Response",
                    "content": raw_text,
                    "type": "info",
                    "url": None,
                }]
            }
