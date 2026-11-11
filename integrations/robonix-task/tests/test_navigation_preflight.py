"""Refuse stale or cross-map poses in the navigation-only preparation profile."""

from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from robonix_task.arrival_verifier_store import digest
from robonix_task.navigation_preflight import assess_navigation_preflight
from robonix_task.patrol import ARRIVAL_CONTRACT, ASSIGNMENTS, arrival_predicate
from robonix_task.patrol_grounding import build_patrol_snapshot
from robonix_task.posture import inspect_posture
from robonix_task.scene_targets import decode_scene_definition
from test_patrol_grounding import _record, _replace_definition, fixture
from test_physical_observation import original


def inputs() -> list[dict[str, Any]]:
    """Build independent original Controller documents plus an exactly scoped registered source."""
    world, inventory, registry, _ = fixture()
    state: dict[str, Any] = {
        "schema": world["schema"],
        "observed_at_ms": world["observed_at_ms"],
        "records": [],
    }
    for assignment in ASSIGNMENTS:
        robot = original()
        robot["chassis"]["value"]["posture"] = "standing"
        robot["mapping"]["value"]["map_id"] = "synthetic-map-" + assignment.node_id
        record = _record(
            assignment.node_id, assignment.entity_id, "robot-state", "lite3.robot-state/v0.1", robot
        )
        record.update(
            object={"class": "node", "object_type": "robot", "object_id": assignment.node_id},
            source=f"node:{assignment.node_id}/robot-observer",
        )
        record["value"].update(valid_for_ms=5000, confidence_millionths=None)
        state["records"].append(record)
    chassis = {a.node_id: inspect_posture(state, inventory, node_id=a.node_id) for a in ASSIGNMENTS}
    snapshot = build_patrol_snapshot(world, inventory, registry, chassis)
    targets = []
    for assignment in ASSIGNMENTS:
        selected = next(t for t in snapshot["targets"] if t["node_id"] == assignment.node_id)
        target = {
            k: selected[k]
            for k in [
                "entity_id",
                "node_id",
                "target_name",
                "map_id",
                "region_id",
                "region_source_digest",
            ]
        }
        target["predicate"] = arrival_predicate(snapshot, assignment)
        targets.append(target)
    config = {
        "schema_version": "roboguide.onsite-arrival-verifier/v0.1",
        "source_id": "software-arrival",
        "revision": 1,
        "tenant_id": "local",
        "producer_principal": "software-observer",
        "verifier": dict(ARRIVAL_CONTRACT),
        "max_age_ms": 5000,
        "targets": targets,
    }
    source = {
        "source_id": config["source_id"],
        "revision": 1,
        "tenant_id": "local",
        "producer_principals": [config["producer_principal"]],
        "verifier": dict(ARRIVAL_CONTRACT),
        "predicates": sorted(t["predicate"] for t in targets),
        "max_age_ms": 5000,
        "stop_timeout_ms": 5000,
        "enabled": True,
        "continuous": False,
        "require_artifact": False,
    }
    view = {
        "schema_version": "roboguide.semantic-verifier-view/v0.1",
        "registry": {
            "schema_version": "roboguide.semantic-verifier-registry/v0.1",
            "registry_id": "synthetic-arrival-registry",
            "revision": 1,
            "sources": {config["source_id"]: source},
        },
    }
    return [world, state, inventory, registry, view, config]


class NavigationPreflightTests(unittest.TestCase):
    """Keep reported-map consistency separate from localization and movement permission."""

    def test_normal_sources_pass_navigation_only_without_photo_or_summary(self) -> None:
        """Exact sources can pass navigation preparation while photo support stays unavailable."""
        values = inputs()
        before = digest(values)
        result = assess_navigation_preflight(*values)
        self.assertTrue(result["navigation_ready"], result)
        self.assertEqual(result["status"], "ReadyForOnsiteAuthorization")
        self.assertTrue(result["reported_map_consistency_verified"])
        self.assertTrue(result["patrol_blockers"])
        self.assertEqual(digest(values), before)
        for key in [
            "localization_verified",
            "shared_frame_verified",
            "motion_authorized",
            "physical_effect_verified",
        ]:
            self.assertFalse(result[key])
        self.assertTrue(
            all(p["evidence"]["remaining_valid_for_ms"] > 0 for p in result["positions"].values())
        )

    def test_wrong_map_stale_missing_or_foreign_pose_blocks(self) -> None:
        """Each target requires its own qualified current pose on that target's original map."""
        for mode in ["wrong-map", "stale", "missing", "foreign-node", "missing-epoch"]:
            with self.subTest(mode=mode):
                values = inputs()
                record = values[1]["records"][0]
                if mode == "wrong-map":
                    record["value"]["value"]["mapping"]["value"]["map_id"] = "wrong-map"
                elif mode == "stale":
                    record["value"]["value"]["mapping"]["age_ms"] = 10001
                elif mode == "missing":
                    record["value"]["value"].pop("mapping")
                elif mode == "foreign-node":
                    record["object"]["object_id"] = ASSIGNMENTS[1].node_id
                else:
                    record["value"]["source_epoch"] = ""
                result = assess_navigation_preflight(*values)
                self.assertFalse(result["navigation_ready"])
                self.assertFalse(result["reported_map_consistency_verified"])
                self.assertTrue(
                    any(
                        "reported_pose_unavailable" in b["code"]
                        for b in result["navigation_blockers"]
                    )
                )

    def test_changed_scene_definition_cannot_reuse_registered_target(self) -> None:
        """Even internally valid updated Scene bytes require a newly reviewed source binding."""
        values = inputs()
        scene = values[0]["records"][1]["value"]["value"]
        definition = decode_scene_definition(scene)
        definition["regions"][0]["points"][0][0] += 0.1
        _replace_definition(scene, definition)
        result = assess_navigation_preflight(*values)
        self.assertFalse(result["navigation_ready"])
        self.assertIn(
            "registered_arrival_source_or_current_target_mismatch",
            [b["code"] for b in result["navigation_blockers"]],
        )

    def test_missing_disabled_changed_or_ambiguous_registry_blocks(self) -> None:
        """A hand-authored registered flag cannot replace the exact original source contract."""
        for mode in ["missing", "disabled", "principal", "predicate", "ambiguous"]:
            with self.subTest(mode=mode):
                values = inputs()
                source = values[4]["registry"]["sources"]["software-arrival"]
                if mode == "missing":
                    values[4]["registry"] = None
                elif mode == "disabled":
                    source["enabled"] = False
                elif mode == "principal":
                    source["producer_principals"] = ["foreign-observer"]
                elif mode == "predicate":
                    source["predicates"][0] = "unrelated-goal"
                else:
                    other = deepcopy(source)
                    other["source_id"] = "competing-source"
                    values[4]["registry"]["sources"]["competing-source"] = other
                self.assertFalse(assess_navigation_preflight(*values)["navigation_ready"])

    def test_known_read_delay_and_later_controller_read_spend_original_pose_budget(self) -> None:
        """No newer envelope or later projection can hide the source's shorter remaining window."""
        values = inputs()
        record = values[1]["records"][0]
        record["value"]["value"]["mapping"]["age_ms"] = 9950
        result = assess_navigation_preflight(*values, local_read_elapsed_ms=40)
        self.assertFalse(result["navigation_ready"])
        position = result["positions"][ASSIGNMENTS[0].node_id]
        self.assertEqual(position["reason"], "mapping_budget_expired_during_preparation")
        self.assertEqual(position["evidence"]["remaining_valid_for_ms"], 0)
        values = inputs()
        values[1]["records"][0]["value"]["value"]["mapping"]["age_ms"] = 9980
        values[2]["observed_at_ms"] += 10
        self.assertFalse(assess_navigation_preflight(*values)["navigation_ready"])

    def test_short_native_chassis_budget_is_not_hidden_by_current_pose(self) -> None:
        """A fresh pose cannot renew the original short native posture observation window."""
        values = inputs()
        values[1]["records"][0]["value"]["value"]["chassis"]["value"]["stale_ms"] = 500
        before = digest(values)
        result = assess_navigation_preflight(*values, local_read_elapsed_ms=600)
        self.assertFalse(result["navigation_ready"])
        self.assertEqual(result["positions"][ASSIGNMENTS[0].node_id]["status"], "reported")
        self.assertIn(
            {
                "code": "fresh_reported_chassis_budget_unavailable",
                "node_id": ASSIGNMENTS[0].node_id,
            },
            result["navigation_blockers"],
        )
        self.assertEqual(digest(values), before)

    def test_malformed_or_expired_projection_refuses_without_actions(self) -> None:
        """Absent documents and invalid read budgets produce explicit blocked preparation."""
        for delay in [-1, True, 3600001]:
            result = assess_navigation_preflight(*inputs(), local_read_elapsed_ms=delay)
            self.assertFalse(result["navigation_ready"])
        values = inputs()
        values[1]["records"][0]["stale"] = True
        self.assertFalse(assess_navigation_preflight(*values)["navigation_ready"])
        self.assertFalse(assess_navigation_preflight({}, {}, {}, {}, {}, {})["navigation_ready"])


if __name__ == "__main__":
    unittest.main()
