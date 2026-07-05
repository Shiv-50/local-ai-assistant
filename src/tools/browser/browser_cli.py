# src/tools/browser/browser_cli.py

import subprocess
import shlex
import logging
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    success: bool
    output: str
    error: Optional[str] = None
    return_code: int = 0


class PlaywrightCLI:
    """
    Thin wrapper over Playwright CLI.
    Keeps execution isolated and safe for LLM tools.
    """

    def __init__(
        self,
        session: str = "agent",
        binary: str = "playwright-cli",
        timeout: int = 60000,
    ):
        self.session = session
        self.binary = binary
        self.timeout = timeout

   # src/tools/browser/browser_cli.py

    def run(self, args: str) -> CLIResult:
        cmd = ["npx", "playwright-cli"] + shlex.split(args)

        try:
            logger.info(f"[PlaywrightCLI] Running: {' '.join(cmd)}")

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
            logger.exception("[PlaywrightCLI] run failed")
            return CLIResult(
                success=False,
                output="",
                error=str(e),
                return_code=-1,
            )

    # -------------------------
    # Convenience helpers
    # -------------------------

    def open(self, url: str) -> CLIResult:
        return self.run(f"-s {self.session} open {url} --headed")

    def snapshot(self) -> CLIResult:
        return self.run(f"-s {self.session} snapshot")

    def click(self, element: str) -> CLIResult:
        return self.run(f"-s {self.session} click {element}")

    def fill(self, element: str, text: str) -> CLIResult:
        return self.run(
            f"-s {self.session} fill {element} {shlex.quote(text)}"
        )

    def press(self, key: str) -> CLIResult:
        return self.run(f"-s {self.session} press {key}")

    def back(self) -> CLIResult:
        return self.run(f"-s {self.session} back")

    def close(self) -> CLIResult:
        return self.run(f"-s {self.session} close")