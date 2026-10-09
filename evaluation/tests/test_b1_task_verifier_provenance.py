"""End-to-end B1 archive checks for independent final verifier evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import build_provenance, make_run, write_json
from roboguide_eval.b1_provenance import plan_digest
from roboguide_eval.b1_run import assess_b1_directory, consume_b1_verdict, write_b1_verdict


def _read(path: Path) -> dict[str, Any]:
    """Load one test-owned archive document for an isolated mutation."""
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def _signed(body: dict[str, Any]) -> dict[str, Any]:
    """Sign a deterministic deployment document using the production canonical digest."""
    return {**body, "digest": plan_digest(body)}


def _verifier_run(tmp_path: Path, *, satisfied: bool = True) -> Path:
    """Build a full MI-to-archive fixture with one final verifier-backed Task."""
    run = make_run(tmp_path)
    request_path = run / "b1-request-record.json"
    request = _read(request_path)
    plan = request["plan"]
    predicate = "any_at(any_targets|0) AND any_at(TARGET_any_targets|0)"
    plan["tasks"][0]["satisfaction"] = {
        "expected_effect": "The joint terminal goal is true.",
        "basis": "verifier-evidence",
        "verifier": {
            "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicate": predicate,
            "max_evidence_age_ms": 5000,
        },
    }
    request["draft_digest"] = plan_digest(plan)
    write_json(request_path, request)
    raw_body = json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode()
    (run / "actual-controller-body.json").write_bytes(raw_body)
    observations_path = run / "b1-request-observations.json"
    observations = _read(observations_path)
    observations["request_record_digest"] = plan_digest(request)
    observations["submission_evidence"]["submitted_plan_digest"] = plan_digest(plan)
    observations["submission_evidence"]["raw_request_body_sha256"] = (
        "sha256:" + hashlib.sha256(raw_body).hexdigest()
    )
    write_json(observations_path, observations)

    semantic = _read(run / "evidence/authoritative-semantic-evidence.json")
    summary_path = run / "evidence/shared-world-summary.json"
    summary = _read(summary_path)
    summary["identity"]["scene_id"] = "scene-51"
    summary["identity"]["habitat_seed"] = 40
    summary["authoritative_semantic_evidence_digest"] = semantic["digest"]
    write_json(summary_path, summary)
    identity = semantic["identity"]
    source = _signed(
        {
            "schema_version": "roboguide.task-verifier-source/v0.1",
            "source_id": "habitat-official-pddl",
            "source_revision": semantic["digest"],
            "identity": {
                "run_id": identity["run_id"],
                "episode_id": identity["episode_id"],
                "scene_id": semantic["world_context"]["scene_id"],
                "dataset_revision": identity["dataset_revision"],
                "dataset_sha256": identity["dataset_sha256"],
            },
            "verifier": plan["tasks"][0]["satisfaction"]["verifier"]["contract"],
            "supported_predicates": [predicate],
            "verdict_finality": "terminal",
        }
    )
    write_json(run / "evidence/task-verifier-source.json", source)
    task_id = plan["tasks"][0]["id"]
    role_id = plan["tasks"][0]["roles"][0]["id"]
    attempts_path = run / "execution-attempts.json"
    attempts = _read(attempts_path)
    attempts["attempts"][0]["role_id"] = role_id
    write_json(attempts_path, attempts)
    verdict = _signed(
        {
            "schema_version": "roboguide.task-verifier-verdict/v0.1",
            "source_digest": source["digest"],
            "source_id": source["source_id"],
            "verifier": source["verifier"],
            "predicate": predicate,
            "source_observed_at_ms": 100,
            "satisfied": satisfied,
            "tasks": [
                {
                    "mission_id": request["mission_id"],
                    "task_id": task_id,
                    "attempts": [{"role_id": role_id, "attempt_id": "exec-0"}],
                }
            ],
        }
    )
    write_json(run / "evidence/task-verifier-verdict.json", verdict)
    events_path = run / "events.json"
    events = _read(events_path)
    events["events"].append(
        {
            "sequence": 4,
            "payload": {
                "TaskVerifierVerdictObserved": {
                    "task_ref": {"mission_id": request["mission_id"], "task_id": task_id},
                    "source_id": source["source_id"],
                    "source_revision": source["source_revision"],
                    "verdict_digest": verdict["digest"],
                    "satisfied": satisfied,
                }
            },
        }
    )
    if satisfied:
        events["events"].append(
            {
                "sequence": 5,
                "payload": {
                    "TaskSatisfied": {
                        "task_ref": {"mission_id": request["mission_id"], "task_id": task_id},
                        "group_id": _read(run / "mission.json")["group_id"],
                    }
                },
            }
        )
    else:
        mission = _read(run / "mission.json")
        mission["status"] = "Failed"
        write_json(run / "mission.json", mission)
        summary = _read(run / "evidence/shared-world-summary.json")
        summary["official_pddl_success"] = False
        write_json(run / "evidence/shared-world-summary.json", summary)
    write_json(events_path, events)
    build_provenance(run)
    return run


@pytest.mark.parametrize("satisfied", [True, False])
def test_final_official_verdict_is_bound_to_archived_task_attempt(
    tmp_path: Path, satisfied: bool
) -> None:
    """A complete positive or negative chain retains valid provenance and official outcome."""
    run = _verifier_run(tmp_path, satisfied=satisfied)
    result = assess_b1_directory(run)
    assert result["admission"]["provenance_valid"] is True, result["context"]["provenance_failures"]
    assert result["admission"]["valid_for_formal_population"] is True
    assert result["admission"]["valid_for_benchmark_population"] is True
    assert result["admission"]["benchmark_outcome"] == (
        "BENCHMARK_TRUE" if satisfied else "BENCHMARK_FALSE"
    )


def test_persisted_b1_gate_tracks_verifier_evidence_bytes(tmp_path: Path) -> None:
    """A post-verdict verifier artifact edit makes the persisted admission stale."""
    run = _verifier_run(tmp_path)
    original = write_b1_verdict(run)
    source_path = run / "evidence/task-verifier-source.json"
    source = _read(source_path)
    source["unrecognized_note"] = "changed after admission"
    write_json(source_path, source)
    current = assess_b1_directory(run)
    assert current["evidence_digest"] != original["evidence_digest"]
    assert (
        "b1_admission_missing_stale_or_tampered" in consume_b1_verdict(run)["admission"]["reasons"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_source",
        "missing_verdict",
        "tampered_source",
        "other_scene",
        "other_dataset",
        "wrong_attempt",
        "missing_event",
        "missing_satisfaction",
        "malformed_attempt",
        "contradictory_official_metric",
        "other_summary_scene",
        "other_summary_seed",
    ],
)
def test_verifier_archive_fails_closed_on_identity_or_event_gap(
    tmp_path: Path, mutation: str
) -> None:
    """Digest-valid cross-world evidence and incomplete Controller events remain invalid."""
    run = _verifier_run(tmp_path)
    source_path = run / "evidence/task-verifier-source.json"
    verdict_path = run / "evidence/task-verifier-verdict.json"
    events_path = run / "events.json"
    source, verdict, events = _read(source_path), _read(verdict_path), _read(events_path)
    if mutation == "missing_source":
        source_path.unlink()
    elif mutation == "missing_verdict":
        verdict_path.unlink()
    elif mutation == "tampered_source":
        source["identity"]["scene_id"] = "other-scene"
        write_json(source_path, source)
    elif mutation in {"other_scene", "other_dataset"}:
        field, value = (
            ("scene_id", "other-scene")
            if mutation == "other_scene"
            else ("dataset_revision", "other-dataset")
        )
        source["identity"][field] = value
        source = _signed({key: item for key, item in source.items() if key != "digest"})
        verdict["source_digest"] = source["digest"]
        verdict = _signed({key: item for key, item in verdict.items() if key != "digest"})
        events["events"][3]["payload"]["TaskVerifierVerdictObserved"]["verdict_digest"] = verdict[
            "digest"
        ]
        write_json(source_path, source)
        write_json(verdict_path, verdict)
        write_json(events_path, events)
    elif mutation == "wrong_attempt":
        verdict["tasks"][0]["attempts"][0]["attempt_id"] = "stale-attempt"
        verdict = _signed({key: item for key, item in verdict.items() if key != "digest"})
        events["events"][3]["payload"]["TaskVerifierVerdictObserved"]["verdict_digest"] = verdict[
            "digest"
        ]
        write_json(verdict_path, verdict)
        write_json(events_path, events)
    elif mutation == "missing_event":
        events["events"] = events["events"][:3]
        write_json(events_path, events)
    elif mutation == "missing_satisfaction":
        events["events"] = events["events"][:-1]
        write_json(events_path, events)
    elif mutation == "malformed_attempt":
        verdict["tasks"][0]["attempts"][0]["attempt_id"] = []
        verdict = _signed({key: item for key, item in verdict.items() if key != "digest"})
        events["events"][3]["payload"]["TaskVerifierVerdictObserved"]["verdict_digest"] = verdict[
            "digest"
        ]
        write_json(verdict_path, verdict)
        write_json(events_path, events)
    else:
        summary_path = run / "evidence/shared-world-summary.json"
        summary = _read(summary_path)
        if mutation == "other_summary_scene":
            summary["identity"]["scene_id"] = "another-scene"
        elif mutation == "other_summary_seed":
            summary["identity"]["habitat_seed"] = 41
        else:
            summary["official_pddl_success"] = False
        write_json(summary_path, summary)
    build_provenance(run)
    result = assess_b1_directory(run)
    assert result["admission"]["provenance_valid"] is False
    assert set(result["context"]["provenance_failures"]) & {
        "task_verifier_evidence_missing",
        "task_verifier_evidence_invalid",
    }
