# src/utils/response_builder.py

from src.tools.tool_response import ToolResponse


def build_response(
    title: str,
    content,
    status: str = "info",
    urls=None,
):
    urls = urls or []

    return {
        "cards": [
            {
                "title": title,
                "content": str(content),
                "url": urls[0] if urls else None,
                "media": None,
                "type": status,
            }
        ]
    }


def build_tool_response(
    result: ToolResponse,
):
    """
    Smart UI builder for ToolResponse objects.
    """

    # =====================================================
    # WEB SEARCH MULTI CARD
    # =====================================================

    if (
        result.tool_name == "search_web"
        and result.metadata.get("results")
    ):

        cards = []

        for r in result.metadata["results"]:

            content = r.get(
                "description",
                "",
            )

            if r.get("page_content"):

                preview = r["page_content"][:1200]

                content += (
                    "\n\n"
                    + preview
                )

            cards.append({
                "title": r.get(
                    "title",
                    "Search Result",
                ),
                "content": content,
                "url": r.get("url"),
                "media": None,
                "type": result.status,
            })

        return {
            "cards": cards
        }

    # =====================================================
    # DEFAULT SINGLE CARD
    # =====================================================

    return {
        "cards": [
            {
                "title": result.title,
                "content": str(result.content),
                "url": result.url,
                "media": result.media,
                "type": result.status,
            }
        ]
    }


def build_multi_response(
    title: str,
    steps,
):

    cards = []

    for step in steps:

        cards.append({
            "title": step.get(
                "tool",
                "Step",
            ),
            "content": step.get(
                "result",
                "",
            ),
            "url": None,
            "media": None,
            "type": step.get(
                "status",
                "info",
            ),
        })

    return {
        "cards": cards
    }