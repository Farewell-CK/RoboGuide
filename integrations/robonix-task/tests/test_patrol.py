"""Verify fixed patrol preparation never invents map bindings, arrival or motion consent."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from robonix_task.patrol import (
    ARRIVAL_CONTRACT,
    ASSIGNMENTS,
    PatrolValidationError,
    arrival_predicate,
    assess_patrol_preflight,
    build_patrol_plan,
    validate_patrol_plan,
)


def fixture() -> dict[str, Any]:
    """Construct explicitly synthetic independent sources, not copied field map coordinates."""
    snapshot: dict[str, Any] = {
        "physical_registry": {
            "schema": "roboguide.physical-entity-registry/v0.1",
            "registry_id": "synthetic-entities",
            "revision": 1,
            "routing_profile": "one-routable-entity-per-node",
            "entities": [{"entity_id": a.entity_id, "node_id": a.node_id} for a in ASSIGNMENTS],
        },
        "inventory": {
            "observed_at_ms": 1000,
            "nodes": [
                {"node_id": a.node_id, "liveness": "Reachable", "contracts": ["task.delegate@v1"]}
                for a in ASSIGNMENTS
            ],
        },
        "chassis": {
            a.node_id: {
                "status": "reported",
                "posture": "standing",
                "evidence": {"controller_observed_at_ms": 1000, "remaining_valid_for_ms": 3000},
            }
            for a in ASSIGNMENTS
        },
        "targets": [],
        "arrival_verifier": {
            "source_id": "synthetic-onsite-observer",
            "registered": True,
            "contract": dict(ARRIVAL_CONTRACT),
            "max_evidence_age_ms": 5000,
            "mode": "independent-onsite-attestation",
        },
        "photo_support": {
            a.node_id: {"robonix_capture": True, "artifact_receipt": True} for a in ASSIGNMENTS
        },
        "summary_artifact_supported": True,
    }
    for index, assignment in enumerate(ASSIGNMENTS):
        digest = "sha256:" + str(index + 1) * 64
        map_id = "synthetic-map-" + str(index)
        region_id = "synthetic-region-" + str(index)
        snapshot["targets"].append(
            {
                "node_id": assignment.node_id,
                "entity_id": assignment.entity_id,
                "target_name": assignment.target_name,
                "map_id": map_id,
                "region_id": region_id,
                "map_binding_verified": True,
                "region_source_digest": digest,
                "region_source": {
                    "node_id": assignment.node_id,
                    "map_id": map_id,
                    "document_digest": digest,
                },
                "region": {
                    "annotation_id": region_id,
                    "name": assignment.target_name,
                    "kind": "region",
                    "stale": False,
                    "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                },
            }
        )
    snapshot["arrival_verifier"]["predicates"] = [
        arrival_predicate(snapshot, a) for a in ASSIGNMENTS
    ]
    return snapshot


class PatrolTests(unittest.TestCase):
    """Protect the real decision boundary while leaving all execution to external RG authority."""

    def test_fixed_template_has_independent_physical_actors_and_external_arrival(self) -> None:
        """Two navigation goals use Robonix task delegation and independent onsite predicates."""
        snapshot = fixture()
        report = assess_patrol_preflight(snapshot)
        self.assertEqual(report["status"], "ReadyForOnsiteAuthorization")
        self.assertFalse(report["motion_authorized"])
        plan = build_patrol_plan(snapshot, "synthetic-patrol")
        self.assertEqual(len(plan["tasks"]), 2)
        self.assertEqual(
            {a["physical_entity"] for a in plan["mission"]["actors"]},
            {a.entity_id for a in ASSIGNMENTS},
        )
        for assignment, task in zip(ASSIGNMENTS, plan["tasks"], strict=True):
            self.assertEqual(task["depends_on"], [])
            self.assertEqual(task["coupling_mode"], "independent")
            self.assertEqual(task["satisfaction"]["basis"], "verifier-evidence")
            self.assertEqual(task["satisfaction"]["verifier"]["contract"], ARRIVAL_CONTRACT)
            self.assertEqual(
                task["satisfaction"]["expected_effect"], arrival_predicate(snapshot, assignment)
            )
            self.assertEqual(
                task["roles"][0]["execution_intent"]["parameters"], {"profile": "local-task-v1"}
            )
        result = validate_patrol_plan(plan, snapshot)
        self.assertTrue(result["read_only_validation"])
        self.assertFalse(result["submitted"])

    def test_execution_report_missing_verifier_or_changed_target_is_rejected(self) -> None:
        """Local success, a different predicate or a missing verifier never proves arrival."""
        for change in (
            "execution-report",
            "missing-verifier",
            "foreign-predicate",
            "pending-source",
        ):
            with self.subTest(change=change):
                snapshot = fixture()
                plan = build_patrol_plan(snapshot, "synthetic-patrol")
                if change == "execution-report":
                    plan["tasks"][0]["satisfaction"].update(basis="execution-report", verifier=None)
                elif change == "missing-verifier":
                    plan["tasks"][0]["satisfaction"]["verifier"] = None
                elif change == "foreign-predicate":
                    plan["tasks"][0]["satisfaction"]["verifier"]["predicate"] = "Pilot said done"
                else:
                    snapshot["targets"][0]["region_source_digest"] = "sha256:" + "3" * 64
                with self.assertRaises(PatrolValidationError):
                    validate_patrol_plan(plan, snapshot)

    def test_same_physical_actor_or_missing_distinctness_constraint_is_rejected(self) -> None:
        """Two Role names cannot substitute for two separately bound physical entities."""
        for change in ("same-entity", "missing-constraint", "sequential", "extra-role"):
            with self.subTest(change=change):
                snapshot = fixture()
                plan = build_patrol_plan(snapshot, "synthetic-patrol")
                if change == "same-entity":
                    plan["mission"]["actors"][1]["physical_entity"] = ASSIGNMENTS[0].entity_id
                elif change == "missing-constraint":
                    plan["contexts"][0]["executor_constraints"] = []
                elif change == "sequential":
                    plan["tasks"][1]["depends_on"] = [plan["tasks"][0]["id"]]
                else:
                    plan["tasks"][0]["roles"].append(deepcopy(plan["tasks"][0]["roles"][0]))
                with self.assertRaises(PatrolValidationError):
                    validate_patrol_plan(plan, snapshot)

    def test_map_label_equality_does_not_authorize_borrowed_peer_annotation(self) -> None:
        """The same map label cannot replace an independent owner and verified map binding."""
        snapshot = fixture()
        first, second = snapshot["targets"]
        second["map_id"] = first["map_id"]
        second["region_source"] = deepcopy(first["region_source"])
        second["region_source_digest"] = first["region_source_digest"]
        second["map_binding_verified"] = False
        report = assess_patrol_preflight(snapshot)
        codes = {
            b["code"]
            for b in report["navigation_blockers"]
            if b["node_id"] == ASSIGNMENTS[1].node_id
        }
        self.assertIn("region_source_node_mismatch", codes)
        self.assertIn("independent_map_binding_unverified", codes)
        self.assertFalse(report["navigation_ready"])

    def test_wrong_map_digest_stale_or_degenerate_region_stays_blocked(self) -> None:
        """Reject each unsupported original target source instead of inventing a goal point."""
        changes: dict[str, Callable[[dict[str, Any]], None]] = {
            "map": lambda t: t["region_source"].update(map_id="foreign-map"),
            "digest": lambda t: t.update(region_source_digest="sha256:" + "f" * 64),
            "stale": lambda t: t["region"].update(stale=True),
            "points": lambda t: t["region"].update(points=[[0, 0], [1, 0], [2, 0]]),
            "huge": lambda t: t["region"].update(points=[[10**1000, 0], [1, 0], [2, 0]]),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                snapshot = fixture()
                change(snapshot["targets"][0])
                self.assertFalse(assess_patrol_preflight(snapshot)["navigation_ready"])

    def test_missing_target_builds_only_explicit_pending_readonly_template(self) -> None:
        """An unavailable dog2 Scene never receives the peer's coordinates or implicit consent."""
        snapshot = fixture()
        snapshot["targets"] = snapshot["targets"][:1]
        plan = build_patrol_plan(snapshot, "pending-patrol")
        predicate = plan["tasks"][1]["satisfaction"]["expected_effect"]
        self.assertIn("pending-map-binding", predicate)
        self.assertIn("尚未独立核验", plan["tasks"][1]["roles"][0]["execution_intent"]["objective"])
        self.assertFalse(validate_patrol_plan(plan, snapshot)["navigation_ready"])
        with self.assertRaisesRegex(PatrolValidationError, "preflight_blocked"):
            validate_patrol_plan(
                plan, snapshot, motion_authorized=True, for_submission=True, observed_at_ms=1000
            )

    def test_submission_requires_new_motion_consent_and_current_controller_time(self) -> None:
        """Even a fully supported template remains unapplied until both external gates exist."""
        snapshot = fixture()
        plan = build_patrol_plan(snapshot, "synthetic-patrol")
        with self.assertRaisesRegex(PatrolValidationError, "motion_authorization"):
            validate_patrol_plan(plan, snapshot, for_submission=True, observed_at_ms=1000)
        with self.assertRaisesRegex(PatrolValidationError, "current_controller"):
            validate_patrol_plan(plan, snapshot, for_submission=True, motion_authorized=True)
        with self.assertRaisesRegex(PatrolValidationError, "preflight_blocked"):
            validate_patrol_plan(
                plan, snapshot, for_submission=True, motion_authorized=True, observed_at_ms=4000
            )
        accepted = validate_patrol_plan(
            plan, snapshot, for_submission=True, motion_authorized=True, observed_at_ms=1000
        )
        self.assertFalse(accepted["submitted"])
        self.assertFalse(accepted["read_only_validation"])

    def test_unregistered_or_unscoped_arrival_source_stays_blocked(self) -> None:
        """A proposed verifier contract or source with no exact predicate grants is not live."""
        for change in ("unregistered", "foreign-contract", "no-predicates"):
            with self.subTest(change=change):
                snapshot = fixture()
                verifier = snapshot["arrival_verifier"]
                if change == "unregistered":
                    verifier["registered"] = False
                elif change == "foreign-contract":
                    verifier["contract"] = {"namespace": "fake", "name": "arrive", "version": "v1"}
                else:
                    verifier["predicates"] = []
                self.assertFalse(assess_patrol_preflight(snapshot)["navigation_ready"])

    def test_photo_artifact_and_summary_gaps_do_not_generate_fake_tasks(self) -> None:
        """Navigation preparation remains distinct from a complete patrol with photo evidence."""
        snapshot = fixture()
        snapshot["photo_support"] = {}
        snapshot["summary_artifact_supported"] = False
        report = assess_patrol_preflight(snapshot)
        self.assertTrue(report["navigation_ready"])
        self.assertEqual(report["status"], "Blocked")
        self.assertEqual(len(report["patrol_blockers"]), 5)
        plan = build_patrol_plan(snapshot, "navigation-only")
        self.assertEqual([t["id"] for t in plan["tasks"]], [a.task_id for a in ASSIGNMENTS])
        self.assertEqual(
            validate_patrol_plan(plan, snapshot)["plan_scope"], "navigation-only-preparation"
        )

    def test_unknown_registry_schema_cannot_establish_current_actor_routes(self) -> None:
        """A foreign registry contract cannot authorize fixed physical Actor routes."""
        snapshot = fixture()
        snapshot["physical_registry"]["schema"] = "unrecognized/registry"
        report = assess_patrol_preflight(snapshot)
        self.assertFalse(report["navigation_ready"])
        self.assertIn(
            "physical_registry_unavailable", [b["code"] for b in report["navigation_blockers"]]
        )

    def test_missing_documents_are_explicit_and_results_detach_from_input(self) -> None:
        """Unavailable deployment data blocks every relevant gate without crashing or mutation."""
        original = {
            "physical_registry": None,
            "inventory": None,
            "chassis": None,
            "photo_support": None,
        }
        report = assess_patrol_preflight(original)
        self.assertFalse(report["navigation_ready"])
        self.assertEqual(
            original,
            {"physical_registry": None, "inventory": None, "chassis": None, "photo_support": None},
        )
        snapshot = fixture()
        plan = build_patrol_plan(snapshot, "synthetic-patrol")
        result = validate_patrol_plan(plan, snapshot)
        result["navigation_blockers"].append({"code": "caller-change", "node_id": "x"})
        self.assertEqual(assess_patrol_preflight(snapshot)["navigation_blockers"], [])


if __name__ == "__main__":
    unittest.main()
