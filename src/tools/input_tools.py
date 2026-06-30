import pyautogui
import keyboard as kb
import pyperclip
import logging
import time

from src.tools.base import safe_tool


# =========================================================
# TYPE TEXT
# =========================================================

@safe_tool("Type Text")
def type_text(
    text: str,
    interval: float = 0.02,
    auto_enter: bool = False,
    window_title: str = None,
):

    if window_title:
        try:
            from src.tools.system_tools import focus_window
            focus_result = focus_window.func(window_title)
            logging.info(f"[TYPE TEXT] Focus result: {focus_result}")
            time.sleep(0.2)
        except Exception as e:
            logging.warning(f"[TYPE TEXT] Failed to focus window '{window_title}': {e}")

    logging.info(
        f"[TYPE TEXT] text={text}"
    )

    pyautogui.write(
        text,
        interval=interval,
    )

    if auto_enter:
        pyautogui.press("enter")

    return f"Typed:\n{text}"


# =========================================================
# PRESS HOTKEY
# =========================================================

@safe_tool("Press Hotkey")
def press_hotkey(
    keys: str,
):

    logging.info(
        f"[HOTKEY] keys={keys}"
    )

    kb.press_and_release(keys)

    return f"Pressed hotkey:\n{keys}"


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
