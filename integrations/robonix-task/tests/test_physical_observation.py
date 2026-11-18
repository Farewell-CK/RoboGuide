"""Keep deployment metadata out of World facts and refuse republished stale measurements."""

from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from robonix_task.physical_observation import entity_reference, physical_observation


def original() -> dict[str, Any]:
    """Supply explicit synthetic physical reports mixed with forbidden deployment details."""
    return {
        "atlas": {"providers": ["not-world"]},
        "resources": ["not-world"],
        "execution": {"status": "not-world"},
        "control_owner": "not-world",
        "chassis": {
            "contract_id": "robonix/primitive/chassis/state",
            "availability": "current",
            "age_ms": 50,
            "received_at_ns": 1000000,
            "value": {
                "stale": False,
                "stale_ms": 5000,
                "posture": "down",
                "battery": {"percent": 75},
            },
        },
        "mapping": {
            "availability": "current",
            "age_ms": 100,
            "received_at_ns": 900000,
            "value": {
                "has_map": True,
                "map_id": "same-label",
                "mode": "localization",
                "pose": {"x": 1.0, "y": 2.0, "theta": 0.0},
            },
        },
    }


class PhysicalObservationTests(unittest.TestCase):
    """Validate a filtered reported World channel without inventing physical authority."""

    def test_only_physical_fields_are_projected_and_source_is_unmodified(self) -> None:
        """A configured entity reference is distinct from physical observation or Node health."""
        snapshot = original()
        before = deepcopy(snapshot)
        result = physical_observation(snapshot, "entity-dog1")
        self.assertEqual(snapshot, before)
        self.assertEqual(result["posture"]["reported_posture"], "down")
        self.assertEqual(result["battery"]["percent"], 75)
        self.assertFalse(
            {"atlas", "resources", "execution", "control_owner", "node_id"} & result.keys()
        )
        self.assertEqual(entity_reference("entity-dog1"), {"entity_id": "entity-dog1"})
        self.assertFalse(result["arrival_verified"])
        self.assertFalse(result["device_stop_verified"])

    def test_inner_expiry_and_unknown_posture_never_become_current_world_facts(self) -> None:
        """A newer outer export cannot revive old, unknown or shorter-lived chassis reports."""
        for change in ["old", "short", "unknown", "negative", "nan"]:
            with self.subTest(change=change):
                snapshot = original()
                chassis = snapshot["chassis"]
                if change == "old":
                    chassis["age_ms"] = 5000
                elif change == "short":
                    chassis["value"]["stale_ms"] = 40
                elif change == "unknown":
                    chassis["value"]["posture"] = "unknown"
                elif change == "negative":
                    chassis["age_ms"] = -1
                else:
                    chassis["age_ms"] = float("nan")
                result = physical_observation(snapshot, "entity-dog1")
                self.assertIsNone(result["posture"]["reported_posture"])
                self.assertEqual(result["posture"]["availability"], "unavailable")

    def test_mapping_response_is_never_qualified_as_pose_or_shared_frame(self) -> None:
        """The HTTP receipt carries neither localization quality nor original measurement time."""
        snapshot = original()
        result = physical_observation(snapshot, "entity-dog1")
        self.assertEqual(result["mapping"]["reported_pose"]["x"], 1.0)
        for key in ["pose_measurement_time", "localization_quality", "map_revision_id"]:
            self.assertIsNone(result["mapping"][key])
        self.assertFalse(result["mapping"]["localization_verified"])
        self.assertFalse(result["shared_frame_verified"])
        snapshot["mapping"]["age_ms"] = 10001
        self.assertIsNone(physical_observation(snapshot, "entity-dog1")["mapping"]["reported_pose"])

    def test_missing_sources_and_invalid_entity_remain_explicit(self) -> None:
        """An empty sensor snapshot does not grant posture, motion or an invented identity."""
        result = physical_observation({}, "entity-dog1")
        self.assertIsNone(result["posture"]["reported_posture"])
        self.assertFalse(result["motion_authorized"])
        with self.assertRaises(ValueError):
            entity_reference("../node")


if __name__ == "__main__":
    unittest.main()
