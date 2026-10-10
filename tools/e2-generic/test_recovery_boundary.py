"""Verify the generic bridge's confirmed-stop and retry identity boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coherent_local_eaios.generic_bridge import PrimitiveInvocation, PrimitiveStore


def invocation(attempt_id: str) -> PrimitiveInvocation:
    """Build one semantic action under a selected physical-attempt identity."""
    return PrimitiveInvocation.from_request(
        {
            "invocation": {
                "mission_id": "mission-1",
                "task_id": "task-1",
                "group_id": "group-1",
                "role_id": "role-1",
                "operation": "coherent.agent-24-primitive@v1",
                "objective": "move the object",
                "parameters": {
                    "env": "env4",
                    "task": 17,
                    "agent_id": 24,
                    "agent_class": "dog",
                    "action": "[grab] <apple>(1)",
                },
                "resource_ids": ["coherent-agent-24-slot"],
                "attempt_id": attempt_id,
            }
        }
    )


class RecoveryBoundaryTests(unittest.TestCase):
    """Exercise durable pre-effect cancellation and fresh-attempt idempotency."""

    def test_retry_gets_new_local_handle_after_confirmed_cancel(self) -> None:
        """A new Controller attempt does not reuse the cancelled local handle."""
        with tempfile.TemporaryDirectory() as directory:
            store = PrimitiveStore(
                Path(directory) / "state.sqlite3", {"nodes": [], "edges": []}
            )
            first, created = store.create_or_get(invocation("attempt-a"))
            self.assertTrue(created)
            store.cancel_before_execution(
                str(first["execution_id"]),
                "cancelled before effect",
                {"primitive_executed": False},
            )
            retry, retry_created = store.create_or_get(invocation("attempt-b"))
            self.assertTrue(retry_created)
            self.assertNotEqual(first["execution_id"], retry["execution_id"])
            self.assertEqual(retry["state"], "ACCEPTED")

    def test_same_attempt_remains_idempotent(self) -> None:
        """Transport replay of one Controller attempt reuses exactly one local handle."""
        with tempfile.TemporaryDirectory() as directory:
            store = PrimitiveStore(
                Path(directory) / "state.sqlite3", {"nodes": [], "edges": []}
            )
            first, first_created = store.create_or_get(invocation("attempt-a"))
            replay, replay_created = store.create_or_get(invocation("attempt-a"))
            self.assertTrue(first_created)
            self.assertFalse(replay_created)
            self.assertEqual(first["execution_id"], replay["execution_id"])


if __name__ == "__main__":
    unittest.main()
