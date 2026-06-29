import json
import logging

from langchain_core.messages import HumanMessage
from src.prompts.response_builder import system_prompt
from json_repair import repair_json
class ResponseBuilderAgent:

    def __init__(self, llm):
        self.llm = llm

    # =====================================================
    # EXTRACT TEXT FROM LLM RESPONSE
    # =====================================================

    def _extract_text(self, content):

        # ---------------------------------------------
        # normal string response
        # ---------------------------------------------

        if isinstance(content, str):
            return content

        # ---------------------------------------------
        # Gemini/OpenAI structured block response
        # ---------------------------------------------

        if isinstance(content, list):

            parts = []

            for item in content:

                if isinstance(item, dict):

                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))

                elif isinstance(item, str):
                    parts.append(item)

            return "\n".join(parts)

        # ---------------------------------------------
        # fallback
        # ---------------------------------------------

        return str(content)

    # =====================================================
    # BUILD RESPONSE
    # =====================================================

    def build(
        self,
        query: str,
        agent_response_text: str,
    ):

        # -------------------------------------------------
        # PROMPT
        # -------------------------------------------------


        input_prompt = f"""
            USER QUERY:
            {query}

            AGENT RESPONSE:
            {agent_response_text}
        """

        prompt = system_prompt + "\n" + input_prompt
        # -------------------------------------------------
        # LLM CALL
        # -------------------------------------------------

        response = self.llm.invoke([
            HumanMessage(content=prompt)
        ])

        # -------------------------------------------------
        # RAW OUTPUT
        # -------------------------------------------------

        logging.info(
            f"[RESPONSE BUILDER RAW OUTPUT]\n{response.content}"
        )

        # -------------------------------------------------
        # EXTRACT TEXT
        # -------------------------------------------------

        raw_text = self._extract_text(
            response.content
        )

        # -------------------------------------------------
        # CLEAN
        # -------------------------------------------------

        raw_text = (
            raw_text
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        # -------------------------------------------------
        # PARSE JSON
        # -------------------------------------------------

        try:

            parsed = json.loads(repair_json(raw_text))

            # -----------------------------------------
            # validate cards
            # -----------------------------------------

            cards = parsed.get("cards", [])

            validated_cards = []

            for c in cards:

                validated_cards.append({
                    "title": str(
                        c.get("title", "Response")
                    ),

                    "content": str(
                        c.get("content", "")
                    ),

                    "type": str(
                        c.get("type", "info")
                    ),

                    "url": c.get("url"),
                })

            return {
                "cards": validated_cards
            }

        except Exception:

            logging.exception(
                "[RESPONSE BUILDER] Failed parsing JSON"
            )

            # fallback safe card

            return {
                "cards": [
                    {
                        "title": "Assistant Response",
                        "content": raw_text,
                        "type": "info",
                        "url": None,
                    }
                ]
            }