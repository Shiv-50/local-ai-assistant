import logging

from src.memory_store import search_memory
from src.tools.base import safe_tool


# =========================================================
# MEMORY RETRIEVAL
# =========================================================

@safe_tool("Retrieve Memory")
def retrieve_memory(
    query: str,
):
    """
    Semantic memory retrieval using FAISS/vector search.
    """

    logging.info(
        f"[MEMORY SEARCH] query={query}"
    )

    results = search_memory(
        query=query,
        top_k=top_k,
    )

    # -----------------------------------------------------
    # NO RESULTS
    # -----------------------------------------------------

    if not results:

        logging.info(
            "[MEMORY SEARCH] no results found"
        )

        return f"No relevant memory found for:\n{query}"

    # -----------------------------------------------------
    # FORMAT RESULTS
    # -----------------------------------------------------

    formatted = []

    for i, memory in enumerate(results):

        role = memory.get(
            "role",
            "unknown",
        )

        content = memory.get(
            "content",
            "",
        )

        formatted.append(
            f"[{i + 1}] ({role})\n{content}"
        )

    final_content = "\n\n".join(formatted)

    logging.info(
        f"[MEMORY SEARCH] found "
        f"{len(results)} memories"
    )

    return final_content


# =========================================================
# EXPORTS
# =========================================================

ALL_MEMORY_TOOLS = [
    retrieve_memory,
]