# src/tools/desktop_browser/desktop_browser_cli.py

import shlex
import subprocess
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    success: bool
    output: str
    error: Optional[str] = None
    return_code: int = 0


class AgentBrowserCLI:
    """
    Thin wrapper over the `agent-browser` CLI (vercel-labs/agent-browser).

    Unlike browser_cli.PlaywrightCLI (which drives a normal web browser),
    this targets a Chrome DevTools Protocol port exposed by an Electron
    desktop app (Slack, VS Code, Discord, Figma, Notion, Spotify, ...),
    launched with --remote-debugging-port=<port>. Every command after
    `connect` is scoped to a named --session so multiple Electron apps
    can be automated concurrently without cross-talk, same idea as the
    -s/--session flag PlaywrightCLI uses.

    Requires the `agent-browser` binary on PATH:
        npm i -g agent-browser && agent-browser install
    """

    def __init__(
        self,
        session: str = "desktop-agent",
        binary: str = "agent-browser",
        timeout: int = 60000,
    ):
        self.session = session
        self.binary = binary
        self.timeout = timeout

    def run(self, args: str) -> CLIResult:
        cmd = [self.binary] + shlex.split(args)

        try:
            logger.info(f"[AgentBrowserCLI] Running: {' '.join(cmd)}")

            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                timeout=self.timeout / 1000,
            )

            output = (process.stdout or "").strip()
            error = (process.stderr or "").strip()

            return CLIResult(
                success=process.returncode == 0,
                output=output,
                error=error if error else None,
                return_code=process.returncode,
            )

        except subprocess.TimeoutExpired:
            return CLIResult(
                success=False,
                output="",
                error="Timeout expired",
                return_code=-1,
            )
        except Exception as e:
            logger.exception("[AgentBrowserCLI] run failed")
            return CLIResult(
                success=False,
                output="",
                error=str(e),
                return_code=-1,
            )

    # -------------------------
    # Connection lifecycle
    # -------------------------

    def connect(self, port: int) -> CLIResult:
        """Attach this session to an already-running app's CDP port."""
        return self.run(f"connect {port} --session {self.session}")

    def disconnect(self) -> CLIResult:
        return self.run(f"close --session {self.session}")

    # -------------------------
    # Snapshot / interaction (mirrors PlaywrightCLI's shape)
    # -------------------------

    def snapshot(self, interactive_only: bool = True) -> CLIResult:
        flag = "-i" if interactive_only else ""
        return self.run(f"snapshot {flag} --session {self.session}".strip())

    def click(self, element: str) -> CLIResult:
        return self.run(f"click {element} --session {self.session}")

    def fill(self, element: str, text: str) -> CLIResult:
        return self.run(
            f"fill {element} {shlex.quote(text)} --session {self.session}"
        )

    def type_at_focus(self, text: str) -> CLIResult:
        """Type at whatever currently has focus, no selector (custom
        input widgets that don't behave like normal <input> elements)."""
        return self.run(
            f"keyboard type {shlex.quote(text)} --session {self.session}"
        )

    def press(self, key: str) -> CLIResult:
        return self.run(f"press {key} --session {self.session}")

    def tab_list(self) -> CLIResult:
        return self.run(f"tab --session {self.session}")

    def tab_switch(self, target: str) -> CLIResult:
        """target can be a numeric index or a --url pattern string."""
        return self.run(f"tab {target} --session {self.session}")

    def screenshot(self, path: str) -> CLIResult:
        return self.run(f"screenshot {shlex.quote(path)} --session {self.session}")

    def close(self) -> CLIResult:
        return self.run(f"close --session {self.session}")
