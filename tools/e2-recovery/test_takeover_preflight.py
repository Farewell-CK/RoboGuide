"""Verify cross-entity preflight never converts metadata into execution permission."""

import unittest

from takeover_preflight import assess_takeover


class TakeoverPreflightTests(unittest.TestCase):
    """Cover absent robots, aliased robots and executor-specific action contracts."""

    def registry(self, replacement: bool = True) -> dict:
        """Return an explicit synthetic topology, never an official task fixture."""
        return {
            "routing_profile": "one-routable-entity-per-node",
            "entities": [{"entity_id": "a", "node_id": "node-a"}]
            + ([{"entity_id": "b", "node_id": "node-b"}] if replacement else []),
        }

    def test_missing_robot_and_pinned_action_are_blockers(self) -> None:
        """Registering a standby process cannot redirect agent-specific semantic intent."""
        result = assess_takeover(self.registry(False), {
            "operation": "coherent.agent-24-primitive@v1", "parameters": {"agent_id": 24}
        }, "a", "b")
        self.assertEqual(set(result["blockers"]), {
            "replacement_entity_not_registered", "operation_pins_original_robot",
            "parameters_pin_original_robot",
        })
        self.assertFalse(result["execution_authorized"])

    def test_portable_operation_still_requires_runtime_authority(self) -> None:
        """A structurally portable action does not prove stop, support or commitment."""
        result = assess_takeover(self.registry(), {"operation": "robot.move@v1"}, "a", "b")
        self.assertTrue(result["structural_prerequisites_passed"])
        self.assertFalse(result["execution_authorized"])

    def test_renaming_same_robot_is_not_takeover(self) -> None:
        """Neither identical entity IDs nor two names for one Node prove a second robot."""
        registry = self.registry()
        same = assess_takeover(registry, {}, "a", "a")
        self.assertIn("same_entity_is_not_cross_robot_takeover", same["blockers"])
        registry["entities"][1]["node_id"] = "node-a"
        aliased = assess_takeover(registry, {}, "a", "b")
        self.assertFalse(aliased["structural_prerequisites_passed"])


if __name__ == "__main__":
    unittest.main()
