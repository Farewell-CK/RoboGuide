"""B1 provenance checks for versioned pre-reset planning-world evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import build_provenance, make_run, write_json
from roboguide_eval.b1_planning_source import freeze_source, preflight_artifact
from roboguide_eval.b1_provenance import (
    build_b1_provenance_record,
    plan_digest,
    write_b1_provenance,
)
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


def _upgrade_required_source(run: Path) -> None:
    """Freeze the new run-local requirement and write its v0.4 provenance links."""
    freeze_source(run)
    record = build_b1_provenance_record(
        run_id=run.name,
        frozen_input_path=run / "b1-input-used.json",
        request_record_path=run / "b1-request-record.json",
        request_observations_path=run / "b1-request-observations.json",
        controller_mission_path=run / "mission.json",
        controller_events_path=run / "events.json",
        execution_attempts_path=run / "execution-attempts.json",
        shared_world_summary_path=run / "evidence/shared-world-summary.json",
        semantic_evidence_path=run / "evidence/authoritative-semantic-evidence.json",
        planning_source_path=run / "planning-world-source.json",
    )
    write_b1_provenance(record, run / "b1-provenance.json")


def test_required_source_rejects_accepted_v02_and_missing_declaration(tmp_path: Path) -> None:
    """A configured B1 run cannot hide missing world evidence behind legacy grounding."""
    run = make_run(tmp_path)
    _upgrade_required_source(run)
    assert (run / "planning-world-source-required.json").exists()
    verdict = assess_b1_directory(run)
    assert "required_planning_world_evidence_missing" in verdict["context"]["provenance_failures"]
    assert verdict["admission"]["valid_for_formal_population"] is False
    (run / "planning-world-source.json").unlink()
    verdict = assess_b1_directory(run)
    assert "planning_world_source_invalid" in verdict["context"]["provenance_failures"]


def test_required_source_preflight_and_v04_provenance(tmp_path: Path) -> None:
    """Only the frozen same-world artifact can satisfy a required B1 source."""
    run = make_run(tmp_path, planning_world=True)
    _upgrade_required_source(run)
    preflight_artifact(run)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is True
    (run / "evidence/authoritative-planning-world-evidence.json").unlink()
    with pytest.raises(ValueError):
        preflight_artifact(run)
    verdict = assess_b1_directory(run)
    assert "planning_world_evidence_missing" in verdict["context"]["provenance_failures"]


def test_required_source_preserves_scoped_grounding_failure(tmp_path: Path) -> None:
    """A real MI acquisition failure is a system outcome, not an accepted v0.2 plan."""
    run = make_run(tmp_path, planning_world=True)
    request_path = run / "b1-request-record.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["lifecycle"] = "Failed"
    request["grounding_context"] = None
    request["plan"] = None
    request["draft_digest"] = None
    request["draft_revision"] = 0
    request["review_history"] = []
    write_json(request_path, request)
    observations_path = run / "b1-request-observations.json"
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    observations["request_record_digest"] = plan_digest(request)
    observations["submission_evidence"] = None
    observations["failure_evidence"] = {
        "schema_version": "roboguide.mission-request-failure/v0.1",
        "request_id": request["request_id"],
        "mission_id": request["mission_id"],
        "stage": "grounding",
        "failure_owner": "SUT_SYSTEM",
        "detail": "required planning world evidence is unavailable or invalid",
        "observed_at_ms": 1,
    }
    write_json(observations_path, observations)
    (run / "evidence/authoritative-planning-world-evidence.json").unlink()
    _upgrade_required_source(run)
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["provenance_valid"] is True
    assert verdict["admission"]["valid_for_formal_population"] is True
    assert verdict["system_outcome"] == "FAILURE"
