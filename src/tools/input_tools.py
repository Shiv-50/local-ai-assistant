import pyautogui
import keyboard as kb
import pyperclip
import logging

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
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle(window_title)
            if windows:
                windows[0].activate()
                logging.info(f"[TYPE TEXT] Activated window: {window_title}")
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