"""Verify structured recovery evidence is bounded and Mission-scoped."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from run_dag import controller_attempts, mission_executions, planner_feedback


class RecoveryEvidenceTests(unittest.TestCase):
    """Exercise Controller/local evidence filtering without a running Controller."""

    def test_controller_attempts_are_filtered_by_mission(self) -> None:
        """Only attempts owned by the failed Mission enter its recovery context."""
        body = {
            "attempts": [
                {"mission_id": "wanted", "task_id": "a", "status": "Failed"},
                {"mission_id": "other", "task_id": "b", "status": "Completed"},
            ]
        }
        with patch("run_dag.http_json", return_value=(200, body)):
            self.assertEqual(
                controller_attempts("http://controller", "wanted"), [body["attempts"][0]]
            )

    def test_local_outcome_drops_large_available_action_snapshot(self) -> None:
        """Recovery keeps the failure and transition facts but not repeated action catalogs."""
        invocation = {
            "mission_id": "wanted",
            "task_id": "a",
            "group_id": "g",
            "role_id": "r",
            "operation": "coherent.agent-1-primitive@v1",
            "objective": "test",
            "parameters": {"action": "[grab] <cup>(1)"},
            "resource_ids": [],
        }
        rows = [
            {
                "execution_id": "e",
                "invocation_json": json.dumps(invocation),
                "state": "FAILED",
                "detail": "not currently available",
                "local_outcome_json": json.dumps(
                    {"error": "not currently available", "available_actions": ["many"]}
                ),
            }
        ]
        outcomes = mission_executions(rows, "wanted")
        self.assertEqual(outcomes[0]["local_outcome"], {"error": "not currently available"})

    def test_planner_feedback_excludes_runtime_diagnostic_blob(self) -> None:
        """The Planner receives structured fields rather than complete event-log payloads."""
        compact = planner_feedback(
            [
                {
                    "segment": 1,
                    "controller_execution_attempts": [{"status": "Failed"}],
                    "runtime_diagnostics": {"events": ["large"]},
                }
            ]
        )
        self.assertIn("controller_execution_attempts", compact[0])
        self.assertNotIn("runtime_diagnostics", compact[0])


if __name__ == "__main__":
    unittest.main()
