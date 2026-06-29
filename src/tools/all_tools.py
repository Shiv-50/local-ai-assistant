from src.tools.system_tools import (
    launch_application, 
    search_installed_apps, 
    launch_app_by_id, 
    execute_shell_command, 
    focus_window
)
from src.tools.web_tools import search_web, open_url_in_browser
from src.tools.input_tools import type_text, press_hotkey, press_key, get_clipboard, set_clipboard
from src.tools.mouse_tools import mouse_click, mouse_move, get_mouse_position, get_screen_size
from src.tools.vision_tools import analyze_screen_with_vision
from src.tools.memory_tools import retrieve_memory

# =========================================================
# CENTRAL TOOL REGISTRY (FLAT LIST FOR LANGCHAIN REACT AGENTS)
# =========================================================

GENERAL_TOOLS = [
    # System
    launch_application,
    search_installed_apps,
    launch_app_by_id,
    execute_shell_command,
    focus_window,
    
    # Web
    search_web,
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