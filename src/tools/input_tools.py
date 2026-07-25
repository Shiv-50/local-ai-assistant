import pyautogui
import keyboard as kb
import pyperclip
import logging
import time
from pywinauto import Desktop
from src.tools.base import safe_tool
from src.tools.system_tools import focus_window
import re
# =========================================================
# TYPE TEXT
# =========================================================

# src/tools/input_tools.py

def _get_focused_control_info():
    try:
        from pywinauto import Desktop
        control = Desktop(backend="uia").get_focused()
        return {
            "control_type": control.friendly_class_name(),
            "name": control.window_text(),
        }
    except Exception as e:
        logging.warning(f"[TYPE TEXT] Could not read focused control: {e}")
        return None

EDITABLE_CONTROL_TYPES = {"Edit", "Document", "ComboBox", "SearchBox", "RichEdit"}

@safe_tool("Type Text")
def type_text(text, interval=0.02, auto_enter=False, window_title=None, skip_focus_check=False):
    if window_title:
        try:
            from src.tools.system_tools import focus_window
            focus_window.func(window_title)
            time.sleep(0.2)
        except Exception as e:
            logging.warning(f"[TYPE TEXT] Focus failed: {e}")

    focused = _get_focused_control_info()

    if not skip_focus_check and focused and focused["control_type"] not in EDITABLE_CONTROL_TYPES:
        return {
            "status": "blocked_wrong_focus",
            "reason": f"Focused element is '{focused['control_type']}' ({focused['name']}), not a text input.",
            "next_step": "Use find_and_click_element or vision to locate the correct field, click it, then retry.",
        }

    pyautogui.write(text, interval=interval)
    if auto_enter:
        pyautogui.press("enter")

    return {"status": "typed", "text": text, "focused_control_before_typing": focused}

# =========================================================
# PRESS HOTKEY
# =========================================================


_AHK_KEY_MAP = {
    "lctrl": "ctrl", "rctrl": "ctrl", "control": "ctrl",
    "lalt": "alt", "ralt": "alt",
    "lshift": "shift", "rshift": "shift",
    "win": "windows", "lwin": "windows", "rwin": "windows",
    "esc": "escape", "return": "enter", "del": "delete",
}

def _normalize_hotkey(keys: str) -> str:
    """Accept AHK/pywinauto-style '{LCTRL}{F}' and convert to the
    keyboard library's 'ctrl+f' syntax, since models sometimes confuse
    this with type_keys()'s bracket syntax used elsewhere in this codebase."""
    if "{" not in keys:
        return keys
    tokens = re.findall(r"\{([^}]+)\}", keys)
    if not tokens:
        return keys
    tokens = [_AHK_KEY_MAP.get(t.lower(), t.lower()) for t in tokens]
    return "+".join(tokens)


@safe_tool("Press Hotkey")
def press_hotkey(keys: str):
    normalized = _normalize_hotkey(keys)

    logging.info(f"[HOTKEY] input={keys} normalized={normalized}")

    try:
        kb.press_and_release(normalized)
    except ValueError as e:
        return {
            "status": "invalid_hotkey",
            "input": keys,
            "attempted": normalized,
            "error": str(e),
            "hint": (
                "Use plus-separated lowercase key names, e.g. 'ctrl+f', "
                "'ctrl+shift+p', 'alt+tab', 'enter', 'escape'. Do not use "
                "curly-brace syntax like '{CTRL}{F}' — that's for a "
                "different tool."
            ),
        }

    return f"Pressed hotkey:\n{normalized}"


# =========================================================
# PRESS SINGLE KEY
# =========================================================

@safe_tool("Press Key")
def press_key(
    key: str,
):

    logging.info(
        f"[KEY] key={key}"
    )

    pyautogui.press(key)

    return f"Pressed key:\n{key}"


# =========================================================
# GET CLIPBOARD
# =========================================================

@safe_tool("Get Clipboard")
def get_clipboard():

    logging.info(
        "[CLIPBOARD] reading clipboard"
    )

    content = pyperclip.paste()

    if not content:
        content = "[Clipboard Empty]"

    return content


# =========================================================
# SET CLIPBOARD
# =========================================================

@safe_tool("Set Clipboard")
def set_clipboard(
    text: str,
):

    logging.info(
        "[CLIPBOARD] setting clipboard"
    )

    pyperclip.copy(text)

    return f"Copied to clipboard:\n{text}"


# =========================================================
# EXPORTS
# =========================================================

ALL_INPUT_TOOLS = [
    type_text,
    press_hotkey,
    press_key,
    get_clipboard,
    set_clipboard,
]
