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

    def test_controller_attempts_retry_one_transient_disconnect(self) -> None:
        """A single transport race does not erase otherwise available attempt evidence."""
        body = {"attempts": [{"mission_id": "wanted", "status": "Failed"}]}
        with patch(
            "run_dag.http_json", side_effect=[ConnectionResetError("race"), (200, body)]
        ) as mocked:
            self.assertEqual(controller_attempts("http://controller", "wanted"), body["attempts"])
            self.assertEqual(mocked.call_count, 2)

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

    def test_planner_feedback_excludes_internal_execution_evidence(self) -> None:
        """The Planner receives semantic facts without attempt identities or evidence paths."""
        compact = planner_feedback(
            [
                {
                    "segment": 1,
                    "mission_id": "internal-mission-id",
                    "controller_execution_attempts": [
                        {"execution_id": "internal-attempt-id", "status": "Failed"}
                    ],
                    "execution_outcomes": [
                        {
                            "execution_id": "internal-execution-id",
                            "operation": "coherent.agent-24-primitive@v1",
                            "parameters": {"action": "[grab] <book>(34)"},
                            "state": "FAILED",
                            "local_outcome": {
                                "error": "not currently available",
                                "evidence_file": "/internal/absolute/path.json",
                            },
                        }
                    ],
                    "runtime_diagnostics": {"events": ["large"]},
                }
            ]
        )
        self.assertNotIn("mission_id", compact[0])
        self.assertNotIn("controller_execution_attempts", compact[0])
        self.assertNotIn("runtime_diagnostics", compact[0])
        serialized = json.dumps(compact)
        self.assertNotIn("internal-attempt-id", serialized)
        self.assertNotIn("internal-execution-id", serialized)
        self.assertNotIn("/internal/absolute/path.json", serialized)
        self.assertIn("not currently available", serialized)

    def test_planner_feedback_keeps_only_three_recent_segments(self) -> None:
        """Replanning context remains bounded even after many observation segments."""
        compact = planner_feedback([{"segment": index} for index in range(1, 7)])
        self.assertEqual([row["segment"] for row in compact], [4, 5, 6])


if __name__ == "__main__":
    unittest.main()
