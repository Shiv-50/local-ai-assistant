import logging
import subprocess
import webbrowser
import json
import shutil
import time
from src.tools.base import safe_tool

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
    "terminal": ("Terminal", "Windows PowerShell", "Command Prompt"),
    "cmd": ("Command Prompt",),
}


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


def _window_match_score(query: str, title: str) -> int:
    query_lower = query.lower().strip()
    title_lower = title.lower().strip()
    aliases = APP_TITLE_ALIASES.get(query_lower, (query,))

    for alias in aliases:
        alias_lower = alias.lower()
        if title_lower == alias_lower:
            return 100
        if title_lower.startswith(alias_lower + " -"):
            return 80

    if query_lower and query_lower in title_lower:
        return 20

    return 0


def _matching_windows(query: str, allow_browser_page_titles: bool = False):
    query = query.lower().strip()
    allow_browser_page_titles = allow_browser_page_titles or _is_browser_app(query)

    scored = []
    for win in _visible_windows():
        title = win["title"]
        if _is_browser_page_title(title) and not allow_browser_page_titles:
            continue

        score = _window_match_score(query, title)
        if score:
            scored.append((score, win))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [win for _, win in scored]


def _find_best_window(title: str, allow_browser_page_titles: bool = False):
    matches = _matching_windows(
        title,
        allow_browser_page_titles=allow_browser_page_titles,
    )
    return matches[0] if matches else None


def _wait_for_matching_window(query: str, timeout: float = 4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        match = _find_best_window(query)
        if match:
            return match
        time.sleep(0.3)
    return None


def try_popen(executable: str):

    try:
        subprocess.Popen(
            executable,
            shell=True,
        )
        return True

    except Exception:
        return False


@safe_tool("Launch application")
def launch_application(app_name: str):

    logging.info(f"[LAUNCH APP] {app_name}")

    key = app_name.lower().strip()

    candidate = APP_MAP.get(key, key)

    if candidate.startswith(("ms-settings:", "http://", "https://")):
        try:
            subprocess.Popen(f'explorer.exe "{candidate}"', shell=True)
            match = _wait_for_matching_window(app_name)
            if match:
                focus_window.func(match["title"])
            return {
                "status": "launched",
                "app": app_name,
                "method": "uri",
                "uri": candidate,
                "matching_window": match,
                "active_window": get_active_window.func(),
            }
        except Exception as e:
            return f"URI launch failed for {candidate}: {e}"

    # =====================================================
    # STRATEGY 1:
    # executable exists in PATH
    # =====================================================

    try:

        resolved = shutil.which(candidate)

        if resolved:

            subprocess.Popen([candidate])
            time.sleep(1)

            return {
                "status": "launched",
                "app": app_name,
                "method": "path",
                "resolved_path": resolved,
                "active_window": get_active_window.func(),
            }

    except Exception:
        pass

    # =====================================================
    # STRATEGY 2:
    # use Windows start command
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
                "active_window": get_active_window.func(),
            }

    except Exception:
        pass

    # =====================================================
    # STRATEGY 3:
    # search installed apps
    # =====================================================

    try:

        cmd = [
            "powershell",
            "-Command",
            "Get-StartApps | ConvertTo-Json"
        ]

        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )

        apps = json.loads(res.stdout or "[]")

        if isinstance(apps, dict):
            apps = [apps]

        matches = [
            a for a in apps
            if key in a.get("Name", "").lower()
        ]

        if matches:

            appid = matches[0]["AppID"]

            subprocess.Popen(
                f'explorer.exe shell:AppsFolder\\{appid}',
                shell=True,
            )
            time.sleep(1)

            return {
                "status": "launched",
                "app": matches[0]["Name"],
                "method": "appid",
                "appid": appid,
                "active_window": get_active_window.func(),
            }

    except Exception:
        pass

    # =====================================================
    # FAILURE
    # =====================================================

    return f"Could not launch '{app_name}'. The application may not be installed."

@safe_tool("Search Installed Apps")
def search_installed_apps(query: str):
    """Search Windows installed apps via PowerShell."""
    try:
        cmd = ["powershell", "-Command", "Get-StartApps | ConvertTo-Json"]
        res = subprocess.run(cmd, capture_output=True, text=True)

        data = json.loads(res.stdout or "[]")
        if isinstance(data, dict):
            data = [data]

        matches = [
            a for a in data
            if query.lower() in a.get("Name", "").lower()
        ]

        return str(matches[:10])

    except Exception as e:
        return f"Error Searching Apps: {str(e)}"


@safe_tool("Launch app by ID")
def launch_app_by_id(appid: str):
    """Launch a Windows app using an exact AppID returned by Search Installed Apps."""
    try:
        subprocess.Popen(f'explorer.exe shell:AppsFolder\\{appid}', shell=True)
        time.sleep(1)
        return {
            "status": "launch_requested",
            "appid": appid,
            "active_window": get_active_window.func(),
            "next_step": "Navigate in the app if it is active. Do not relaunch the same AppID.",
        }
    except Exception as e:
        return f"Launch failed: {e}"


@safe_tool("Execute Shell Command")
def execute_shell_command(command: str):
    """Run a safe shell command."""
    try:
        import ctypes
        
        msg = f"Do you want to allow the AI to execute the following shell command?\n\n{command}"
        flags = 0x00000004 | 0x00000020 | 0x00040000
        
        response = ctypes.windll.user32.MessageBoxW(0, msg, "Command Execution Confirmation", flags)
        
        if response != 6:
            return "User cancelled the command."
            
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15
        )
        return result.stdout or result.stderr or "Command executed successfully with no output."
    except Exception as e:
        return f"Command Execution Failed: {str(e)}"


@safe_tool("Focus window by title")
def focus_window(title: str):
    """Focus a window by partial title."""
    try:
        import pygetwindow as gw

        match = _find_best_window(title)
        if not match:
            return f"No window found with title: {title}"

        windows = gw.getWindowsWithTitle(match["title"])
        if not windows:
            return f"Window disappeared before focus: {match['title']}"

        win = windows[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(0.3)
        return {
            "status": "focused",
            "requested_title": title,
            "active_window": get_active_window.func(),
        }
    except Exception as e:
        return f"Focus failed: {e}"


@safe_tool("Launch or focus application")
def launch_or_focus_application(app_name: str):
    """Focus an existing app window, otherwise launch the app once and report state."""
    key = app_name.lower().strip()

    if key in APP_MAP and APP_MAP[key].startswith(("ms-settings:", "http://", "https://")):
        launch_result = launch_application.func(app_name)
        return {
            "status": "launched_known_uri_app",
            "app": app_name,
            "launch_result": launch_result,
            "active_window": get_active_window.func(),
            "next_step": (
                "Navigate inside this app now. Do not focus browser tabs "
                "whose titles merely contain the app name."
            ),
        }

    try:
        existing = _matching_windows(key)
        if existing:
            focus_window.func(existing[0]["title"])
            return {
                "status": "focused_existing",
                "app": app_name,
                "window": get_active_window.func(),
            }
    except Exception:
        logging.exception("launch_or_focus window check failed")

    launch_result = launch_application.func(app_name)
    time.sleep(2)

    matches = []
    try:
        matches = _matching_windows(key)
    except Exception:
        logging.exception("launch_or_focus post-launch window check failed")

    return {
        "status": "launched_or_requested",
        "app": app_name,
        "launch_result": launch_result,
        "matching_windows": matches[:5],
        "active_window": get_active_window.func(),
        "next_step": (
            "If the intended window is active, navigate inside it. "
            "Do not launch the same app again unless the user asks."
        ),
    }


@safe_tool("Open Windows Settings page")
def open_settings_page(page: str):
    """Open a known Windows Settings page directly by name."""
    key = page.lower().strip()
    uri = SETTINGS_PAGE_URIS.get(key)

    if not uri:
        return {
            "status": "unknown_settings_page",
            "page": page,
            "known_pages": sorted(SETTINGS_PAGE_URIS.keys()),
        }

    try:
        subprocess.Popen(f'explorer.exe "{uri}"', shell=True)
        match = _wait_for_matching_window("settings")
        if match:
            focus_window.func(match["title"])

        time.sleep(1)
        return {
            "status": "opened",
            "page": page,
            "uri": uri,
            "active_window": get_active_window.func(),
            "visible_text": inspect_active_window_text.func(page, max_items=60),
        }
    except Exception as e:
        return f"Open settings page failed: {e}"


@safe_tool("List open windows")
def list_windows():
    """List visible desktop windows with titles and geometry."""
    try:
        return _visible_windows()[:50]
    except Exception as e:
        return f"List windows failed: {e}"


@safe_tool("Get active window")
def get_active_window():
    """Return the currently active window title and geometry."""
    try:
        import pygetwindow as gw

        win = gw.getActiveWindow()
        if not win:
            return "No active window detected."

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
        return f"Get active window failed: {e}"


@safe_tool("Inspect active window text")
def inspect_active_window_text(query: str = "", max_items: int = 80):
    """Read visible UI Automation text/control names from the active window."""
    try:
        from pywinauto import Desktop

        max_items = max(10, min(int(max_items), 200))
        window = Desktop(backend="uia").get_active()
        title = window.window_text()

        items = []
        query_lower = query.lower().strip()
        matches = []

        controls = window.descendants()
        for control in controls:
            if len(items) >= max_items:
                break

            try:
                if not control.is_visible():
                    continue
            except Exception:
                pass

            try:
                text = (control.window_text() or "").strip()
            except Exception:
                text = ""

            if not text:
                continue

            try:
                control_type = control.friendly_class_name()
            except Exception:
                control_type = "unknown"

            item = {
                "text": text,
                "type": control_type,
            }
            items.append(item)

            if query_lower and query_lower in text.lower():
                matches.append(item)

        return {
            "title": title,
            "query": query,
            "query_found": bool(matches) if query_lower else None,
            "matches": matches[:20],
            "visible_text": items,
        }

    except Exception as e:
        return f"Inspect active window text failed: {e}"


@safe_tool("Wait briefly")
def wait_seconds(seconds: float = 1.0):
    """Wait for UI transitions, app launches, menus, or search results."""
    seconds = max(0.1, min(float(seconds), 10.0))
    time.sleep(seconds)
    return f"Waited {seconds:.1f}s"
