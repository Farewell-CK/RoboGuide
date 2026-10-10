"""Verify the adapter prevents post-goal primitives without changing benchmark state."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from coherent_local_eaios.generic_bridge import (
    CoherentPrimitiveAdapter,
    GenericBackendConfig,
    PrimitiveStore,
)


class GoalGuardTests(unittest.TestCase):
    """Exercise the pre-execution official-goal guard in isolation."""

    def test_satisfied_goal_cancels_overshoot_without_incrementing_step(self) -> None:
        """A queued extra primitive becomes Cancelled and never mutates the graph."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph: dict[str, object] = {
                "nodes": [],
                "edges": [{"from_id": 1, "to_id": 2, "relation_type": "ON"}],
            }
            task: dict[str, object] = {"task_goal": {"on_<cup>(1)_<table>(2)": [1]}}
            store = PrimitiveStore(root / "state.sqlite3", graph)
            adapter = CoherentPrimitiveAdapter(
                store,
                GenericBackendConfig(
                    env_name="env0",
                    task_index=1,
                    pefa_root=root,
                    evidence_dir=root / "evidence",
                    task_data=task,
                ),
            )
            response = adapter.accept(
                {
                    "invocation": {
                        "mission_id": "mission-1",
                        "task_id": "extra-task",
                        "group_id": "group-1",
                        "role_id": "role-1",
                        "operation": "coherent.agent-9-primitive@v1",
                        "objective": "unnecessary extra action",
                        "parameters": {
                            "env": "env0",
                            "task": 1,
                            "agent_id": 9,
                            "agent_class": "quadrotor",
                            "action": "[takeoff_from] <table>(2)",
                        },
                        "resource_ids": ["agent-9-slot"],
                    }
                }
            )
            adapter.dispatch(str(response["execution_id"]))
            deadline = time.monotonic() + 2.0
            while response["state"] == "ACCEPTED" and time.monotonic() < deadline:
                time.sleep(0.01)
                response = adapter.status({"execution_id": response["execution_id"]})
            retained_graph, step_count = store.graph_state()
            self.assertEqual(response["state"], "CANCELLED")
            self.assertFalse(response["local_outcome"]["primitive_executed"])
            self.assertEqual(step_count, 0)
            self.assertEqual(retained_graph, graph)
            adapter.close()


if __name__ == "__main__":
    unittest.main()
