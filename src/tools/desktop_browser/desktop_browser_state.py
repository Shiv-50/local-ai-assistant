# src/tools/desktop_browser/desktop_browser_state.py

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class DesktopAppState:
    app_name: str = ""
    port: Optional[int] = None
    session: str = "desktop-agent"
    connected: bool = False

    last_action: str = ""
    completed_steps: List[str] = field(default_factory=list)
    last_snapshot: Optional[Dict[str, Any]] = None

    def update_from_snapshot(self, snapshot_text: str):
        self.last_snapshot = {"raw": snapshot_text}

    def add_step(self, step: str):
        self.completed_steps.append(step)
        self.last_action = step

        if len(self.completed_steps) > 20:
            self.completed_steps = self.completed_steps[-20:]
