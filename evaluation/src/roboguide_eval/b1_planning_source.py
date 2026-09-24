"""Freeze and preflight the B1 deployment's required planning-world source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mission.planning_world_evidence import (
    AuthoritativePlanningWorldEvidence,
    PlanningWorldEvidenceError,
)

from roboguide_eval.b1_provenance import (
    PLANNING_SOURCE_ARTIFACT,
    PLANNING_SOURCE_REQUIREMENT_ARTIFACT,
    load_document,
    required_planning_source,
)
from roboguide_eval.b1_workload import extract_b1_workload


def freeze_source(run: Path) -> None:
    """Persist the requirement before any SUT component can accept a request."""
    marker = {
        "schema_version": "roboguide.e1.planning-world-source-requirement/v0.1",
        "required": True,
        "artifact": "planning-world-source.json",
    }
    (run / PLANNING_SOURCE_REQUIREMENT_ARTIFACT).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    frozen = load_document(run / "b1-input-used.json")
    source = required_planning_source(run.name, frozen)
    (run / "planning-world-source.json").write_text(
        json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def preflight_artifact(run: Path) -> None:
    """Reject a missing or different-world source before Mission Intelligence runs."""
    frozen = load_document(run / "b1-input-used.json")
    workload = extract_b1_workload(frozen)
    if load_document(run / "planning-world-source.json") != required_planning_source(
        run.name, frozen
    ):
        raise PlanningWorldEvidenceError("required planning source declaration is invalid")
    document = load_document(run / PLANNING_SOURCE_ARTIFACT)
    evidence = AuthoritativePlanningWorldEvidence.from_json(document)
    if (
        evidence.run_id != run.name
        or evidence.episode_id != workload.episode_id
        or evidence.scene_id != workload.scene_id
        or evidence.dataset_revision != workload.dataset_revision
        or evidence.dataset_sha256 != workload.dataset_sha256
    ):
        raise PlanningWorldEvidenceError("planning world identity differs from frozen B1 input")


def main() -> None:
    """Create or preflight one run-local required source without invoking the SUT."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--check-artifact", action="store_true")
    args = parser.parse_args()
    if args.check_artifact:
        preflight_artifact(args.run)
    else:
        freeze_source(args.run)


if __name__ == "__main__":
    main()
