"""B1 provenance checks for versioned pre-reset planning-world evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import build_provenance, make_run, write_json
from roboguide_eval.b1_provenance import plan_digest
from roboguide_eval.b1_run import assess_b1_directory


def _update_planning_world_evidence(run: Path, document: dict[str, Any]) -> None:
    """Rehash every request link to isolate the evaluator's inner identity check."""
    document["digest"] = plan_digest(
        {key: value for key, value in document.items() if key != "digest"}
    )
    write_json(run / "evidence/authoritative-planning-world-evidence.json", document)
    request = json.loads((run / "b1-request-record.json").read_text(encoding="utf-8"))
    context = request["grounding_context"]
    context["planning_world_evidence"] = document
    context["context_digest"] = plan_digest(
        {
            key: value
            for key, value in context.items()
            if key not in {"schema_version", "context_digest"}
        }
    )
    write_json(run / "b1-request-record.json", request)
    observations = json.loads((run / "b1-request-observations.json").read_text(encoding="utf-8"))
    observations["request_record_digest"] = plan_digest(request)
    write_json(run / "b1-request-observations.json", observations)
    build_provenance(run)


def test_v03_planning_world_evidence_preserves_b1_admission_and_goal_diagnostic(
    tmp_path: Path,
) -> None:
    """An actual MI v0.3 request passes provenance without making goal coverage admission."""
    run = make_run(tmp_path, planning_world=True, omit_second_goal=True)
    request = json.loads((run / "b1-request-record.json").read_text(encoding="utf-8"))
    assert request["grounding_context"]["schema_version"] == "roboguide.grounding-context/v0.3"
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["provenance_valid"] is True
    assert verdict["admission"]["valid_for_formal_population"] is True
    assert verdict["context"]["semantic_goal_diagnostic"][0]["status"] == "partial"


def test_missing_or_tampered_planning_world_artifact_fails_closed(tmp_path: Path) -> None:
    """MI's embedded world facts cannot substitute for missing or altered adapter evidence."""
    run = make_run(tmp_path, planning_world=True)
    path = run / "evidence/authoritative-planning-world-evidence.json"
    original = path.read_bytes()
    path.unlink()
    verdict = assess_b1_directory(run)
    assert verdict["context"]["provenance_failures"] == ["planning_world_evidence_missing"]
    assert verdict["admission"]["valid_for_formal_population"] is False

    path.write_bytes(original)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["facts"][0]["floor_id"] = "different-floor"
    write_json(path, document)
    verdict = assess_b1_directory(run)
    assert verdict["context"]["provenance_failures"] == ["planning_world_evidence_invalid"]


@pytest.mark.parametrize("field", ["episode_id", "scene_id", "dataset_revision", "dataset_sha256"])
def test_rehashed_planning_world_identity_cannot_cross_frozen_input(
    tmp_path: Path, field: str
) -> None:
    """Rehashing the adapter artifact and MI snapshot cannot change the frozen world."""
    run = make_run(tmp_path, planning_world=True)
    document = json.loads(
        (run / "evidence/authoritative-planning-world-evidence.json").read_text(encoding="utf-8")
    )
    document["identity"][field] = "b" * 64 if field == "dataset_sha256" else "other-world"
    _update_planning_world_evidence(run, document)
    verdict = assess_b1_directory(run)
    assert verdict["context"]["provenance_failures"] == [
        "planning_world_evidence_identity_mismatch"
    ]
    assert verdict["admission"]["valid_for_formal_population"] is False


def test_rehashed_artifact_different_from_mi_snapshot_fails_closed(tmp_path: Path) -> None:
    """A valid but different static-scene artifact cannot replace what MI actually consumed."""
    run = make_run(tmp_path, planning_world=True)
    path = run / "evidence/authoritative-planning-world-evidence.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["facts"][0]["floor_id"] = "different-floor"
    document["digest"] = plan_digest(
        {key: value for key, value in document.items() if key != "digest"}
    )
    write_json(path, document)
    verdict = assess_b1_directory(run)
    assert verdict["context"]["provenance_failures"] == ["mi_planning_world_evidence_mismatch"]


def test_old_v02_grounding_stays_valid_without_planning_world_artifact(tmp_path: Path) -> None:
    """Existing B1 archives keep their previous provenance and admission behavior."""
    run = make_run(tmp_path)
    assert not (run / "evidence/authoritative-planning-world-evidence.json").exists()
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is True


def test_v03_review_and_repair_attempts_bind_to_frozen_world_context(tmp_path: Path) -> None:
    """The actual MI repair path keeps every review tied to one v0.3 snapshot."""
    run = make_run(tmp_path, planning_world=True, repaired=True)
    request = json.loads((run / "b1-request-record.json").read_text(encoding="utf-8"))
    digest = request["grounding_context"]["context_digest"]
    assert len(request["review_history"]) == 2
    assert all(
        attempt["grounding_context_digest"] == digest for attempt in request["review_history"]
    )
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is True
