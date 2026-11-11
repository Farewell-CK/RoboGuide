"""Check exact admitted MI plan exports without contacting a Controller or robot."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from mission.models import MissionPlan
from mission.submission_evidence import canonical_plan_digest
from robonix_task.arrival_plan import (
    MAX_BYTES,
    ArrivalPlanError,
    encode_plan_for_arrival,
    save_plan,
)
from robonix_task.patrol import ASSIGNMENTS, build_patrol_plan


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Construct explicit synthetic targets and genuine MI-codec admission-shaped documents."""
    snapshot = {
        "targets": [
            {
                "entity_id": assignment.entity_id,
                "node_id": assignment.node_id,
                "target_name": assignment.target_name,
                "map_id": "synthetic-map",
                "region_id": "synthetic-region-" + str(index),
                "region_source_digest": "sha256:" + str(index + 1) * 64,
            }
            for index, assignment in enumerate(ASSIGNMENTS)
        ]
    }
    document = MissionPlan.from_json(build_patrol_plan(snapshot, "export-fixture")).to_json()
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    admission = {
        "schema_version": "roboguide.controller-mission-admission/v0.1",
        "mission_id": "export-fixture",
        "group_id": "group-export-fixture",
        "accepted_request_body_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "admitted_at_ms": 1234,
    }
    tenant = {
        "mission_id": "export-fixture",
        "tenant_id": "local",
        "source_plan_digest": "sha256:"
        + hashlib.sha256(b"synthetic internal domain MissionPlan, not public MI JSON").hexdigest(),
    }
    return document, admission, tenant, raw


class ArrivalPlanTests(unittest.TestCase):
    """Keep exported bytes, original identities and local evidence ownership exact."""

    def setUp(self) -> None:
        """Prepare independent immutable original authority documents for each case."""
        self.plan, self.admission, self.tenant, self.raw = fixture()

    def test_real_mi_codec_and_nonascii_bytes_match(self) -> None:
        """Retain the exact compact UTF-8 encoding used by HttpMissionController."""
        actual = encode_plan_for_arrival(self.plan, self.admission, self.tenant)
        self.assertEqual(actual, self.raw)
        self.assertIn("茶水间门口".encode(), actual)
        self.assertNotIn(b"\\u8336", actual)

    def test_input_key_order_is_not_mistaken_for_wire_authority(self) -> None:
        """Parse through the original model instead of dumping caller-controlled object order."""
        changed = dict(reversed(list(self.plan.items())))
        self.assertEqual(encode_plan_for_arrival(changed, self.admission, self.tenant), self.raw)

    def test_wrong_wire_digest_and_pretty_printed_admission_are_rejected(self) -> None:
        """Semantically equal pretty JSON cannot replace the original accepted wire identity."""
        for raw in (b"other original body", json.dumps(self.plan, indent=2).encode()):
            admission = {
                **self.admission,
                "accepted_request_body_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
            with self.subTest(raw=raw[:20]), self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(self.plan, admission, self.tenant)

    def test_changed_goal_and_physical_actor_are_rejected(self) -> None:
        """Changed objectives or entity bindings cannot be exported under old admission."""
        for field in ("objective", "entity"):
            plan = deepcopy(self.plan)
            if field == "objective":
                plan["mission"]["objective"] = "changed goal"
            else:
                plan["mission"]["actors"][0]["physical_entity"] = "different-dog"
            with self.subTest(field=field), self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(plan, self.admission, self.tenant)

    def test_wrong_mission_identity_or_malformed_tenant_token_is_rejected(self) -> None:
        """Both original views must identify the same Mission and a qualified Core source token."""
        for key, value in (
            ("mission_id", "foreign-mission"),
            ("source_plan_digest", "unqualified-token"),
            ("source_plan_digest", None),
        ):
            with self.subTest(key=key), self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(self.plan, self.admission, {**self.tenant, key: value})
        with self.assertRaises(ArrivalPlanError):
            encode_plan_for_arrival(
                self.plan, {**self.admission, "mission_id": "foreign"}, self.tenant
            )

    def test_core_source_token_is_not_mi_public_canonical_digest(self) -> None:
        """Do not compare hashes of Core's internal plan and MI's distinct public document."""
        self.assertNotEqual(self.tenant["source_plan_digest"], canonical_plan_digest(self.plan))
        self.assertEqual(encode_plan_for_arrival(self.plan, self.admission, self.tenant), self.raw)
        # The offline helper cannot authenticate or derive an arbitrary Core token.
        # The caller supplies authenticated projections; the actual publisher and
        # Controller revalidate current authority instead of trusting this export.
        tenant = {**self.tenant, "source_plan_digest": "sha256:" + "a" * 64}
        self.assertEqual(encode_plan_for_arrival(self.plan, self.admission, tenant), self.raw)

    def test_unscoped_or_missing_tenant_is_rejected(self) -> None:
        """An export cannot infer a tenant or promote a wildcard into exact task ownership."""
        for tenant_id in (None, "", "*", " local", "local\n"):
            with self.subTest(tenant_id=tenant_id), self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(
                    self.plan, self.admission, {**self.tenant, "tenant_id": tenant_id}
                )

    def test_explicit_nonzero_or_boolean_revision_is_rejected(self) -> None:
        """A caller providing new-revision metadata cannot use a revision-zero arrival export."""
        for revision in (1, True, -1, "0"):
            for source in ("admission", "tenant"):
                admission, tenant = deepcopy(self.admission), deepcopy(self.tenant)
                (admission if source == "admission" else tenant)["plan_revision"] = revision
                with (
                    self.subTest(source=source, revision=revision),
                    self.assertRaises(ArrivalPlanError),
                ):
                    encode_plan_for_arrival(self.plan, admission, tenant)

    def test_malformed_old_admission_and_unbounded_inputs_fail_closed(self) -> None:
        """Missing authority, old generations and unbounded/nonfinite input fail locally."""
        for admission in ({}, {**self.admission, "schema_version": "old"}):
            with self.subTest(admission=admission), self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(self.plan, admission, self.tenant)
        for extra in ("x" * (MAX_BYTES + 1), float("nan")):
            plan = {**self.plan, "unaccepted": extra}
            with self.assertRaises(ArrivalPlanError):
                encode_plan_for_arrival(plan, self.admission, self.tenant)

    def test_input_documents_are_unchanged(self) -> None:
        """Export validation neither renews source metadata nor mutates the original documents."""
        original = deepcopy((self.plan, self.admission, self.tenant))
        encode_plan_for_arrival(self.plan, self.admission, self.tenant)
        self.assertEqual((self.plan, self.admission, self.tenant), original)

    def test_exclusive_save_keeps_existing_evidence_and_writes_no_bad_export(self) -> None:
        """Create only the requested fresh file and refuse overwrite or invalid authority."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accepted-plan.raw.json"
            self.assertEqual(save_plan(self.plan, self.admission, self.tenant, path), path)
            self.assertEqual(path.read_bytes(), self.raw)
            with self.assertRaises(FileExistsError):
                save_plan(self.plan, self.admission, self.tenant, path)
            rejected = Path(directory) / "rejected.json"
            with self.assertRaises(ArrivalPlanError):
                save_plan(self.plan, {}, self.tenant, rejected)
            self.assertFalse(rejected.exists())
            self.assertEqual(path.read_bytes(), self.raw)


if __name__ == "__main__":
    unittest.main()
