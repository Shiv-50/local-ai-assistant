from src.tools.system_tools import (
    launch_application,
    search_installed_apps,
    launch_app_by_id,
    execute_shell_command,
    focus_window,
)
from src.tools.web_tools import open_url_in_browser
from src.tools.input_tools import type_text, press_hotkey, press_key, get_clipboard, set_clipboard
from src.tools.mouse_tools import mouse_click, mouse_move, get_mouse_position, get_screen_size
from src.tools.vision_tools import analyze_screen_with_vision
from src.tools.memory_tools import retrieve_memory

# =========================================================
# CENTRAL TOOL REGISTRY
# =========================================================
#
# Web search used to be handled by the in-process `parallel_search` tool,
# which fanned out raw HTML scrapes across DuckDuckGo/Bing/Brave by hand.
# That's now replaced by a real, free MCP search server (DuckDuckGo —
# see src/core/mcp_manager.py) loaded at startup and injected here via
# `build_general_tools()`. No API key required; more capable than the
# old scraper since it ships its own rate limiting and clean content
# extraction instead of hand-rolled CSS selectors.

STATIC_TOOLS = [
    # System
    launch_application,
    search_installed_apps,
    launch_app_by_id,
    execute_shell_command,
    focus_window,

    # Web (non-search)
    open_url_in_browser,

    # Input
    type_text,
    press_hotkey,
    press_key,
    get_clipboard,
    set_clipboard,

    # Mouse
    mouse_click,
    mouse_move,
    get_mouse_position,
    get_screen_size,

    # Vision
    analyze_screen_with_vision,

    # Memory
    retrieve_memory,
]


def build_general_tools(search_tools: list | None = None) -> list:
    """
    Assemble the full tool list for the general agent: static local tools
    plus whatever MCP search tools (e.g. Tavily's tavily-search /
    tavily-extract / tavily-crawl) were loaded by mcp_manager at startup.
    Falls back to an empty list if no search MCP server is configured
    (e.g. missing TAVILY_API_KEY), so the agent still runs — just without
    web search.
    """
    return STATIC_TOOLS + list(search_tools or [])


# Backwards-compatible default (no MCP search tools attached). Prefer
# calling build_general_tools(mcp_manager.search_tools) once MCP has
# been initialized.
GENERAL_TOOLS = build_general_tools()
