"""Keep explicit verifier deployment metadata precise without creating execution authority."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import jsonschema
from robonix_task.arrival_verifier import ArrivalVerificationError, ArrivalVerifier
from robonix_task.arrival_verifier_store import digest, encode
from robonix_task.patrol import ARRIVAL_CONTRACT
from robonix_task.verifier_catalog import MARKER, VerifierCatalogError, build_arrival_catalog


class VerifierCatalogTests(unittest.TestCase):
    """Exercise exact source/config equality and downstream canonical Catalog serialization."""

    def setUp(self) -> None:
        """Build explicit invented references and the shipped actual v0.3 Catalog."""
        self.repository = Path(__file__).resolve().parents[3]
        self.catalog: dict[str, Any] = json.loads(
            (
                self.repository / "integrations/robonix-task/deploy/patrol-capability-catalog.json"
            ).read_text(encoding="utf-8")
        )
        target = {
            "entity_id": "entity-dog1",
            "node_id": "dog1",
            "target_name": "茶水间门口",
            "map_id": "software-map",
            "region_id": "software-region",
            "region_source_digest": "sha256:" + "1" * 64,
        }
        target["predicate"] = (
            "onsite-arrival/v0.1 entity-dog1 arrived-and-stopped at 茶水间门口 target-reference-"
            + digest(target)
        )
        self.config: dict[str, Any] = {
            "schema_version": "roboguide.onsite-arrival-verifier/v0.1",
            "source_id": "manual-arrival",
            "revision": 1,
            "tenant_id": "local",
            "producer_principal": "software-observer",
            "verifier": dict(ARRIVAL_CONTRACT),
            "max_age_ms": 5000,
            "targets": [target],
        }
        self.source: dict[str, Any] = {
            "source_id": "manual-arrival",
            "revision": 1,
            "tenant_id": "local",
            "producer_principals": ["software-observer"],
            "verifier": dict(ARRIVAL_CONTRACT),
            "predicates": [target["predicate"]],
            "max_age_ms": 5000,
            "stop_timeout_ms": 5000,
            "enabled": True,
            "continuous": False,
            "require_artifact": False,
        }
        self.view: dict[str, Any] = {
            "schema_version": "roboguide.semantic-verifier-view/v0.1",
            "registry": {
                "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                "registry_id": "software-registry",
                "revision": 3,
                "sources": {"manual-arrival": self.source},
            },
        }

    def build(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Convert only the retained synthetic source using the public pure API."""
        return build_arrival_catalog(self.catalog, self.view, self.config)

    def test_actual_schema_and_exact_literal_predicate_are_preserved(self) -> None:
        """Embed the original exact contract without inventing a new Catalog generation."""
        catalog, provenance = self.build()
        schema = json.loads(
            (self.repository / "contracts/capability/v0.3/catalog.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.validate(catalog, schema)
        description = catalog["capabilities"][1]["description"]
        snapshot = json.loads(description.split(MARKER, 1)[1])
        self.assertEqual(
            snapshot["schema_version"], "roboguide.deployment-verifier-catalog-snapshot/v0.2"
        )
        self.assertEqual(snapshot["source"], self.source)
        self.assertEqual(snapshot["target_references"], self.config["targets"])
        self.assertIn(self.source["predicates"][0], description)
        self.assertEqual(provenance["registry_digest"], digest(self.view["registry"]))
        self.assertEqual(provenance["source_config_digest"], digest(self.config))
        self.assertEqual(provenance["output_catalog_digest"], digest(catalog))
        self.assertFalse(provenance["transport_authenticated_by_converter"])
        self.assertFalse(snapshot["motion_authorized"])
        self.assertFalse(snapshot["automatic_arrival_assessment"])

    def test_declared_satisfaction_passes_the_actual_manual_publisher_guard(self) -> None:
        """Accept the declared exact goal while rejecting an unsupported joint final-state claim."""
        catalog, _ = self.build()
        snapshot = json.loads(catalog["capabilities"][1]["description"].split(MARKER, 1)[1])
        descriptors = snapshot["supported_task_satisfactions"]
        self.assertEqual(
            {d["expected_effect"] for d in descriptors}, set(self.source["predicates"])
        )
        for descriptor in descriptors:
            self.assertEqual(descriptor["expected_effect"], descriptor["verifier"]["predicate"])
            self.assertEqual(descriptor["verifier"]["max_evidence_age_ms"], 5000)
            self.assertIn(self.config["targets"][0]["entity_id"], descriptor["expected_effect"])
        self.assertIn("do not prove simultaneous", snapshot["binding_policy"])
        plan: dict[str, Any] = {
            "schema_version": "roboguide.mission-plan/v0.10",
            "mission": {
                "id": "mission",
                "actors": [
                    {"id": "dog", "physical_entity": self.config["targets"][0]["entity_id"]}
                ],
            },
            "contexts": [{"id": "context", "roles": [{"id": "member", "actor": "dog"}]}],
            "tasks": [
                {
                    "id": "navigate",
                    "context_id": "context",
                    "roles": [{"id": "role", "context_role": "member"}],
                    "satisfaction": descriptors[0],
                }
            ],
        }
        view: dict[str, Any] = {
            "tenant": {
                "mission_id": "mission",
                "tenant_id": "local",
                "source_plan_digest": "sha256:" + "2" * 64,
            },
            "admission": {"mission_id": "mission", "group_id": "group"},
            "mission": {
                "mission_id": "mission",
                "group_id": "group",
                "plan_revision": 0,
                "tasks": [
                    {
                        "task_id": "navigate",
                        "context_id": "context",
                        "status": "AwaitingSatisfaction",
                    }
                ],
            },
            "attempts": [
                {
                    "task_id": "navigate",
                    "mission_id": "mission",
                    "group_id": "group",
                    "role_id": "role",
                    "execution_id": "attempt",
                    "node_id": "dog1",
                    "status": "Completed",
                }
            ],
        }
        controller = Mock()
        verifier = ArrivalVerifier(self.config, controller, None)
        for joint_goal in (False, True):
            if joint_goal:
                plan["tasks"][0]["satisfaction"]["expected_effect"] = "both dogs remain together"
            raw = encode(plan).encode()
            view["admission"]["accepted_request_body_sha256"] = (
                "sha256:" + hashlib.sha256(raw).hexdigest()
            )
            controller.arrival_view.return_value = view
            if joint_goal:
                with self.assertRaisesRegex(ArrivalVerificationError, "pending_exact"):
                    verifier.inspect(raw, "mission", "navigate")
            else:
                self.assertEqual(
                    verifier.inspect(raw, "mission", "navigate")["predicate"],
                    self.source["predicates"][0],
                )

    def test_only_one_existing_description_changes_and_inputs_are_detached(self) -> None:
        """Vocabulary, operations, parameters and caller-owned documents stay intact."""
        before = deepcopy([self.catalog, self.view, self.config])
        catalog, provenance = self.build()
        changed = deepcopy(catalog)
        changed["capabilities"][1]["description"] = self.catalog["capabilities"][1]["description"]
        self.assertEqual(changed, self.catalog)
        self.assertEqual(before, [self.catalog, self.view, self.config])
        catalog["operations"].clear()
        self.assertTrue(self.catalog["operations"])
        self.assertEqual(provenance["input_catalog_digest"], digest(self.catalog))

    def test_changed_source_contracts_are_rejected(self) -> None:
        """A registered name cannot stand in for exact revision, producer and predicate grants."""
        for field, value in {
            "revision": 2,
            "tenant_id": "foreign",
            "producer_principals": ["foreign"],
            "predicates": ["changed-target"],
            "max_age_ms": 10000,
            "enabled": False,
            "continuous": True,
            "require_artifact": True,
            "stop_timeout_ms": 100,
        }.items():
            with self.subTest(field=field):
                view = deepcopy(self.view)
                view["registry"]["sources"]["manual-arrival"][field] = value
                with self.assertRaisesRegex(VerifierCatalogError, "differs"):
                    build_arrival_catalog(self.catalog, view, self.config)

    def test_bad_original_schema_fields_and_boolean_integers_are_rejected(self) -> None:
        """Unknown codecs or malformed unrelated sources cannot enter a publication."""
        for path, value in [
            (["schema_version"], "future"),
            (["registry"], None),
            (["registry", "revision"], True),
            (["registry", "sources", "manual-arrival", "revision"], True),
            (["registry", "sources", "manual-arrival", "producer_principals"], ["x", "x"]),
            (["registry", "sources", "manual-arrival", "enabled"], 1),
            (["registry", "sources", "manual-arrival", "extra"], "invented"),
        ]:
            view = deepcopy(self.view)
            parent = view
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(VerifierCatalogError):
                build_arrival_catalog(self.catalog, view, self.config)

    def test_target_digest_and_duplicate_or_absent_capability_are_rejected(self) -> None:
        """Publication cannot repair a changed target or add a missing verifier vocabulary."""
        config = deepcopy(self.config)
        config["targets"][0]["map_id"] = "changed-map"
        with self.assertRaises(VerifierCatalogError):
            build_arrival_catalog(self.catalog, self.view, config)
        for capabilities in [self.catalog["capabilities"][:1], self.catalog["capabilities"] * 2]:
            with self.assertRaises(VerifierCatalogError):
                build_arrival_catalog(
                    dict(self.catalog, capabilities=capabilities), self.view, self.config
                )

    def test_ambiguous_sources_and_reenrichment_are_rejected(self) -> None:
        """No duplicate eligible source or stale enriched base can silently change metadata."""
        peer = deepcopy(self.source)
        peer["source_id"] = "ambiguous"
        self.view["registry"]["sources"]["ambiguous"] = peer
        with self.assertRaisesRegex(VerifierCatalogError, "ambiguous"):
            self.build()
        del self.view["registry"]["sources"]["ambiguous"]
        catalog, _ = self.build()
        for revision in [1, 2]:
            config = deepcopy(self.config)
            config["revision"] = revision
            with self.assertRaises(VerifierCatalogError):
                build_arrival_catalog(catalog, self.view, config)

    def test_foreign_sources_are_not_exposed_but_original_digest_covers_them(self) -> None:
        """Selected tenant metadata never copies other tenants' identities or predicate strings."""
        peer = deepcopy(self.source)
        peer.update(
            source_id="foreign",
            tenant_id="secret-tenant",
            producer_principals=["secret-principal"],
            predicates=["secret-predicate"],
        )
        self.view["registry"]["sources"]["foreign"] = peer
        catalog, provenance = self.build()
        self.assertNotIn("secret-", json.dumps([catalog, provenance]))
        self.assertEqual(provenance["registry_view_digest"], digest(self.view))

    @unittest.skipIf(sys.platform == "win32", "real MI package requires its Linux runtime")
    def test_actual_mi_canonical_serialization_retains_declaration(self) -> None:
        """Exercise the production model-input serializer, not a copied JSON projection."""
        from mission.capability_catalog import CanonicalCapabilityCatalog

        catalog, _ = self.build()
        canonical = CanonicalCapabilityCatalog.from_json(catalog).to_json()
        selected = next(c for c in canonical["capabilities"] if c["contract"] == ARRIVAL_CONTRACT)
        self.assertEqual(selected["description"], catalog["capabilities"][1]["description"])


if __name__ == "__main__":
    unittest.main()
