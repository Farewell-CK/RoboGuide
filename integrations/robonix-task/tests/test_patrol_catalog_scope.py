"""Keep fixed-patrol evidence requirements scoped without inventing local motion support."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

import jsonschema


class PatrolCatalogScopeTests(unittest.TestCase):
    """Validate the actual deployment artifact and its distinct evidence profiles."""

    def setUp(self) -> None:
        """Read the shipped catalog and its declared version's canonical schema."""
        repository = Path(__file__).resolve().parents[3]
        self.catalog: dict[str, Any] = json.loads(
            (
                repository / "integrations/robonix-task/deploy/patrol-capability-catalog.json"
            ).read_text(encoding="utf-8")
        )
        self.schema: dict[str, Any] = json.loads(
            (repository / "contracts/capability/v0.3/catalog.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.operation = self.catalog["operations"][0]

    def test_catalog_validates_its_declared_schema(self) -> None:
        """A descriptive policy fix must retain the actual closed v0.3 artifact format."""
        jsonschema.Draft202012Validator.check_schema(self.schema)
        jsonschema.validate(self.catalog, self.schema)
        self.assertEqual(
            self.catalog["schema_version"], self.schema["properties"]["schema_version"]["const"]
        )
        changed = dict(self.catalog, schema_version="roboguide.capability-catalog/v0.4")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(changed, self.schema)

    def test_vocabulary_and_receiver_parameters_are_preserved(self) -> None:
        """The scope correction cannot remove capabilities or introduce a motion operation."""
        delegate = {"namespace": "task", "name": "delegate", "version": "v1"}
        arrival = {"namespace": "observation.patrol", "name": "verify-arrival", "version": "v1"}
        self.assertEqual(
            [capability["contract"] for capability in self.catalog["capabilities"]],
            [delegate, arrival],
        )
        self.assertEqual(len(self.catalog["operations"]), 1)
        self.assertEqual(self.operation["operation"], delegate)
        self.assertEqual(self.operation["required_capabilities"], [delegate])
        self.assertEqual(
            self.operation["parameters"],
            [
                {
                    "name": "profile",
                    "type": "string",
                    "required": True,
                    "description": (
                        "Exact deployed local-task-v1 profile, with no extra motion parameters."
                    ),
                }
            ],
        )
        self.assertEqual(
            self.catalog["capabilities"][0]["attributes"],
            [
                {
                    "name": "task-profile",
                    "type": "string",
                    "description": "Exact supported local-task-v1 receiver profile.",
                }
            ],
        )

    def test_generic_delegation_does_not_require_a_patrol_verifier(self) -> None:
        """Delegation describes satisfaction limits without imposing a patrol-only identity."""
        general, remainder = self.operation["description"].split(
            "Fixed named-target patrol profile:", 1
        )
        self.assertIn(
            "Every Task declares a satisfaction basis appropriate to its stated goal", general
        )
        self.assertIn("execution-report completes the delegated execution lifecycle only", general)
        self.assertIn("shared-zone clearance or device stop", general)
        self.assertNotIn("observation.patrol.verify-arrival@v1", general)
        self.assertNotIn("Navigation Tasks must use", self.operation["description"])
        self.assertIn("already accepted Tasks", remainder)

    def test_fixed_patrol_retains_the_exact_evidence_boundary(self) -> None:
        """Named-target patrol retains the source, target, entity and freshness obligations."""
        patrol = (
            self.operation["description"]
            .split("Fixed named-target patrol profile:", 1)[1]
            .split("Other objectives:", 1)[0]
        )
        for obligation in (
            "dog1 to the tea-room entrance",
            "dog2 to Professor Wang's office entrance",
            "These fixed named-target patrol Tasks must use verifier-evidence",
            "observation.patrol.verify-arrival@v1",
            "independent onsite source",
            "exact deployment target-reference predicate binding the target and physical entity",
            "5000 ms receive-age bound",
            "independently verified local map/Scene provenance",
            "onsite motion authorization prevent submission of this patrol profile",
        ):
            with self.subTest(obligation=obligation):
                self.assertIn(obligation, patrol)

    def test_manual_source_and_unsupported_bounded_motion_stay_explicit(self) -> None:
        """Registration cannot create localization, motion, clearance or recovery authority."""
        source = self.catalog["capabilities"][1]["description"]
        self.assertIn("explicit human onsite confirmation", source)
        self.assertIn("restricted verifier identity", source)
        self.assertIn(
            "does not claim automatic localization or autonomous arrival assessment", source
        )
        self.assertIn("device-stop/recovery authority", source)
        other = self.operation["description"].split("Other objectives:", 1)[1]
        self.assertIn("0.5 m body-forward movement contract has not been verified", other)
        self.assertIn("keep that objective blocked", other)
        self.assertIn("does not make bounded movement executable", other)
        self.assertIn("Do not invent coordinate, map, stop, relative-step", other)


if __name__ == "__main__":
    unittest.main()
