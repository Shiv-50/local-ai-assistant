import logging
import pyautogui

from langchain.tools import tool




# =========================================================
# CLICK
# =========================================================

@tool(description=(
    "Click the mouse at verified screen coordinates. Use only after "
    "analyze_screen_with_vision, get_active_window, or another tool has "
    "confirmed the target coordinates. Prefer keyboard navigation first."
))
def mouse_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
):

    try:

        width, height = pyautogui.size()

        if not (0 <= x < width and 0 <= y < height):

            return (
                f"Coordinates ({x}, {y}) are outside "
                f"screen bounds {width}x{height}"
            )

        logging.info(
            f"[MOUSE CLICK] ({x}, {y}) "
            f"button={button} clicks={clicks}"
        )

        pyautogui.click(
            x=x,
            y=y,
            button=button,
            clicks=clicks,
        )

        return (
            f"Clicked {button} mouse button "
            f"at ({x}, {y})"
        )

    except Exception as e:

        logging.exception("mouse_click failed")

        return str(e)


# =========================================================
# MOVE
# =========================================================

@tool(description="Move mouse cursor to coordinates")
def mouse_move(
    x: int,
    y: int,
    duration: float = 0.2,
):

    try:

        width, height = pyautogui.size()

        if not (0 <= x < width and 0 <= y < height):

            return (
                f"Coordinates ({x}, {y}) are outside "
                f"screen bounds {width}x{height}"
            )

        logging.info(
            f"[MOUSE MOVE] ({x}, {y})"
        )

        pyautogui.moveTo(
            x,
            y,
            duration=duration,
        )

        return f"Moved mouse to ({x}, {y})"

    except Exception as e:

        logging.exception("mouse_move failed")

        return str(e)


# =========================================================
# POSITION
# =========================================================

@tool(description="Get current mouse cursor position")
def get_mouse_position():

    try:

        x, y = pyautogui.position()

        logging.info(
            f"[MOUSE POSITION] ({x}, {y})"
        )

        return f"Mouse position: ({x}, {y})"

    except Exception as e:

        logging.exception("get_mouse_position failed")

        return str(e)


# =========================================================
# SCREEN SIZE
# =========================================================

@tool(description="Get screen resolution")
def get_screen_size():

    try:

        width, height = pyautogui.size()

        logging.info(
            f"[SCREEN SIZE] {width}x{height}"
        )

        return f"Screen resolution: {width}x{height}"

    except Exception as e:

        logging.exception("get_screen_size failed")

        return str(e)
