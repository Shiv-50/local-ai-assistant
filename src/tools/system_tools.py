import logging
import subprocess
import json
import shutil
import time
import uuid
from typing import Optional
import ctypes
from pywinauto import Desktop
from src.tools.base import safe_tool


# =========================================================
# APPLICATION MAPS
# =========================================================

APP_MAP = {
    "chrome": "start chrome",
    "vscode": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "explorer": "explorer",
    "settings": "ms-settings:",
    "terminal": "wt",
    "cmd": "cmd",
}


SETTINGS_PAGE_URIS = {
    "storage": "ms-settings:storagesense",
    "storage sense": "ms-settings:storagesense",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth",
    "network": "ms-settings:network",
    "wifi": "ms-settings:network-wifi",
    "apps": "ms-settings:appsfeatures",
    "privacy": "ms-settings:privacy",
    "windows update": "ms-settings:windowsupdate",
    "update": "ms-settings:windowsupdate",
    "power": "ms-settings:powersleep",
    "battery": "ms-settings:batterysaver",
    "default apps": "ms-settings:defaultapps",
}


BROWSER_APP_KEYS = {
    "browser",
    "chrome",
    "edge",
    "firefox",
    "opera",
    "brave",
}


BROWSER_TITLE_SUFFIXES = (
    " - Google Chrome",
    " - Microsoft Edge",
    " - Mozilla Firefox",
    " - Opera",
    " - Brave",
)


APP_TITLE_ALIASES = {
    "settings": ("Settings",),
    "calculator": ("Calculator",),
    "notepad": ("Notepad",),
    "explorer": ("File Explorer", "Explorer"),
    "terminal": (
        "Terminal",
        "Windows PowerShell",
        "Command Prompt",
    ),
    "cmd": ("Command Prompt",),
}


# =========================================================
# UI AUTOMATION STATE
# =========================================================

# Elements are registered after desktop_snapshot().
#
# Example:
#
# desktop_snapshot()
#   -> element_id = "ui_abc123"
#
# desktop_click("ui_abc123")
#
# The registry is intentionally process-local. UIA elements can become
# stale after navigation, so the agent should take a fresh snapshot after
# major UI changes.
UI_ELEMENT_REGISTRY = {}

CURRENT_SNAPSHOT_ID = None


# =========================================================
# WINDOW HELPERS
# =========================================================

def _is_browser_app(query: str) -> bool:
    query = query.lower().strip()
    return any(key in query for key in BROWSER_APP_KEYS)


def _is_browser_page_title(title: str) -> bool:
    return title.endswith(BROWSER_TITLE_SUFFIXES)


def _visible_windows():
    import pygetwindow as gw

    windows = []

    for win in gw.getAllWindows():

        title = (win.title or "").strip()

        if not title:
            continue

        windows.append({
            "title": title,
            "left": win.left,
            "top": win.top,
            "width": win.width,
            "height": win.height,
            "is_active": win.isActive,
            "is_minimized": win.isMinimized,
            "is_maximized": win.isMaximized,
        })

    return windows


def _window_match_score(
    query: str,
    title: str,
) -> int:

    query_lower = query.lower().strip()
    title_lower = title.lower().strip()

    aliases = APP_TITLE_ALIASES.get(
        query_lower,
        (query,),
    )

    for alias in aliases:

        alias_lower = alias.lower()

        if title_lower == alias_lower:
            return 100

        if title_lower.startswith(alias_lower + " -"):
            return 80

    if query_lower and query_lower in title_lower:
        return 20

    return 0


def _matching_windows(
    query: str,
    allow_browser_page_titles: bool = False,
):

    query = query.lower().strip()

    allow_browser_page_titles = (
        allow_browser_page_titles
        or _is_browser_app(query)
    )

    scored = []

    for win in _visible_windows():

        title = win["title"]

        if (
            _is_browser_page_title(title)
            and not allow_browser_page_titles
        ):
            continue

        score = _window_match_score(
            query,
            title,
        )

        if score:
            scored.append(
                (score, win)
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        win
        for _, win in scored
    ]


def _find_best_window(
    title: str,
    allow_browser_page_titles: bool = False,
):

    matches = _matching_windows(
        title,
        allow_browser_page_titles=allow_browser_page_titles,
    )

    return matches[0] if matches else None


def _wait_for_matching_window(
    query: str,
    timeout: float = 4.0,
):

    deadline = time.time() + timeout

    while time.time() < deadline:

        match = _find_best_window(query)

        if match:
            return match

        time.sleep(0.3)

    return None


# =========================================================
# UI AUTOMATION HELPERS
# =========================================================

def _get_active_uia_window():
    """
    Return the active window through Microsoft UI Automation.

    pywinauto's UIA backend is used because it exposes semantic controls
    such as Button, Edit, CheckBox, ComboBox, ListItem, etc.
    """

    from pywinauto import Desktop

    return Desktop(
        backend="uia"
    ).get_active()


def _safe_control_text(control) -> str:

    try:
        return (
            control.window_text()
            or ""
        ).strip()

    except Exception:
        return ""


def _safe_control_type(control) -> str:

    try:

        control_type = (
            control.element_info.control_type
        )

        if control_type:
            return control_type

    except Exception:
        pass

    try:
        return control.friendly_class_name()

    except Exception:
        return "Unknown"


def _safe_automation_id(control) -> str:

    try:
        return (
            control.element_info.automation_id
            or ""
        )

    except Exception:
        return ""


def _safe_class_name(control) -> str:

    try:
        return (
            control.element_info.class_name
            or ""
        )

    except Exception:
        return ""


def _safe_is_visible(control) -> bool:

    try:
        return control.is_visible()

    except Exception:
        return True


def _safe_is_enabled(control) -> bool:

    try:
        return control.is_enabled()

    except Exception:
        return True


def _safe_rect(control):

    try:

        rect = control.rectangle()

        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.width(),
            "height": rect.height(),
        }

    except Exception:
        return None


def _safe_current_value(control):

    """
    Try to read the current value of a UI element.

    Different Windows controls expose values through different patterns.
    """

    # UIA ValuePattern
    try:

        value_pattern = (
            control.iface_value
        )

        value = value_pattern.CurrentValue

        if value is not None:
            return value

    except Exception:
        pass

    # Edit control fallback
    try:

        value = control.get_value()

        if value is not None:
            return value

    except Exception:
        pass

    # Text fallback
    return _safe_control_text(control)


def _is_useful_control(control) -> bool:

    """
    Filter out most UIA noise.

    We keep controls that are useful for navigation:
    buttons, edit fields, checkboxes, radio buttons, combo boxes,
    list items, tabs, links, menu items, sliders, etc.
    """

    useful_types = {
        "Button",
        "Edit",
        "CheckBox",
        "RadioButton",
        "ComboBox",
        "List",
        "ListItem",
        "Tab",
        "TabItem",
        "Hyperlink",
        "Menu",
        "MenuItem",
        "Tree",
        "TreeItem",
        "Slider",
        "Spinner",
        "Calendar",
        "DataGrid",
        "DataItem",
        "Document",
        "Text",
    }

    control_type = _safe_control_type(control)
    text = _safe_control_text(control)

    if control_type not in useful_types:
        return False

    if not text and control_type not in {
        "Edit",
        "Document",
        "Slider",
        "Spinner",
    }:
        return False

    return True


def _register_ui_element(
    control,
    snapshot_id: str,
    index: int,
):

    element_id = (
        f"ui_{snapshot_id}_{index}"
    )

    UI_ELEMENT_REGISTRY[element_id] = {
        "control": control,
        "snapshot_id": snapshot_id,
        "created_at": time.time(),
    }

    return element_id


def _get_registered_element(
    element_id: str,
):

    entry = UI_ELEMENT_REGISTRY.get(
        element_id
    )

    if not entry:
        return None

    return entry


def _clear_old_ui_elements(
    keep_snapshot_id: Optional[str] = None,
):

    if not keep_snapshot_id:
        UI_ELEMENT_REGISTRY.clear()
        return

    stale_ids = [
        element_id
        for element_id, entry
        in UI_ELEMENT_REGISTRY.items()
        if entry.get("snapshot_id")
        != keep_snapshot_id
    ]

    for element_id in stale_ids:
        UI_ELEMENT_REGISTRY.pop(
            element_id,
            None,
        )


# =========================================================
# DESKTOP SNAPSHOT
# =========================================================

@safe_tool("Desktop Snapshot")
def desktop_snapshot(
    window_title: str = None,
    max_elements: int = 150,
):

    """
    Inspect the current desktop window through Windows UI Automation.

    This is the primary perception tool for desktop navigation.

    The agent should call this before attempting to interact with a
    previously unknown UI element.

    Returns semantic controls such as:

        {
            "id": "ui_xxx_4",
            "role": "Edit",
            "name": "Email",
            "automation_id": "emailInput",
            "value": "",
            "enabled": true,
            "visible": true
        }
    """

    global CURRENT_SNAPSHOT_ID

    try:

        from pywinauto import Desktop

        max_elements = max(
            10,
            min(
                int(max_elements),
                300,
            ),
        )

        if window_title:

            window = Desktop(
                backend="uia"
            ).window(
                title_re=(
                    f".*{window_title}.*"
                )
            )

        else:

            window = (
                Desktop(
                    backend="uia"
                ).get_active()
            )

        title = window.window_text()

        snapshot_id = uuid.uuid4().hex[:8]

        CURRENT_SNAPSHOT_ID = snapshot_id

        # Remove stale references from previous snapshots.
        _clear_old_ui_elements(
            keep_snapshot_id=snapshot_id
        )

        elements = []

        controls = window.descendants()

        for control in controls:

            if len(elements) >= max_elements:
                break

            try:

                if not _safe_is_visible(control):
                    continue

                if not _is_useful_control(control):
                    continue

                control_type = (
                    _safe_control_type(control)
                )

                name = (
                    _safe_control_text(control)
                )

                automation_id = (
                    _safe_automation_id(control)
                )

                class_name = (
                    _safe_class_name(control)
                )

                value = (
                    _safe_current_value(control)
                )

                rectangle = (
                    _safe_rect(control)
                )

                element_id = (
                    _register_ui_element(
                        control,
                        snapshot_id,
                        len(elements),
                    )
                )

                element = {
                    "id": element_id,
                    "role": control_type,
                    "name": name,
                    "automation_id": automation_id,
                    "class_name": class_name,
                    "value": value,
                    "enabled": _safe_is_enabled(
                        control
                    ),
                    "visible": True,
                }

                if rectangle:
                    element["bounds"] = rectangle

                elements.append(element)

            except Exception as e:

                logging.debug(
                    "[DESKTOP SNAPSHOT] "
                    f"Skipping control: {e}"
                )

        return {
            "status": "success",
            "snapshot_id": snapshot_id,
            "window": title,
            "element_count": len(elements),
            "elements": elements,
            "next_step": (
                "Use the element id from this snapshot "
                "for desktop_click, desktop_focus, "
                "desktop_type, or desktop_get_value."
            ),
        }

    except Exception as e:

        logging.exception(
            "[DESKTOP SNAPSHOT] Failed"
        )

        return {
            "status": "error",
            "error": str(e),
        }


# =========================================================
# FIND UI ELEMENT
# =========================================================

@safe_tool("Find Desktop Element")
def desktop_find(
    name: str = "",
    role: str = "",
    automation_id: str = "",
    max_results: int = 10,
):

    """
    Search the current UIA tree semantically.

    Example:

        desktop_find(
            name="Email",
            role="Edit"
        )
    """

    try:

        window = (
            _get_active_uia_window()
        )

        name_query = (
            name.lower().strip()
        )

        role_query = (
            role.lower().strip()
        )

        automation_query = (
            automation_id.lower().strip()
        )

        results = []

        for control in window.descendants():

            if not _safe_is_visible(control):
                continue

            control_name = (
                _safe_control_text(control)
            )

            control_role = (
                _safe_control_type(control)
            )

            control_automation_id = (
                _safe_automation_id(control)
            )

            if (
                name_query
                and name_query
                not in control_name.lower()
            ):
                continue

            if (
                role_query
                and role_query
                not in control_role.lower()
            ):
                continue

            if (
                automation_query
                and automation_query
                not in control_automation_id.lower()
            ):
                continue

            element_id = (
                _register_ui_element(
                    control,
                    CURRENT_SNAPSHOT_ID
                    or "search",
                    len(results),
                )
            )

            results.append({
                "id": element_id,
                "role": control_role,
                "name": control_name,
                "automation_id": control_automation_id,
                "value": _safe_current_value(
                    control
                ),
                "bounds": _safe_rect(control),
            })

            if len(results) >= max_results:
                break

        return {
            "status": "success",
            "matches": results,
        }

    except Exception as e:

        return {
            "status": "error",
            "error": str(e),
        }


# =========================================================
# FOCUS UI ELEMENT
# =========================================================

@safe_tool("Focus Desktop Element")
def desktop_focus(
    element_id: str,
):

    """
    Focus a UI element returned by desktop_snapshot().
    """

    entry = _get_registered_element(
        element_id
    )

    if not entry:

        return {
            "status": "error",
            "error": (
                f"Unknown or expired element id: "
                f"{element_id}. "
                "Take a fresh desktop_snapshot()."
            ),
        }

    control = entry["control"]

    try:

        control.set_focus()

        return {
            "status": "focused",
            "element_id": element_id,
            "name": _safe_control_text(
                control
            ),
            "role": _safe_control_type(
                control
            ),
        }

    except Exception as e:

        return {
            "status": "error",
            "element_id": element_id,
            "error": str(e),
        }


# =========================================================
# CLICK UI ELEMENT
# =========================================================

@safe_tool("Click Desktop Element")
def desktop_click(
    element_id: str,
):

    """
    Click a semantic UI element.

    Uses UI Automation first.

    Coordinate clicking is only used as a fallback when the UIA
    control does not support a direct click method.
    """

    entry = _get_registered_element(
        element_id
    )

    if not entry:

        return {
            "status": "error",
            "error": (
                f"Unknown or expired element id: "
                f"{element_id}. "
                "Take a fresh desktop_snapshot()."
            ),
        }

    control = entry["control"]

    try:

        # Preferred: UI Automation click.
        try:

            control.click_input()

            return {
                "status": "clicked",
                "method": "uia",
                "element_id": element_id,
                "name": _safe_control_text(
                    control
                ),
                "role": _safe_control_type(
                    control
                ),
            }

        except Exception as direct_click_error:

            logging.debug(
                "[DESKTOP CLICK] "
                f"Direct UIA click failed: "
                f"{direct_click_error}"
            )

        # Fallback: invoke the control pattern.
        try:

            control.invoke()

            return {
                "status": "clicked",
                "method": "uia_invoke",
                "element_id": element_id,
            }

        except Exception:
            pass

        # Last resort: click the UIA bounding rectangle.
        rectangle = _safe_rect(control)

        if not rectangle:

            raise RuntimeError(
                "Element has no clickable UIA "
                "method or bounding rectangle."
            )

        import pyautogui

        x = (
            rectangle["left"]
            + rectangle["width"] // 2
        )

        y = (
            rectangle["top"]
            + rectangle["height"] // 2
        )

        pyautogui.click(x, y)

        return {
            "status": "clicked",
            "method": "coordinate_fallback",
            "element_id": element_id,
            "x": x,
            "y": y,
        }

    except Exception as e:

        return {
            "status": "error",
            "element_id": element_id,
            "error": str(e),
        }


# =========================================================
# TYPE INTO UI ELEMENT
# =========================================================

@safe_tool("Type Into Desktop Element")
def desktop_type(
    element_id: str,
    text: str,
    clear_existing: bool = False,
):

    """
    Type text into a semantic UI element.

    Preferred strategy:

        1. Focus UIA element
        2. Use UIA Edit control
        3. Fallback to clipboard paste

    The clipboard fallback is more reliable than pyautogui.write()
    for Unicode and special characters.
    """

    entry = _get_registered_element(
        element_id
    )

    if not entry:

        return {
            "status": "error",
            "error": (
                f"Unknown or expired element id: "
                f"{element_id}. "
                "Take a fresh desktop_snapshot()."
            ),
        }

    control = entry["control"]

    try:

        control.set_focus()

        if clear_existing:

            import pyautogui

            pyautogui.hotkey(
                "ctrl",
                "a",
            )

        # Best option for standard Windows Edit controls.
        try:

            control.set_edit_text(text)

            return {
                "status": "typed",
                "method": "uia_set_edit_text",
                "element_id": element_id,
                "text_length": len(text),
            }

        except Exception as edit_error:

            logging.debug(
                "[DESKTOP TYPE] "
                f"set_edit_text failed: "
                f"{edit_error}"
            )

        # Reliable fallback for most focused text controls.
        import pyperclip
        import pyautogui

        pyperclip.copy(text)

        pyautogui.hotkey(
            "ctrl",
            "v",
        )

        return {
            "status": "typed",
            "method": "clipboard_paste",
            "element_id": element_id,
            "text_length": len(text),
        }

    except Exception as e:

        return {
            "status": "error",
            "element_id": element_id,
            "error": str(e),
        }


# =========================================================
# READ UI ELEMENT VALUE
# =========================================================

@safe_tool("Read Desktop Element")
def desktop_get_value(
    element_id: str,
):

    """
    Read the current value of a UI element.
    """

    entry = _get_registered_element(
        element_id
    )

    if not entry:

        return {
            "status": "error",
            "error": (
                f"Unknown or expired element id: "
                f"{element_id}. "
                "Take a fresh desktop_snapshot()."
            ),
        }

    control = entry["control"]

    try:

        return {
            "status": "success",
            "element_id": element_id,
            "role": _safe_control_type(
                control
            ),
            "name": _safe_control_text(
                control
            ),
            "value": _safe_current_value(
                control
            ),
        }

    except Exception as e:

        return {
            "status": "error",
            "element_id": element_id,
            "error": str(e),
        }


# =========================================================
# LEGACY / COMPATIBILITY WINDOW FUNCTIONS
# =========================================================

def try_popen(executable: str):

    try:

        subprocess.Popen(
            executable,
            shell=True,
        )

        return True

    except Exception:

        return False


# =========================================================
# LAUNCH APPLICATION
# =========================================================

@safe_tool("Launch application")
def launch_application(
    app_name: str,
):

    logging.info(
        f"[LAUNCH APP] {app_name}"
    )

    key = (
        app_name
        .lower()
        .strip()
    )

    candidate = APP_MAP.get(
        key,
        key,
    )

    # =====================================================
    # URI LAUNCH
    # =====================================================

    if candidate.startswith(
        (
            "ms-settings:",
            "http://",
            "https://",
        )
    ):

        try:

            subprocess.Popen(
                f'explorer.exe "{candidate}"',
                shell=True,
            )

            match = (
                _wait_for_matching_window(
                    app_name
                )
            )

            if match:

                focus_window.func(
                    match["title"]
                )

            return {
                "status": "launched",
                "app": app_name,
                "method": "uri",
                "uri": candidate,
                "matching_window": match,
                "active_window": (
                    get_active_window.func()
                ),
            }

        except Exception as e:

            return (
                f"URI launch failed for "
                f"{candidate}: {e}"
            )

    # =====================================================
    # STRATEGY 1:
    # EXECUTABLE EXISTS IN PATH
    # =====================================================

    try:

        resolved = shutil.which(
            candidate
        )

        if resolved:

            subprocess.Popen(
                [candidate]
            )

            time.sleep(1)

            return {
                "status": "launched",
                "app": app_name,
                "method": "path",
                "resolved_path": resolved,
                "active_window": (
                    get_active_window.func()
                ),
            }

    except Exception:

        pass

    # =====================================================
    # STRATEGY 2:
    # WINDOWS START COMMAND
    # =====================================================

    try:

        result = subprocess.run(
            f'start "" "{app_name}"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:

            time.sleep(1)

            return {
                "status": "launch_requested",
                "app": app_name,
                "method": "windows_start",
                "active_window": (
                    get_active_window.func()
                ),
            }

    except Exception:

        pass

    # =====================================================
    # STRATEGY 3:
    # SEARCH INSTALLED APPS
    # =====================================================

    try:

        cmd = [
            "powershell",
            "-Command",
            "Get-StartApps | ConvertTo-Json",
        ]

        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )

        apps = json.loads(
            res.stdout
            or "[]"
        )

        if isinstance(
            apps,
            dict,
        ):

            apps = [apps]

        matches = [
            app
            for app in apps
            if key
            in app.get(
                "Name",
                "",
            ).lower()
        ]

        if matches:

            appid = matches[0]["AppID"]

            subprocess.Popen(
                (
                    "explorer.exe "
                    f"shell:AppsFolder\\{appid}"
                ),
                shell=True,
            )

            time.sleep(1)

            return {
                "status": "launched",
                "app": matches[0]["Name"],
                "method": "appid",
                "appid": appid,
                "active_window": (
                    get_active_window.func()
                ),
            }

    except Exception:

        pass

    return (
        f"Could not launch '{app_name}'. "
        "The application may not be installed."
    )


# =========================================================
# SEARCH INSTALLED APPS
# =========================================================

@safe_tool("Search Installed Apps")
def search_installed_apps(
    query: str,
):

    """
    Search Windows installed apps via PowerShell.
    """

    try:

        cmd = [
            "powershell",
            "-Command",
            "Get-StartApps | ConvertTo-Json",
        ]

        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        data = json.loads(
            res.stdout
            or "[]"
        )

        if isinstance(
            data,
            dict,
        ):

            data = [data]

        matches = [
            app
            for app in data
            if query.lower()
            in app.get(
                "Name",
                "",
            ).lower()
        ]

        return str(
            matches[:10]
        )

    except Exception as e:

        return (
            f"Error Searching Apps: "
            f"{str(e)}"
        )


# =========================================================
# LAUNCH APP BY ID
# =========================================================

@safe_tool("Launch app by ID")
def launch_app_by_id(
    appid: str,
):

    """
    Launch a Windows app using an exact AppID returned by
    Search Installed Apps.
    """

    try:

        subprocess.Popen(
            (
                "explorer.exe "
                f"shell:AppsFolder\\{appid}"
            ),
            shell=True,
        )

        time.sleep(1)

        return {
            "status": "launch_requested",
            "appid": appid,
            "active_window": (
                get_active_window.func()
            ),
            "next_step": (
                "Take a desktop_snapshot() "
                "and navigate inside the active app. "
                "Do not relaunch the same AppID."
            ),
        }

    except Exception as e:

        return (
            f"Launch failed: {e}"
        )


# =========================================================
# EXECUTE SHELL COMMAND
# =========================================================

@safe_tool("Execute Shell Command")
def execute_shell_command(
    command: str,
):

    """
    Run a shell command after explicit user confirmation.
    """

    try:

        import ctypes

        msg = (
            "Do you want to allow the AI to execute "
            "the following shell command?\n\n"
            f"{command}"
        )

        flags = (
            0x00000004
            | 0x00000020
            | 0x00040000
        )

        response = (
            ctypes.windll.user32.MessageBoxW(
                0,
                msg,
                "Command Execution Confirmation",
                flags,
            )
        )

        if response != 6:

            return (
                "User cancelled the command."
            )

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )

        return (
            result.stdout
            or result.stderr
            or (
                "Command executed successfully "
                "with no output."
            )
        )

    except Exception as e:

        return (
            f"Command Execution Failed: "
            f"{str(e)}"
        )


# =========================================================
# FOCUS WINDOW BY TITLE
# =========================================================

@safe_tool("Focus window by title")
def focus_window(
    title: str,
):

    """
    Focus a window by partial title.
    """

    try:

        import pygetwindow as gw

        match = _find_best_window(
            title
        )

        if not match:

            return (
                f"No window found with title: "
                f"{title}"
            )

        windows = (
            gw.getWindowsWithTitle(
                match["title"]
            )
        )

        if not windows:

            return (
                "Window disappeared before focus: "
                f"{match['title']}"
            )

        win = windows[0]

        if win.isMinimized:
            win.restore()

        win.activate()

        time.sleep(0.3)

        return {
            "status": "focused",
            "requested_title": title,
            "active_window": (
                get_active_window.func()
            ),
        }

    except Exception as e:

        return (
            f"Focus failed: {e}"
        )


# =========================================================
# LAUNCH OR FOCUS APPLICATION
# =========================================================

@safe_tool("Launch or focus application")
def launch_or_focus_application(
    app_name: str,
):

    """
    Focus an existing app window.

    If it does not exist, launch it once.

    After this tool returns, the agent should use
    desktop_snapshot() before navigating.
    """

    key = (
        app_name
        .lower()
        .strip()
    )

    if (
        key in APP_MAP
        and APP_MAP[key].startswith(
            (
                "ms-settings:",
                "http://",
                "https://",
            )
        )
    ):

        launch_result = (
            launch_application.func(
                app_name
            )
        )

        return {
            "status": (
                "launched_known_uri_app"
            ),
            "app": app_name,
            "launch_result": launch_result,
            "active_window": (
                get_active_window.func()
            ),
            "next_step": (
                "Call desktop_snapshot() "
                "and navigate using semantic UI "
                "elements. Do not relaunch."
            ),
        }

    try:

        existing = _matching_windows(
            key
        )

        if existing:

            focus_window.func(
                existing[0]["title"]
            )

            return {
                "status": "focused_existing",
                "app": app_name,
                "window": (
                    get_active_window.func()
                ),
                "next_step": (
                    "Call desktop_snapshot() "
                    "before interacting."
                ),
            }

    except Exception:

        logging.exception(
            "launch_or_focus window check failed"
        )

    launch_result = (
        launch_application.func(
            app_name
        )
    )

    time.sleep(2)

    matches = []

    try:

        matches = _matching_windows(
            key
        )

    except Exception:

        logging.exception(
            "launch_or_focus post-launch "
            "window check failed"
        )

    return {
        "status": "launched_or_requested",
        "app": app_name,
        "launch_result": launch_result,
        "matching_windows": matches[:5],
        "active_window": (
            get_active_window.func()
        ),
        "next_step": (
            "Call desktop_snapshot() before "
            "navigating inside the app. "
            "Do not launch the same app again."
        ),
    }


# =========================================================
# OPEN WINDOWS SETTINGS PAGE
# =========================================================

@safe_tool("Open Windows Settings page")
def open_settings_page(
    page: str,
):

    """
    Open a known Windows Settings page directly by name.
    """

    key = (
        page
        .lower()
        .strip()
    )

    uri = SETTINGS_PAGE_URIS.get(
        key
    )

    if not uri:

        return {
            "status": (
                "unknown_settings_page"
            ),
            "page": page,
            "known_pages": sorted(
                SETTINGS_PAGE_URIS.keys()
            ),
        }

    try:

        subprocess.Popen(
            f'explorer.exe "{uri}"',
            shell=True,
        )

        match = (
            _wait_for_matching_window(
                "settings"
            )
        )

        if match:

            focus_window.func(
                match["title"]
            )

        time.sleep(1)

        return {
            "status": "opened",
            "page": page,
            "uri": uri,
            "active_window": (
                get_active_window.func()
            ),
            "next_step": (
                "Call desktop_snapshot() "
                "to inspect the Settings UI."
            ),
        }

    except Exception as e:

        return (
            f"Open settings page failed: {e}"
        )


# =========================================================
# LIST OPEN WINDOWS
# =========================================================

@safe_tool("List open windows")
def list_windows():

    """
    List visible desktop windows with titles and geometry.
    """

    try:

        return _visible_windows()[:50]

    except Exception as e:

        return (
            f"List windows failed: {e}"
        )


# =========================================================
# GET ACTIVE WINDOW
# =========================================================

@safe_tool("Get active window")
def get_active_window():

    """
    Return the currently active window title and geometry.
    """

    try:

        import pygetwindow as gw

        win = (
            gw.getActiveWindow()
        )

        if not win:

            return (
                "No active window detected."
            )

        return {
            "title": win.title,
            "left": win.left,
            "top": win.top,
            "width": win.width,
            "height": win.height,
            "is_minimized": win.isMinimized,
            "is_maximized": win.isMaximized,
        }

    except Exception as e:

        return (
            f"Get active window failed: {e}"
        )


# =========================================================
# INSPECT ACTIVE WINDOW TEXT
# =========================================================
@safe_tool("Inspect active window text")
def inspect_active_window_text(query: str = "", max_items: int = 80):
    try:


        max_items = max(10, min(int(max_items), 200))
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "No active window detected."
        window = Desktop(backend="uia").window(handle=hwnd)
        title = window.window_text()

        items = []
        matches = []

        query_lower = (
            query
            .lower()
            .strip()
        )

        controls = (
            window.descendants()
        )

        for control in controls:

            if len(items) >= max_items:
                break

            if not _safe_is_visible(
                control
            ):
                continue

            text = (
                _safe_control_text(
                    control
                )
            )

            if not text:
                continue

            control_type = (
                _safe_control_type(
                    control
                )
            )

            item = {
                "text": text,
                "type": control_type,
            }

            items.append(item)

            if (
                query_lower
                and query_lower
                in text.lower()
            ):

                matches.append(item)

        return {
            "title": title,
            "query": query,
            "query_found": (
                bool(matches)
                if query_lower
                else None
            ),
            "matches": matches[:20],
            "visible_text": items,
        }

    except Exception as e:

        return (
            "Inspect active window text "
            f"failed: {e}"
        )


# =========================================================
# WAIT
# =========================================================

@safe_tool("Wait briefly")
def wait_seconds(
    seconds: float = 1.0,
):

    """
    Wait for UI transitions, app launches,
    menus, or search results.
    """

    seconds = max(
        0.1,
        min(
            float(seconds),
            10,
        ),
    )

    time.sleep(
        seconds
    )

    return (
        f"Waited {seconds:.1f}s"
    )


# src/tools/system_tools.py

@safe_tool("Find and click UI element by text")
def find_and_click_element(query: str, control_type_hint: str = None):
    """Locate a visible element by name via UI Automation and click its center.
    Use this before typing into any field you haven't just clicked."""
    from pywinauto import Desktop
    window = Desktop(backend="uia").get_active()
    q = query.lower().strip()

    candidates = []
    for control in window.descendants():
        try:
            if not control.is_visible():
                continue
            name = (control.window_text() or "").strip()
            ctype = control.friendly_class_name()
        except Exception:
            continue
        if control_type_hint and control_type_hint.lower() not in ctype.lower():
            continue
        if q in name.lower() or q in ctype.lower():
            r = control.rectangle()
            candidates.append({"name": name, "control_type": ctype,
                                "center_x": (r.left+r.right)//2, "center_y": (r.top+r.bottom)//2})

    if not candidates:
        return {"status": "not_found", "query": query,
                "hint": "Try analyze_screen_with_vision if not UIA-visible."}

    target = candidates[0]
    pyautogui.click(x=target["center_x"], y=target["center_y"])
    time.sleep(0.2)
    return {"status": "clicked", "query": query, "matched": target}
