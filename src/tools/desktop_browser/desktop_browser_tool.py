# src/tools/desktop_browser/desktop_browser_tool.py

import glob
import logging
import os
import shutil
import socket
import subprocess
import time
from typing import Dict, Any, List, Optional

from src.tools.base import safe_tool
from .desktop_browser_cli import AgentBrowserCLI
from .desktop_browser_state import DesktopAppState

logger = logging.getLogger(__name__)


# =========================================================
# KNOWN ELECTRON APPS (Windows launch targets)
# =========================================================
#
# agent-browser's `electron` skill just needs the app started with
# --remote-debugging-port=<port> — every Electron app supports this
# since it's built into Chromium.
#
# IMPORTANT: on Windows, only VS Code's `code` typically ends up on
# PATH. Slack/Discord/Figma/Notion/WhatsApp install per-user under
# %LOCALAPPDATA% (Spotify under %APPDATA%, and Spotify is NOT Electron
# at all — it's CEF-based, so it's intentionally excluded here) with
# no PATH entry. `path_globs` below are checked with os.path.expandvars
# + glob, so version-numbered folders (Discord's `app-1.0.9123`, etc.)
# resolve without hardcoding a version. Order matters: first existing
# match wins.

KNOWN_ELECTRON_APPS: Dict[str, Dict[str, Any]] = {
    "slack": {
        "exe": "slack",
        "port": 9222,
        "path_globs": [r"%LOCALAPPDATA%\slack\slack.exe"],
    },
    "vscode": {
        "exe": "code",
        "port": 9223,
        "path_globs": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"],
    },
    "vs code": {
        "exe": "code",
        "port": 9223,
        "path_globs": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"],
    },
    "discord": {
        "exe": "discord",
        "port": 9224,
        "path_globs": [r"%LOCALAPPDATA%\Discord\app-*\Discord.exe"],
    },
    "figma": {
        "exe": "figma",
        "port": 9225,
        "path_globs": [r"%LOCALAPPDATA%\Figma\Figma.exe"],
    },
    "notion": {
        "exe": "notion",
        "port": 9226,
        "path_globs": [r"%LOCALAPPDATA%\Programs\Notion\Notion.exe"],
    },
    "whatsapp": {
        "exe": "whatsapp",
        "port": 9228,
        "path_globs": [
            r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe",
            r"%LOCALAPPDATA%\WhatsApp\app-*\WhatsApp.exe",
        ],
    },
    "teams": {
        "exe": "teams",
        "port": 9229,
        "path_globs": [r"%LOCALAPPDATA%\Microsoft\Teams\current\Teams.exe"],
    },
    "obsidian": {
        "exe": "obsidian",
        "port": 9230,
        "path_globs": [r"%LOCALAPPDATA%\Obsidian\Obsidian.exe"],
    },
    "postman": {
        "exe": "postman",
        "port": 9231,
        "path_globs": [r"%LOCALAPPDATA%\Postman\Postman.exe"],
    },
    "signal": {
        "exe": "signal",
        "port": 9232,
        "path_globs": [r"%LOCALAPPDATA%\Programs\signal-desktop\Signal.exe"],
    },
    "github desktop": {
        "exe": "github desktop",
        "port": 9233,
        "path_globs": [r"%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe"],
    },
    # NOTE: Spotify is intentionally NOT listed — it is not an Electron
    # app (CEF/native hybrid), so agent-browser's electron workflow
    # cannot automate it. Route Spotify requests elsewhere.
}


def _expand_glob(pattern: str) -> Optional[str]:
    expanded = os.path.expandvars(pattern)
    matches = sorted(glob.glob(expanded), reverse=True)  # newest version-ish first
    return matches[0] if matches else None


def _resolve_via_known_paths(app_key: str) -> Optional[str]:
    known = KNOWN_ELECTRON_APPS.get(app_key)
    if not known:
        return None
    for pattern in known.get("path_globs", []):
        hit = _expand_glob(pattern)
        if hit and os.path.isfile(hit):
            return hit
    return None


def _resolve_via_start_menu(app_name: str) -> Optional[str]:
    """
    Last-resort fallback: resolve a Start Menu .lnk shortcut's target
    exe via PowerShell's WScript.Shell COM object. Catches anything not
    covered by the known-path table above (custom install dirs, apps
    not yet added to KNOWN_ELECTRON_APPS, etc.) the same way
    system_tools.search_installed_apps falls back to Get-StartApps.
    """
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject WScript.Shell
$roots = @(
    "$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs",
    "$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs"
)
$matches = @()
foreach ($root in $roots) {{
    Get-ChildItem -Path $root -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue |
        Where-Object {{ $_.BaseName -like "*{app_name}*" }} |
        ForEach-Object {{
            $target = $shell.CreateShortcut($_.FullName).TargetPath
            if ($target -and (Test-Path $target)) {{ $matches += $target }}
        }}
}}
$matches | Select-Object -First 1
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        hit = (result.stdout or "").strip()
        return hit if hit and os.path.isfile(hit) else None
    except Exception:
        logger.exception("[DesktopAppTool] Start Menu shortcut lookup failed")
        return None


def _resolve_exe(app_name: str, explicit_exe: Optional[str] = None) -> tuple[Optional[str], List[str]]:
    """Try shutil.which -> known install-path globs -> Start Menu shortcut.
    Returns (resolved_path_or_None, strategies_tried) for diagnostics."""
    key = app_name.lower().strip()
    tried = []

    candidate = explicit_exe or (KNOWN_ELECTRON_APPS.get(key, {}).get("exe") or key)

    tried.append(f"PATH lookup for '{candidate}'")
    hit = shutil.which(candidate)
    if hit:
        return hit, tried

    tried.append(f"known install-path patterns for '{key}'")
    hit = _resolve_via_known_paths(key)
    if hit:
        return hit, tried

    tried.append(f"Start Menu shortcut search for '{app_name}'")
    hit = _resolve_via_start_menu(app_name)
    if hit:
        return hit, tried

    return None, tried


def detect_installed_electron_apps() -> Dict[str, str]:
    """Check every app in KNOWN_ELECTRON_APPS and return {name: resolved_path}
    for ones actually found on this machine, without launching anything."""
    found = {}
    for key in KNOWN_ELECTRON_APPS:
        hit, _ = _resolve_exe(key)
        if hit:
            found[key] = hit
    return found


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_is_open(port):
            return True
        time.sleep(0.3)
    return False


class DesktopAppTool:
    """
    Automates Electron-based desktop apps via agent-browser + CDP.

    Scope: ONLY apps built on Electron (Slack, VS Code, Discord, Figma,
    Notion, WhatsApp, Teams, and similar Chromium-shell apps). Spotify
    is explicitly NOT supported (not Electron). For anything else
    (native Win32 apps, Settings, arbitrary unknown apps) keep using
    the existing system_tools / vision_tools pipeline — this tool
    intentionally doesn't try to cover that ground.
    """

    def __init__(self, session: str = "desktop-agent"):
        self.cli = AgentBrowserCLI(session=session)
        self.state = DesktopAppState(session=session)

    # -------------------------
    # Launch + connect
    # -------------------------

    def launch_and_connect(
        self,
        app_name: str,
        port: Optional[int] = None,
        executable_path: Optional[str] = None,
    ):
        key = app_name.lower().strip()
        known = KNOWN_ELECTRON_APPS.get(key)
        resolved_port = port or (known["port"] if known else 9222)

        if executable_path:
            resolved_path = executable_path if os.path.isfile(executable_path) else None
            tried = [f"caller-provided path '{executable_path}'"]
        else:
            resolved_path, tried = _resolve_exe(app_name)

        if not resolved_path:
            return {
                "status": "launch_failed",
                "app": app_name,
                "error": (
                    f"Could not locate an executable for '{app_name}'. Tried: "
                    f"{'; '.join(tried)}. Call detect_installed_electron_apps to "
                    f"see what's actually present on this machine, or pass "
                    f"executable_path with the exact .exe location if you know it."
                ),
            }

        if _port_is_open(resolved_port):
            logger.info(
                "[DesktopAppTool] Port %s already open, assuming '%s' is "
                "already running with remote debugging enabled.",
                resolved_port, app_name,
            )
        else:
            try:
                subprocess.Popen(
                    [resolved_path, f"--remote-debugging-port={resolved_port}"],
                    shell=False,
                )
            except Exception as e:
                return {
                    "status": "launch_failed",
                    "app": app_name,
                    "error": f"Failed to launch '{resolved_path}': {e}",
                }

            if not _wait_for_port(resolved_port):
                return {
                    "status": "launch_failed",
                    "app": app_name,
                    "port": resolved_port,
                    "resolved_path": resolved_path,
                    "error": (
                        f"App launched but port {resolved_port} never opened. "
                        f"If '{app_name}' was already running WITHOUT the "
                        f"debugging flag, fully quit it first (check the "
                        f"system tray) and retry."
                    ),
                }

        res = self.cli.connect(resolved_port)

        self.state.app_name = app_name
        self.state.port = resolved_port
        self.state.connected = res.success
        self.state.add_step(f"connect:{app_name}:{resolved_port}")

        snap = self.cli.snapshot() if res.success else None
        if snap:
            self.state.update_from_snapshot(snap.output)

        return self._format(res, snap)

    def connect_existing(self, port: int):
        """Attach to an app that's already running with the debugging
        port open (skip launch entirely)."""
        res = self.cli.connect(port)

        self.state.port = port
        self.state.connected = res.success
        self.state.add_step(f"connect:existing:{port}")

        snap = self.cli.snapshot() if res.success else None
        if snap:
            self.state.update_from_snapshot(snap.output)

        return self._format(res, snap)

    # -------------------------
    # Core actions (same shape as BrowserTool)
    # -------------------------

    def snapshot(self):
        res = self.cli.snapshot()
        self.state.update_from_snapshot(res.output)
        return self._format(res)

    def click(self, element: str):
        res = self.cli.click(element)
        self.state.add_step(f"click:{element}")

        snap = self.cli.snapshot()
        self.state.update_from_snapshot(snap.output)
        return self._format(res, snap)

    def fill(self, element: str, text: str):
        res = self.cli.fill(element, text)
        self.state.add_step(f"fill:{element}")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def type_at_focus(self, text: str):
        res = self.cli.type_at_focus(text)
        self.state.add_step("type_at_focus")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def press(self, key: str):
        res = self.cli.press(key)
        self.state.add_step(f"press:{key}")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def switch_window(self, target: str):
        """target: numeric tab index, or a URL/title pattern. Electron
        apps commonly have multiple windows/webviews (e.g. Slack's
        main window vs a huddle popup)."""
        res = self.cli.tab_switch(target)
        self.state.add_step(f"tab:{target}")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def list_windows(self):
        res = self.cli.tab_list()
        return self._format(res)

    def close(self):
        res = self.cli.close()
        self.state.add_step("close")
        self.state.connected = False
        return self._format(res)

    # -------------------------
    # Output normalization
    # -------------------------

    def _format(self, res, snapshot=None) -> Dict[str, Any]:
        return {
            "success": res.success,
            "output": res.output[:2000],
            "error": res.error,
            "state": {
                "app": self.state.app_name,
                "port": self.state.port,
                "connected": self.state.connected,
                "last_action": self.state.last_action,
                "completed_steps": self.state.completed_steps[-10:],
            },
            "snapshot": snapshot.output[:10000] if snapshot else None,
        }

    def describe_state(self) -> str:
        s = self.state
        if not s.connected:
            return (
                "CURRENT DESKTOP APP STATE: Not connected to any app. "
                "Call launch_and_connect_electron_app first, or "
                "detect_installed_electron_apps if unsure what's installed."
            )
        steps = "; ".join(s.completed_steps[-5:]) or "none"
        return (
            "CURRENT DESKTOP APP STATE:\n"
            f"- App: {s.app_name}\n"
            f"- Port: {s.port}\n"
            f"- Last action: {s.last_action}\n"
            f"- Recent steps this session: {steps}\n"
            "If this app is already connected, do NOT call "
            "launch_and_connect_electron_app again. Call "
            "snapshot_electron_app to see current state and proceed."
        )


# =========================================================
# LANGCHAIN TOOL WRAPPERS
# =========================================================

def get_tools(app: DesktopAppTool):

    @safe_tool("Detect installed Electron desktop apps")
    def detect_installed_electron_apps_tool():
        """
        Check known Electron apps (slack, vscode, discord, figma,
        notion, whatsapp, teams, obsidian, postman, signal, github
        desktop) against this machine's PATH, common install
        locations, and Start Menu shortcuts. Returns which are
        actually present, with resolved paths. Call this BEFORE asking
        the user for an exact path -- it's often unnecessary.
        """
        found = detect_installed_electron_apps()
        if not found:
            return {
                "found": {},
                "note": (
                    "None of the known Electron apps were found via PATH, "
                    "common install locations, or Start Menu shortcuts. If "
                    "the user named a specific app not in that list, ask "
                    "for its executable path directly instead of retrying."
                ),
            }
        return {"found": found}

    @safe_tool("Launch and connect to an Electron desktop app")
    def launch_and_connect_electron_app(
        app_name: str, port: int = None, executable_path: str = None
    ):
        """
        Launch a known Electron desktop app (slack, vscode, discord,
        figma, notion, whatsapp, teams, obsidian, postman, signal,
        github desktop) with remote debugging enabled, or attach if
        it's already running that way. Resolution order: PATH, known
        install-path patterns, Start Menu shortcuts. If all fail, or
        for an app not in the known list, pass executable_path with
        the exact .exe location. Only works for Electron/Chromium-shell
        apps -- NOT Spotify (not Electron) or native Win32 apps.
        """
        return app.launch_and_connect(app_name, port, executable_path)

    @safe_tool("Connect to an already-running Electron app by port")
    def connect_electron_app_by_port(port: int):
        """Attach to an app already running with --remote-debugging-port."""
        return app.connect_existing(port)

    @safe_tool("Snapshot the connected Electron app")
    def snapshot_electron_app():
        """Get the accessibility-tree snapshot with element refs (@e1, @e2, ...)."""
        return app.snapshot()

    @safe_tool("Click an element in the connected Electron app")
    def click_electron_app(element: str):
        """Click using an element ref from the most recent snapshot (e.g. @e5)."""
        return app.click(element)

    @safe_tool("Fill a field in the connected Electron app")
    def fill_electron_app(element: str, text: str):
        """Clear and fill a field using an element ref from the snapshot."""
        return app.fill(element, text)

    @safe_tool("Type at current focus in the connected Electron app")
    def type_at_focus_electron_app(text: str):
        """Type text at whatever currently has focus, no selector needed.
        Use for custom input widgets that don't behave like normal inputs."""
        return app.type_at_focus(text)

    @safe_tool("Press a key in the connected Electron app")
    def press_key_electron_app(key: str):
        """Press a key, e.g. Enter, Tab, Control+a."""
        return app.press(key)

    @safe_tool("List windows/webviews in the connected Electron app")
    def list_windows_electron_app():
        """List all targets (windows, webviews) the app currently exposes."""
        return app.list_windows()

    @safe_tool("Switch window/webview in the connected Electron app")
    def switch_window_electron_app(target: str):
        """Switch to a window/webview by numeric index or URL pattern."""
        return app.switch_window(target)

    @safe_tool("Close the connected Electron app session")
    def close_electron_app():
        """Disconnect agent-browser from the app (does not quit the app itself)."""
        return app.close()

    return [
        detect_installed_electron_apps_tool,
        launch_and_connect_electron_app,
        connect_electron_app_by_port,
        snapshot_electron_app,
        click_electron_app,
        fill_electron_app,
        type_at_focus_electron_app,
        press_key_electron_app,
        list_windows_electron_app,
        switch_window_electron_app,
        close_electron_app,
    ]