# src/tools/desktop_snapshot.py

import logging
import time
from src.tools.base import safe_tool
import ctypes
from pywinauto import Desktop

logger = logging.getLogger(__name__)

INTERACTIVE_TYPES = {
    "Button", "Edit", "ComboBox", "CheckBox", "RadioButton",
    "ListItem", "TreeItem", "TabItem", "MenuItem", "Hyperlink",
    "Document", "SearchBox", "Slider", "Spinner",
}


class _SnapshotCache:
    def __init__(self):
        self.refs: dict[str, object] = {}   # ref -> live pywinauto control
        self.window_title: str = ""

    def clear(self):
        self.refs.clear()


_cache = _SnapshotCache()


def _center(control):
    r = control.rectangle()
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2

def _get_active_uia_window():
    from pywinauto import Desktop
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        return Desktop(backend="uia").window(handle=hwnd)
    except Exception:
        logger.exception("Failed to wrap foreground window via UIA")
        return None

@safe_tool("Snapshot desktop UI elements")
def desktop_snapshot(query: str = "", max_items: int = 40, include_all: bool = False):
    """
    Walk the active window's UI Automation tree and return interactive
    elements (buttons, inputs, list items, tabs, links) each with a short
    ref (e1, e2...), name, control type, and center coordinates.

    ALWAYS call this before clicking or typing into anything you haven't
    just interacted with. Pass query to filter by name (e.g. query="search").
    Refs go stale after any click/type/navigation -- re-snapshot before
    reusing one.
    """
    

    _cache.clear()
    window = _get_active_uia_window()
    if not window:
        return {"status": "no_active_window"}

    _cache.window_title = window.window_text()
    q = query.lower().strip()
    items, counter = [], 0

    for control in window.descendants():
        if len(items) >= max_items:
            break
        try:
            if not control.is_visible() or not control.is_enabled():
                continue
            ctype = control.friendly_class_name()
        except Exception:
            continue

        if not include_all and ctype not in INTERACTIVE_TYPES:
            continue

        try:
            name = (control.window_text() or "").strip()
        except Exception:
            name = ""

        if not name and ctype not in {"Edit", "SearchBox", "ComboBox"}:
            continue
        if q and q not in name.lower() and q not in ctype.lower():
            continue

        try:
            x, y = _center(control)
        except Exception:
            continue

        counter += 1
        ref = f"e{counter}"
        _cache.refs[ref] = control
        items.append({"ref": ref, "type": ctype, "name": name, "x": x, "y": y})

    return {
        "window": _cache.window_title,
        "elements": items,
        "count": len(items),
        "note": "Use click_element_by_ref / type_into_element_by_ref. Re-snapshot after any action.",
    }


@safe_tool("Click desktop UI element by ref")
def click_element_by_ref(ref: str):
    """Click an element by ref from desktop_snapshot. Clicks the actual
    control, not raw pixels -- reliable even if the window moved."""
    control = _cache.refs.get(ref)
    if control is None:
        return {"status": "stale_or_unknown_ref", "ref": ref,
                "next_step": "Call desktop_snapshot again for fresh refs."}
    try:
        control.click_input()
        time.sleep(0.2)
        return {"status": "clicked", "ref": ref}
    except Exception as e:
        return {"status": "click_failed", "ref": ref, "error": str(e)}


@safe_tool("Type into desktop UI element by ref")
def type_into_element_by_ref(ref: str, text: str, auto_enter: bool = False, clear_first: bool = True):
    """Focus and type into a SPECIFIC element by ref. Prefer this over the
    generic type_text tool -- it guarantees text lands in the field you
    identified, not wherever OS focus happens to be."""
    control = _cache.refs.get(ref)
    if control is None:
        return {"status": "stale_or_unknown_ref", "ref": ref,
                "next_step": "Call desktop_snapshot again for fresh refs."}
    try:
        control.click_input()
        time.sleep(0.1)
        try:
            control.set_edit_text(text)          # clean path for Edit controls
        except Exception:
            if clear_first:
                try:
                    control.type_keys("^a{DELETE}", pause=0.02)
                except Exception:
                    pass
            control.type_keys(text, with_spaces=True, pause=0.02)
        if auto_enter:
            control.type_keys("{ENTER}", pause=0.02)
        return {"status": "typed", "ref": ref, "text": text}
    except Exception as e:
        return {"status": "type_failed", "ref": ref, "error": str(e)}


ALL_DESKTOP_SNAPSHOT_TOOLS = [desktop_snapshot, click_element_by_ref, type_into_element_by_ref]