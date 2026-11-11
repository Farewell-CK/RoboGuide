"""Protect exact central provenance and expiry while preparing a two-body patrol snapshot."""

from __future__ import annotations

import hashlib
import json
import unittest
from copy import deepcopy
from typing import Any

from robonix_task.patrol import ASSIGNMENTS, arrival_predicate, assess_patrol_preflight
from robonix_task.patrol_grounding import (
    ENTITY_SCHEMA,
    PatrolGroundingError,
    arrival_registry_template,
    build_patrol_snapshot,
)
from robonix_task.scene_targets import MAX_AGE_MS, SCHEMA, decode_scene_definition


def _definition_bytes(
    entity_id: str, node_id: str, binding: dict[str, Any], regions: list[dict[str, Any]]
) -> str:
    """Create the original canonical definition once, before any typed State transport."""
    return json.dumps(
        {
            "physical_entity_id": entity_id,
            "source_node_id": node_id,
            "map_binding": binding,
            "regions": regions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _replace_definition(report: dict[str, Any], definition: dict[str, Any]) -> None:
    """Regenerate only a test source's original bytes and matching original digest."""
    report["definition_json"] = _definition_bytes(
        definition["physical_entity_id"],
        definition["source_node_id"],
        definition["map_binding"],
        definition["regions"],
    )
    report["region_source_digest"] = (
        "sha256:" + hashlib.sha256(report["definition_json"].encode()).hexdigest()
    )


def _record(
    node_id: str, entity_id: str, channel: str, schema: str, value: dict[str, Any]
) -> dict[str, Any]:
    """Construct a synthetic original Controller record with explicit independent source clocks."""
    return {
        "object": {"class": "world", "object_type": channel, "object_id": entity_id},
        "semantic": "reported",
        "source": f"node:{node_id}/physical-observer",
        "channel_id": channel,
        "stale": False,
        "value": {
            "payload_schema": schema,
            "value": value,
            "source_observed_at_ms": 999000,
            "received_at_ms": 1000,
            "valid_for_ms": 10000 if schema == ENTITY_SCHEMA else MAX_AGE_MS,
            "source_epoch": "synthetic-source-" + node_id,
            "sequence": 7,
        },
    }


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build two independent synthetic source reports without any live robot or copied map data."""
    records: list[dict[str, Any]] = []
    chassis = {}
    for index, assignment in enumerate(ASSIGNMENTS):
        node_id, entity_id = assignment.node_id, assignment.entity_id
        records.append(
            _record(
                node_id,
                entity_id,
                "physical-entity-reference",
                ENTITY_SCHEMA,
                {"entity_id": entity_id},
            )
        )
        binding = {
            "map_id": "synthetic-map-" + node_id,
            "mode": "localization",
            "generation": index,
            "source": "synthetic-confirmed-state",
        }
        regions = [
            {
                "annotation_id": "region-" + node_id,
                "kind": "region",
                "name": assignment.target_name,
                "points": [
                    [11.399093130268493 + index * 3, 0],
                    [12.399093130268493 + index * 3, 0],
                    [12.399093130268493 + index * 3, 1],
                ],
                "stale": False,
            }
        ]
        report = {
            "schema_version": SCHEMA,
            "physical_entity_id": entity_id,
            "source_node_id": node_id,
            "availability": "current",
            "sample_started_at_ns": 8000000000,
            "sample_completed_at_ns": 8100000000,
            "sample_age_ms": 100,
            "max_age_ms": MAX_AGE_MS,
            "remaining_at_publisher_ms": MAX_AGE_MS - 100,
            "definition_json": _definition_bytes(entity_id, node_id, binding, regions),
            "region_source_digest": "sha256:"
            + hashlib.sha256(
                _definition_bytes(entity_id, node_id, binding, regions).encode()
            ).hexdigest(),
            "source_documents": {
                "state": {
                    "path": "/api/state",
                    "body_sha256": "sha256:" + "1" * 64,
                    "confirmation_body_sha256": "sha256:" + "2" * 64,
                },
                "regions": {"path": "/api/regions", "body_sha256": "sha256:" + "3" * 64},
            },
            "localization_verified": False,
            "arrival_verified": False,
            "reason": "synthetic matching original state-regions-state",
        }
        records.append(_record(node_id, entity_id, "scene-targets", SCHEMA, report))
        chassis[node_id] = {
            "schema_version": "roboguide.reported-posture-inspection/v0.1",
            "node_id": node_id,
            "status": "reported",
            "posture": "standing",
            "motion_authorized": False,
            "physical_effect_verified": False,
            "evidence": {
                "source": f"node:{node_id}/robot-observer",
                "channel_id": "robot-state",
                "source_epoch": "synthetic-chassis-" + node_id,
                "sequence": 9,
                "controller_received_at_ms": 995,
                "controller_observed_at_ms": 1000,
                "remaining_valid_for_ms": 3900,
                "aggregate_age_plus_controller_retention_ms": 1100,
                "original_value": {"stale": False, "stale_ms": 5000, "posture": "standing"},
            },
        }
    state = {"schema": "roboguide.state-query/v0.1", "observed_at_ms": 1010, "records": records}
    inventory = {
        "schema_version": "roboguide.inventory/v0.1",
        "observed_at_ms": 1015,
        "nodes": [
            {"node_id": a.node_id, "liveness": "Reachable", "contracts": ["task.delegate@v1"]}
            for a in ASSIGNMENTS
        ],
    }
    registry = {
        "schema": "roboguide.physical-entity-registry/v0.1",
        "registry_id": "synthetic-physical-registry",
        "revision": 1,
        "routing_profile": "one-routable-entity-per-node",
        "entities": [{"entity_id": a.entity_id, "node_id": a.node_id} for a in ASSIGNMENTS],
    }
    return state, inventory, registry, chassis


class PatrolGroundingTests(unittest.TestCase):
    """Verify source identity, stable target provenance and conservative preparation authority."""

    def test_independent_targets_preserve_sources_and_create_no_authority(self) -> None:
        """Accepted source evidence yields two own-body targets and an unregistered verifier."""
        inputs = fixture()
        snapshot = build_patrol_snapshot(*inputs, local_read_elapsed_ms=12)
        self.assertEqual(snapshot["source_gaps"], [])
        self.assertEqual(len(snapshot["targets"]), 2)
        self.assertFalse(snapshot["arrival_verifier"]["registered"])
        self.assertFalse(snapshot["motion_authorized"])
        for assignment, target in zip(ASSIGNMENTS, snapshot["targets"], strict=True):
            self.assertEqual(target["node_id"], assignment.node_id)
            self.assertEqual(target["entity_id"], assignment.entity_id)
            self.assertFalse(target["localization_verified"])
            self.assertFalse(target["immutable_map_revision_verified"])
            self.assertEqual(target["region_source"]["remaining_after_known_delay_ms"], 14873)
            self.assertEqual(target["region_source"]["sequence"], 7)
            self.assertIn(
                arrival_predicate(snapshot, assignment), snapshot["arrival_verifier"]["predicates"]
            )
        self.assertEqual(
            snapshot["source_evidence"][ASSIGNMENTS[0].node_id]["physical_reference"],
            inputs[0]["records"][0],
        )
        assessment = assess_patrol_preflight(snapshot, observed_at_ms=1015)
        self.assertEqual(
            assessment["navigation_blockers"],
            [{"code": "independent_arrival_verifier_unavailable", "node_id": ""}],
        )
        self.assertFalse(assessment["navigation_ready"])

    def test_foreign_node_body_source_or_object_cannot_supply_peer_target(self) -> None:
        """One peer remains valid while every cross-body substitution loses the affected target."""
        for change in (
            "source",
            "object",
            "body-node",
            "body-entity",
            "body-entity-type",
            "schema",
            "channel",
        ):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                record = state["records"][1]
                if change == "source":
                    record["source"] = "node:lite3-dog2/physical-observer"
                elif change == "object":
                    record["object"]["object_id"] = ASSIGNMENTS[1].entity_id
                elif change == "body-node":
                    record["value"]["value"]["source_node_id"] = "lite3-dog2"
                elif change == "body-entity":
                    record["value"]["value"]["physical_entity_id"] = ASSIGNMENTS[1].entity_id
                elif change == "body-entity-type":
                    record["value"]["value"]["physical_entity_id"] = None
                elif change == "schema":
                    record["value"]["payload_schema"] = ENTITY_SCHEMA
                else:
                    record["channel_id"] = "another-scene-channel"
                result = build_patrol_snapshot(state, inventory, registry, chassis)
                expected_nodes = [] if change == "object" else ["lite3-dog2"]
                self.assertEqual([t["node_id"] for t in result["targets"]], expected_nodes)
                self.assertTrue(result["source_gaps"])
                self.assertEqual(result["arrival_verifier"]["predicates"], [])

    def test_outer_freshness_never_renews_expired_or_inflated_native_budget(self) -> None:
        """Native remaining lifetime is charged Controller retention and all supplied read time."""
        for change in ("expired", "inflated", "local-elapsed", "outer-expired", "clock-reversed"):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                record = state["records"][1]
                report = record["value"]["value"]
                elapsed = 0
                if change == "expired":
                    report["sample_age_ms"] = MAX_AGE_MS - 10
                    report["remaining_at_publisher_ms"] = 10
                elif change == "inflated":
                    report["remaining_at_publisher_ms"] = MAX_AGE_MS
                elif change == "local-elapsed":
                    report["sample_age_ms"] = MAX_AGE_MS - 100
                    report["remaining_at_publisher_ms"] = 100
                    elapsed = 90
                elif change == "outer-expired":
                    record["value"]["received_at_ms"] = 0
                else:
                    record["value"]["received_at_ms"] = 2000
                if change == "outer-expired":
                    record["value"]["valid_for_ms"] = 100
                result = build_patrol_snapshot(
                    state, inventory, registry, chassis, local_read_elapsed_ms=elapsed
                )
                self.assertNotIn("lite3-dog1", [t["node_id"] for t in result["targets"]])
                self.assertTrue(result["source_gaps"])

    def test_source_envelope_requires_original_epoch_sequence_ttl_and_no_ambiguity(self) -> None:
        """Malformed State provenance is refused independently from the nested Scene report."""
        for change in (
            "epoch",
            "sequence-bool",
            "sequence-zero",
            "ttl-bool",
            "ttl-long",
            "source-clock",
            "stale",
            "duplicate",
        ):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                record = state["records"][1]
                if change == "epoch":
                    record["value"]["source_epoch"] = ""
                elif change == "sequence-bool":
                    record["value"]["sequence"] = True
                elif change == "sequence-zero":
                    record["value"]["sequence"] = 0
                elif change == "ttl-bool":
                    record["value"]["valid_for_ms"] = True
                elif change == "ttl-long":
                    record["value"]["valid_for_ms"] = MAX_AGE_MS + 1
                elif change == "source-clock":
                    record["value"]["source_observed_at_ms"] = True
                elif change == "stale":
                    record["stale"] = True
                else:
                    state["records"].append(deepcopy(record))
                result = build_patrol_snapshot(state, inventory, registry, chassis)
                self.assertEqual([t["node_id"] for t in result["targets"]], ["lite3-dog2"])

    def test_definition_change_without_original_digest_cannot_change_arrival_predicate(
        self,
    ) -> None:
        """A modified polygon/map/region cannot ride on an earlier stable source digest."""
        for change in ("map", "polygon", "digest", "stale-region", "duplicate-name"):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                report = state["records"][1]["value"]["value"]
                definition = decode_scene_definition(report)
                if change == "map":
                    definition["map_binding"]["map_id"] = "different-map"
                    original_digest = report["region_source_digest"]
                    _replace_definition(report, definition)
                    report["region_source_digest"] = original_digest
                elif change == "polygon":
                    definition["regions"][0]["points"][0][0] = 99
                    original_digest = report["region_source_digest"]
                    _replace_definition(report, definition)
                    report["region_source_digest"] = original_digest
                elif change == "digest":
                    report["region_source_digest"] = "sha256:" + "f" * 64
                elif change == "stale-region":
                    definition["regions"][0]["stale"] = True
                    _replace_definition(report, definition)
                else:
                    other = deepcopy(definition["regions"][0])
                    other["annotation_id"] = "region-z"
                    definition["regions"].append(other)
                    _replace_definition(report, definition)
                result = build_patrol_snapshot(state, inventory, registry, chassis)
                self.assertEqual([t["node_id"] for t in result["targets"]], ["lite3-dog2"])

    def test_stable_definition_digest_keeps_predicate_across_new_observation_times(self) -> None:
        """New source times and counters do not change the original target definition identity."""
        first = fixture()
        second = deepcopy(first)
        record = second[0]["records"][1]
        record["value"]["sequence"] += 1
        report = record["value"]["value"]
        report["sample_started_at_ns"] += 1000000000
        report["sample_completed_at_ns"] += 1000000000
        report["sample_age_ms"] += 100
        report["remaining_at_publisher_ms"] -= 100
        before, after = build_patrol_snapshot(*first), build_patrol_snapshot(*second)
        self.assertEqual(
            before["arrival_verifier"]["predicates"], after["arrival_verifier"]["predicates"]
        )
        self.assertNotEqual(
            before["targets"][0]["region_source"]["sequence"],
            after["targets"][0]["region_source"]["sequence"],
        )

    def test_later_inventory_cannot_hide_an_invalid_original_state_clock(self) -> None:
        """A record after its own State projection time is refused despite a later valid read."""
        state, inventory, registry, chassis = fixture()
        state["records"][1]["value"]["received_at_ms"] = 1012
        result = build_patrol_snapshot(state, inventory, registry, chassis)
        self.assertEqual([t["node_id"] for t in result["targets"]], ["lite3-dog2"])
        self.assertTrue(any("clock_reversed" in gap["code"] for gap in result["source_gaps"]))

    def test_nullable_native_binding_remains_reported_without_upgrading_navigation(self) -> None:
        """Keep unknown binding fields as reported data, without proving localization."""
        for mode in ("", None):
            with self.subTest(mode=mode):
                state, inventory, registry, chassis = fixture()
                report = state["records"][1]["value"]["value"]
                definition = decode_scene_definition(report)
                definition["map_binding"].update(mode=mode, generation=None, source="env")
                _replace_definition(report, definition)
                snapshot = build_patrol_snapshot(state, inventory, registry, chassis)
                target = snapshot["targets"][0]
                original = decode_scene_definition(target["region_source"]["original_report"])[
                    "map_binding"
                ]
                self.assertIsNone(original["generation"])
                self.assertEqual(original["mode"], mode)
                self.assertEqual(original["source"], "env")
                self.assertFalse(target["localization_verified"])
                self.assertFalse(target["immutable_map_revision_verified"])
                self.assertFalse(target["map_binding_atomicity_verified"])
                self.assertEqual(
                    target["map_binding_verification_scope"],
                    "same-node-state-regions-state-source-consistency",
                )
                self.assertFalse(snapshot["motion_authorized"])
                self.assertFalse(assess_patrol_preflight(snapshot)["navigation_ready"])

    def test_original_utf8_definition_preserves_real_float_digits_and_refuses_tampering(
        self,
    ) -> None:
        """Hash the original string so transport number formatting cannot change target identity."""
        inputs = fixture()
        original = inputs[0]["records"][1]["value"]["value"]
        self.assertIn("11.399093130268493", original["definition_json"])
        snapshot = build_patrol_snapshot(*inputs)
        target = snapshot["targets"][0]
        retained = target["region_source"]["original_report"]["definition_json"]
        self.assertEqual(retained.encode("utf-8"), original["definition_json"].encode("utf-8"))
        self.assertEqual(target["region"]["points"][0][0], 11.399093130268493)
        self.assertEqual(
            target["region_source_digest"],
            "sha256:" + hashlib.sha256(retained.encode()).hexdigest(),
        )
        original["definition_json"] = original["definition_json"].replace(
            "11.399093130268493", "11.399093130268494"
        )
        refused = build_patrol_snapshot(*inputs)
        self.assertEqual([t["node_id"] for t in refused["targets"]], ["lite3-dog2"])

    def test_optional_source_time_stays_unknown_without_renewing_native_budget(self) -> None:
        """Valid absent publisher timestamps remain null; freshness uses original local budgets."""
        state, inventory, registry, chassis = fixture()
        for record in state["records"]:
            record["value"]["source_observed_at_ms"] = None
        snapshot = build_patrol_snapshot(state, inventory, registry, chassis)
        self.assertEqual(snapshot["source_gaps"], [])
        self.assertEqual(len(snapshot["targets"]), 2)
        self.assertIsNone(snapshot["targets"][0]["region_source"]["source_observed_at_ms"])
        record = state["records"][1]
        record["value"]["value"].update(sample_age_ms=MAX_AGE_MS - 10, remaining_at_publisher_ms=10)
        expired = build_patrol_snapshot(state, inventory, registry, chassis)
        self.assertEqual([t["node_id"] for t in expired["targets"]], ["lite3-dog2"])

    def test_physical_reference_and_registry_are_both_required(self) -> None:
        """Current labels do not invent Control routing or another body's World identity."""
        for change in (
            "reference-body",
            "reference-node",
            "registry-route",
            "registry-duplicate",
            "node-unreachable",
        ):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                if change == "reference-body":
                    state["records"][0]["value"]["value"]["entity_id"] = "another-entity"
                elif change == "reference-node":
                    state["records"][0]["source"] = "node:lite3-dog2/physical-observer"
                elif change == "registry-route":
                    registry["entities"][0]["node_id"] = "another-node"
                elif change == "registry-duplicate":
                    registry["entities"].append(deepcopy(registry["entities"][0]))
                else:
                    inventory["nodes"][0]["liveness"] = "Unreachable"
                result = build_patrol_snapshot(state, inventory, registry, chassis)
                self.assertNotIn("lite3-dog1", [t["node_id"] for t in result["targets"]])
                self.assertTrue(result["source_gaps"])

    def test_foreign_expired_or_inflated_posture_does_not_become_current(self) -> None:
        """Keep a known target while refusing an invalid chassis diagnostic for readiness."""
        for change in (
            "node",
            "source",
            "expired",
            "inflated",
            "original-posture",
            "age",
            "authority",
        ):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                report = chassis["lite3-dog1"]
                if change == "node":
                    report["node_id"] = "lite3-dog2"
                elif change == "source":
                    report["evidence"]["source"] = "node:lite3-dog2/robot-observer"
                elif change == "expired":
                    report["evidence"]["remaining_valid_for_ms"] = 1
                elif change == "inflated":
                    report["evidence"]["remaining_valid_for_ms"] = 4999
                elif change == "original-posture":
                    report["evidence"]["original_value"]["posture"] = "down"
                elif change == "authority":
                    report["motion_authorized"] = True
                else:
                    report["evidence"]["aggregate_age_plus_controller_retention_ms"] = float("inf")
                if change == "age":
                    with self.assertRaises(PatrolGroundingError):
                        build_patrol_snapshot(state, inventory, registry, chassis)
                    continue
                result = build_patrol_snapshot(state, inventory, registry, chassis)
                self.assertNotIn("lite3-dog1", result["chassis"])
                self.assertEqual(len(result["targets"]), 2)
                self.assertIn(
                    {"code": "fresh_reported_chassis_unavailable", "node_id": "lite3-dog1"},
                    assess_patrol_preflight(result)["navigation_blockers"],
                )

    def test_source_inputs_and_original_clocks_remain_unchanged(self) -> None:
        """Derived budget charging and caller mutations cannot overwrite original observations."""
        inputs = fixture()
        before = deepcopy(inputs)
        result = build_patrol_snapshot(*inputs, local_read_elapsed_ms=100)
        self.assertEqual(inputs, before)
        diagnostic = result["chassis"]["lite3-dog1"]
        self.assertEqual(diagnostic["evidence"]["remaining_valid_for_ms"], 3800)
        self.assertEqual(
            diagnostic["original_inspection"]["evidence"]["remaining_valid_for_ms"], 3900
        )
        result["targets"][0]["region"]["points"][0][0] = 900
        result["physical_registry"]["entities"].clear()
        self.assertEqual(inputs, before)

    def test_verifier_template_is_disabled_unpublished_and_requires_both_targets(self) -> None:
        """Preparing target-bound predicates never enrolls a producer or publishes a verifier."""
        snapshot = build_patrol_snapshot(*fixture())
        args: dict[str, Any] = {
            "registry_id": "synthetic-registry",
            "source_id": "synthetic-onsite-source",
            "tenant_id": "synthetic-tenant",
            "producer_principal": "synthetic-onsite-principal",
        }
        template = arrival_registry_template(snapshot, **args)
        self.assertFalse(template["registered"])
        self.assertFalse(template["published"])
        self.assertFalse(template["producer_identity_enrolled"])
        source = template["registry"]["sources"][args["source_id"]]
        self.assertFalse(source["enabled"])
        self.assertEqual(source["predicates"], snapshot["arrival_verifier"]["predicates"])
        self.assertEqual(source["producer_principals"], [args["producer_principal"]])
        self.assertNotIn("command_id", template)
        snapshot["targets"].pop()
        with self.assertRaises(PatrolGroundingError):
            arrival_registry_template(snapshot, **args)

    def test_unqualified_document_or_elapsed_clock_is_not_defaulted_to_fresh(self) -> None:
        """Missing central clocks and malformed local durations fail before source selection."""
        for change in (
            "world-schema",
            "missing-now",
            "local-negative",
            "local-bool",
            "local-float",
        ):
            with self.subTest(change=change):
                state, inventory, registry, chassis = fixture()
                elapsed: Any = 0
                if change == "world-schema":
                    state["schema"] = "roboguide.node-query/v0.1"
                elif change == "missing-now":
                    state.pop("observed_at_ms")
                elif change == "local-negative":
                    elapsed = -1
                elif change == "local-bool":
                    elapsed = True
                else:
                    elapsed = 0.1
                with self.assertRaises(PatrolGroundingError):
                    build_patrol_snapshot(
                        state, inventory, registry, chassis, local_read_elapsed_ms=elapsed
                    )


if __name__ == "__main__":
    unittest.main()
