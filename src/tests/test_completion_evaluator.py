import unittest

from src.utils.completion_evaluator import (
    normalize_tool_result,
    is_action_success,
    is_verification_success,
    is_task_complete,
)


class TestCompletionEvaluator(unittest.TestCase):

    def test_normalize_action_success_status(self):
        result = normalize_tool_result("launch_application", {"status": "launched", "app": "Calculator"})
        self.assertEqual(result["completion_type"], "action_success")
        self.assertTrue(is_action_success(result))
        self.assertFalse(is_verification_success(result))
        self.assertFalse(is_task_complete(result))

    def test_normalize_observation_result(self):
        result = normalize_tool_result(
            "inspect_active_window_text",
            {"title": "Calculator", "visible_text": [{"text": "1", "type": "button"}]},
        )
        self.assertEqual(result["completion_type"], "observation")
        self.assertFalse(is_action_success(result))
        self.assertFalse(is_verification_success(result))
        self.assertFalse(is_task_complete(result))

    def test_normalize_verification_success(self):
        result = normalize_tool_result(
            "inspect_active_window_text",
            {"query": "calculator", "query_found": True, "matches": [{"text": "Calculator"}]},
        )
        self.assertEqual(result["completion_type"], "verification_success")
        self.assertFalse(is_action_success(result))
        self.assertTrue(is_verification_success(result))
        self.assertFalse(is_task_complete(result))

    def test_normalize_terminal_task_complete(self):
        result = normalize_tool_result("custom_tool", {"status": "completed", "details": "Finished"})
        self.assertEqual(result["completion_type"], "task_complete")
        self.assertFalse(is_action_success(result))
        self.assertFalse(is_verification_success(result))
        self.assertTrue(is_task_complete(result))
