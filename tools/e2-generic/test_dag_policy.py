"""Exercise multi-step admission boundaries without a provider or simulator mutation."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from dag_policy import validate_plan


class DagPolicyTests(unittest.TestCase):
    """Verify future-action admission, exact identities, budgets, and dependency ordering."""

    def setUp(self) -> None:
        """Create a synthetic two-action plan, unrelated to benchmark task solutions."""
        self.contexts = {
            9: {"agent_class": "quadrotor", "available_actions": ["[takeoff_from] <table>(10)"]}
        }
        contract = {"namespace": "coherent", "name": "agent-9-primitive", "version": "v1"}
        tasks = []
        for index, action in enumerate(("[takeoff_from] <table>(10)", "[land_on] <table>(10)")):
            tasks.append(
                {
                    "id": f"t{index}",
                    "depends_on": [] if index == 0 else ["t0"],
                    "satisfaction": {"basis": "execution-report"},
                    "roles": [
                        {
                            "resource_scope": "task",
                            "requirements": {
                                "capabilities": [{"contract": contract}],
                                "resources": [{"kind": "space", "units": 1}],
                            },
                            "execution_intent": {
                                "operation": contract,
                                "parameters": {
                                    "env": "env0",
                                    "task": 1,
                                    "agent_id": 9,
                                    "agent_class": "quadrotor",
                                    "action": action,
                                },
                            },
                        }
                    ],
                }
            )
        self.plan: dict[str, Any] = {"mission": {"id": "test"}, "contexts": [{}], "tasks": tasks}

    def test_future_action_and_unedited_plan(self) -> None:
        """A future landing is admitted without pretending it is executable now."""
        before = copy.deepcopy(self.plan)
        ordered = validate_plan(self.plan, "test", "env0", 1, self.contexts, 2)
        self.assertEqual(len(ordered), 2)
        self.assertEqual(self.plan, before)

    def test_array_order_is_not_execution_order(self) -> None:
        """Dependencies, rather than provider array order, determine execution order."""
        self.plan["tasks"].reverse()
        ordered = validate_plan(self.plan, "test", "env0", 1, self.contexts, 2)
        self.assertEqual([t["task_id"] for t in ordered], ["t0", "t1"])

    def test_parallel_roots_rejected(self) -> None:
        """The serial benchmark cannot silently admit racing root tasks."""
        self.plan["tasks"][1]["depends_on"] = []
        with self.assertRaises(ValueError):
            validate_plan(self.plan, "test", "env0", 1, self.contexts, 2)

    def test_budget_rejected(self) -> None:
        """A whole submitted plan must fit the remaining execution budget."""
        with self.assertRaises(ValueError):
            validate_plan(self.plan, "test", "env0", 1, self.contexts, 1)

    def test_wrong_capability_rejected(self) -> None:
        """An execution intent cannot be routed by a mismatched capability."""
        self.plan["tasks"][1]["roles"][0]["requirements"]["capabilities"] = []
        with self.assertRaises(ValueError):
            validate_plan(self.plan, "test", "env0", 1, self.contexts, 2)

    def test_unavailable_first_action_rejected(self) -> None:
        """Future preconditions cannot justify an unavailable first action."""
        self.contexts[9]["available_actions"] = []
        with self.assertRaises(ValueError):
            validate_plan(self.plan, "test", "env0", 1, self.contexts, 2)


if __name__ == "__main__":
    unittest.main()
