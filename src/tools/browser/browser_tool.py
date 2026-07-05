# src/tools/browser/browser_tool.py

from typing import Dict, Any
import logging

from .browser_cli import PlaywrightCLI
from .browser_state import BrowserState
from src.tools.base import safe_tool
logger = logging.getLogger(__name__)
from langchain_core.tools import tool

class BrowserTool:
    """
    High-level tool interface for LLM agent.
    Keeps actions small + structured.
    """

    def __init__(self, session: str = "agent"):
        self.cli = PlaywrightCLI(session=session)
        self.state = BrowserState(session=session)

    # -------------------------
    # Core actions
    # -------------------------


    def open(self, url: str):
        res = self.cli.open(url)
        self.state.url = url
        self.state.add_step(f"open:{url}")

        snap = self.cli.snapshot()
        self.state.update_from_snapshot(snap.output)
        return self._format(res, snap)

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


    def press(self, key: str):
        res = self.cli.press(key)
        self.state.add_step(f"press:{key}")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def back(self):
        res = self.cli.back()
        self.state.add_step("back")

        snap = self.cli.snapshot()
        return self._format(res, snap)

    def close(self):
        res = self.cli.close()
        self.state.add_step("close")
        return self._format(res)
    # -------------------------
    # Output normalization
    # -------------------------

    def _format(self, res, snapshot=None) -> Dict[str, Any]:
        return {
            "success": res.success,
            "output": res.output[:2000],  # prevent context explosion
            "error": res.error,
            "state": {
                "url": self.state.url,
                "last_action": self.state.last_action,
                "completed_steps": self.state.completed_steps[-10:],
            },
            "snapshot": snapshot.output[:10000] if snapshot else None,
        }
    
    def describe_state(self) -> str:
        s = self.state
        if not s.url:
            return "CURRENT BROWSER STATE: No page is currently open in this session."
        steps = "; ".join(s.completed_steps[-5:]) or "none"
        return (
            "CURRENT BROWSER STATE:\n"
        f"- URL: {s.url}\n"
        f"- Last action: {s.last_action}\n"
        f"- Recent steps this session: {steps}\n"
        "If the page above already satisfies what's being asked (e.g. the target "
        "site is already open), do NOT call open() or close() again. Call "
        "snapshot() to see the current page and proceed directly with the "
        "requested action (e.g. click a login button)."
    )
    


def get_tools(browser: BrowserTool):

    @safe_tool("open")
    def open(url: str):
        return browser.open(url)

    @safe_tool("snapshot")
    def snapshot():
        return browser.snapshot()

    @safe_tool("click")
    def click(element: str):
        return browser.click(element)

    @safe_tool("fill")
    def fill(element: str, text: str):
        return browser.fill(element, text)

    @safe_tool("press")
    def press(key: str):
        return browser.press(key)

    @safe_tool("back")
    def back():
        return browser.back()

    @safe_tool("close")
    def close():
        return browser.close()

    return [
        open,
        snapshot,
        click,
        fill,
        press,
        back,
        close,
    ]