"""
Optimized tool registry for local LLM tool calling.

Goal:
- Maximize tool selection accuracy
- Reduce ambiguity between similar tools
- Improve Qwen/Ollama tool adherence
- Make intent boundaries explicit
"""

from src.tools.system_tools import (
    launch_application,
    search_installed_apps,
    launch_app_by_id,
    execute_shell_command,
    focus_window,
)

from src.tools.web_tools import open_url_in_browser
from src.tools.input_tools import (
    type_text,
    press_hotkey,
    press_key,
    get_clipboard,
    set_clipboard,
)
from src.tools.mouse_tools import (
    mouse_click,
    mouse_move,
    get_mouse_position,
    get_screen_size,
)
from src.tools.vision_tools import analyze_screen_with_vision

# IMPORTANT: memory tool removed from agent space (intentional fix)


# =========================================================
# TOOL DOCUMENTATION LAYER (CRITICAL FOR LOCAL MODELS)
# =========================================================

def _enhance_tool(tool, description: str, usage: str, when_not_to_use: str):
    """
    Attaches structured reasoning hints to tools.
    This dramatically improves local model tool selection.
    """
    tool.description = f"""
{description}

WHEN TO USE:
{usage}

DO NOT USE WHEN:
{when_not_to_use}
"""
    return tool


# =========================================================
# SYSTEM TOOLS (OPTIMIZED)
# =========================================================

launch_application = _enhance_tool(
    launch_application,
    "Launch an installed desktop application by name.",
    "User explicitly asks to open/launch/start an app (e.g., 'open Chrome', 'launch VS Code').",
    "If app is already open OR user is asking to interact inside an app (use mouse/keyboard instead).",
)

search_installed_apps = _enhance_tool(
    search_installed_apps,
    "Find installed applications on the system.",
    "User asks 'do I have X installed' or you need exact app name before launching.",
    "Do NOT use for general web search or browsing tasks.",
)

launch_app_by_id = _enhance_tool(
    launch_app_by_id,
    "Launch application using internal system identifier.",
    "When exact app ID is already known from search_installed_apps.",
    "Do NOT guess IDs; only use confirmed ones.",
)

execute_shell_command = _enhance_tool(
    execute_shell_command,
    "Execute a shell/terminal command.",
    "User explicitly requests CLI operations or system commands.",
    "Do NOT use for UI actions like clicking or typing.",
)

focus_window = _enhance_tool(
    focus_window,
    "Bring an application window into focus.",
    "When app is already open but not active.",
    "Do NOT use before launching app or when window state is unknown.",
)

open_url_in_browser = _enhance_tool(
    open_url_in_browser,
    "Open a website URL in the browser.",
    "User provides a direct URL or asks to open a website.",
    "Do NOT use if user wants interaction inside page (use browser agent).",
)

# =========================================================
# INPUT TOOLS (STRICT BOUNDARIES)
# =========================================================

type_text = _enhance_tool(
    type_text,
    "Type text into the currently focused input field.",
    "Only after user explicitly needs text entry in UI.",
    "Do NOT use without ensuring correct focus window is active.",
)

press_hotkey = _enhance_tool(
    press_hotkey,
    "Press keyboard shortcut combinations.",
    "For known shortcuts like Ctrl+C, Ctrl+V, Alt+Tab.",
    "Do NOT use for free-form typing.",
)

press_key = _enhance_tool(
    press_key,
    "Press a single keyboard key.",
    "Navigation keys like Enter, Escape, Tab, Arrow keys.",
    "Do NOT use for text input.",
)

get_clipboard = _enhance_tool(
    get_clipboard,
    "Read current system clipboard content.",
    "When user asks 'what is copied' or needs pasted content.",
    "Do not use proactively.",
)

set_clipboard = _enhance_tool(
    set_clipboard,
    "Set system clipboard content.",
    "When preparing text for paste operations.",
    "Do not use unless explicitly required for workflow.",
)

# =========================================================
# MOUSE TOOLS (STRICT VISUAL DEPENDENCY)
# =========================================================

mouse_click = _enhance_tool(
    mouse_click,
    "Click at a specific screen coordinate.",
    "Only after visual confirmation of UI element position.",
    "Do NOT guess coordinates without vision tool confirmation.",
)

mouse_move = _enhance_tool(
    mouse_move,
    "Move mouse cursor to coordinates.",
    "For hover actions or preparing click.",
    "Do NOT use without purpose (avoid unnecessary movement).",
)

get_mouse_position = _enhance_tool(
    get_mouse_position,
    "Get current mouse position.",
    "Debugging or coordinate alignment tasks.",
    "Not needed for normal workflows.",
)

get_screen_size = _enhance_tool(
    get_screen_size,
    "Get screen resolution dimensions.",
    "When calculating UI coordinates or layout scaling.",
    "Do not use for normal browsing tasks.",
)

# =========================================================
# VISION TOOL (IMPORTANT FOR AGENT CONTROL)
# =========================================================

analyze_screen_with_vision = _enhance_tool(
    analyze_screen_with_vision,
    "Analyze current screen visually using AI vision.",
    "ONLY when you need to identify UI elements, confirm state, or locate buttons.",
    "Do NOT use if task can be completed via known actions or already structured data.",
)

# =========================================================
# TOOL GROUPING
# =========================================================

STATIC_TOOLS = [
    launch_application,
    search_installed_apps,
    launch_app_by_id,
    execute_shell_command,
    focus_window,
    open_url_in_browser,
    type_text,
    press_hotkey,
    press_key,
    get_clipboard,
    set_clipboard,
    mouse_click,
    mouse_move,
    get_mouse_position,
    get_screen_size,
    analyze_screen_with_vision,
]

def build_general_tools(search_tools=None):
    """
    General agent toolset (NO MEMORY TOOL INCLUDED)
    """
    return STATIC_TOOLS + list(search_tools or [])

GENERAL_TOOLS = build_general_tools()