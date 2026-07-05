# src/tools/browser/browser_state.py

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class BrowserState:
    url: str = ""
    title: str = ""
    session: str = "agent"

    goal: str = ""
    last_action: str = ""

    # Keep ONLY structured memory
    completed_steps: List[str] = field(default_factory=list)

    # last known element refs (very important for 7B stability)
    last_snapshot: Optional[Dict[str, Any]] = None

    def update_from_snapshot(self, snapshot_text: str):
        """
        Minimal parser — you can upgrade this later.
        Keeps state small.
        """

        self.last_snapshot = {
            "raw": snapshot_text
        }

    def add_step(self, step: str):
        self.completed_steps.append(step)
        self.last_action = step

        if len(self.completed_steps) > 20:
            self.completed_steps = self.completed_steps[-20:]