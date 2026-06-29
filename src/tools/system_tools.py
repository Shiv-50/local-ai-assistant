import logging
import subprocess
import webbrowser
import json
import shutil
from src.tools.base import safe_tool

APP_MAP = {
    "chrome": "start chrome",
    "vscode": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "explorer": "explorer",
    "terminal": "wt",
    "cmd": "cmd",
}
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

    # =====================================================
    # STRATEGY 1:
    # executable exists in PATH
    # =====================================================

    try:

        resolved = shutil.which(candidate)

        if resolved:

            subprocess.Popen([candidate])

            return f"Successfully launched {app_name} (resolved path: {resolved})"

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

            return f"Successfully launched {app_name} via Windows start command"

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

            return f"Successfully launched {matches[0]['Name']} (AppID: {appid})"

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
    """Launch app using Windows AppID."""
    try:
        subprocess.Popen(f'explorer.exe shell:AppsFolder\\{appid}', shell=True)
        return f"Launched AppID {appid}"
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

        windows = gw.getWindowsWithTitle(title)
        if not windows:
            return f"No window found with title: {title}"

        windows[0].activate()
        return f"Focused {title}"
    except Exception as e:
        return f"Focus failed: {e}"