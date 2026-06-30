from __future__ import annotations

from typing import Any

INTERMEDIATE_COMPLETION_STATUSES = {
    "launched",
    "launch_requested",
    "focused",
    "launched_known_uri_app",
    "focused_existing",
    "launched_or_requested",
    "opened",
}

TERMINAL_TASK_STATUSES = {
    "success",
    "completed",
    "complete",
    "done",
    "verified",
}

VERIFICATION_KEY = "query_found"
OBSERVATION_KEYS = {
    "visible_text",
    "matches",
    "title",
    "window",
}


def normalize_tool_result(tool_name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result

    if "completion_type" in result:
        return result

    status = str(result.get("status", "")).lower()
    query_found = result.get(VERIFICATION_KEY)

    if query_found is True:
        result["completion_type"] = "verification_success"
        return result

    if status in INTERMEDIATE_COMPLETION_STATUSES:
        result["completion_type"] = "action_success"
        return result

    if status in TERMINAL_TASK_STATUSES:
        result["completion_type"] = "task_complete"
        return result

    if any(key in result for key in OBSERVATION_KEYS):
        result["completion_type"] = "observation"
        return result

    return result


def is_action_success(result: Any) -> bool:
    return isinstance(result, dict) and result.get("completion_type") == "action_success"


def is_verification_success(result: Any) -> bool:
    return isinstance(result, dict) and result.get("completion_type") == "verification_success"


def is_task_complete(result: Any) -> bool:
    return isinstance(result, dict) and result.get("completion_type") == "task_complete"
