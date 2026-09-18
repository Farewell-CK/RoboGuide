"""Adversarial final-draft, actual-POST and scoped-execution provenance tests."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import ROOT, build_provenance, make_run, write_json
from mission.request_engine import _plan_digest
from mission.request_record import MissionRequestRecord
from roboguide_eval.b1_provenance import digest, plan_digest
from roboguide_eval.b1_run import assess_b1_directory


def update_request(run: Path, request: dict[str, Any]) -> None:
    """Update both observation binding and links to isolate each inner identity gate."""
    write_json(run / "b1-request-record.json", request)
    obs = json.loads((run / "b1-request-observations.json").read_text())
    obs["request_record_digest"] = plan_digest(request)
    write_json(run / "b1-request-observations.json", obs)
    build_provenance(run)


def test_actual_controller_post_digest_matches_captured_bytes(tmp_path: Path) -> None:
    """Production submission hashes the exact bytes received at the HTTP server."""
    run = make_run(tmp_path)
    body = (run / "actual-controller-body.json").read_bytes()
    obs = json.loads((run / "b1-request-observations.json").read_text())
    sent = obs["submission_evidence"]
    assert sent["raw_request_body_sha256"] == "sha256:" + hashlib.sha256(body).hexdigest()
    assert sent["submitted_plan_digest"] == plan_digest(json.loads(body))
    assert sent["controller_status_code"] == 202
    assert assess_b1_directory(run)["checks"]["provenance_chain_passed"] is True


@pytest.mark.parametrize("mutation", ["destination", "actor", "constraint", "execution_intent"])
def test_same_task_ids_do_not_hide_changed_actual_submission(tmp_path: Path, mutation: str) -> None:
    """An independently hashed different POST body cannot pass via unchanged TaskIds."""
    run = make_run(tmp_path)
    request = json.loads((run / "b1-request-record.json").read_text())
    original = copy.deepcopy(request["plan"])
    if mutation == "destination":
        request["plan"]["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["destination"] = (
            "elsewhere"
        )
    elif mutation == "actor":
        request["plan"]["contexts"][0]["roles"][0]["actor"] = "shared-robot-b"
    elif mutation == "constraint":
        request["plan"]["contexts"][0]["executor_constraints"] = [
            {"kind": "distinct-physical-entities", "actors": ["shared-robot-a", "shared-robot-b"]}
        ]
    else:
        request["plan"]["tasks"][0]["roles"][0]["execution_intent"]["objective"] = "changed"
    request["draft_digest"] = plan_digest(request["plan"])
    update_request(run, request)
    assert [t["id"] for t in original["tasks"]] == [t["id"] for t in request["plan"]["tasks"]]
    verdict = assess_b1_directory(run)
    assert "controller_plan_mismatch" in verdict["context"]["provenance_failures"]
    assert verdict["admission"]["valid_for_formal_population"] is False


@pytest.mark.parametrize("fake", ["digest-placeholder", "sha256:placeholder", "0" * 64, None])
def test_arbitrary_digest_is_not_generation_evidence(tmp_path: Path, fake: Any) -> None:
    """Even with approved review history, only the exact final cryptographic digest passes."""
    run = make_run(tmp_path, repaired=True)
    request = json.loads((run / "b1-request-record.json").read_text())
    assert request["review_history"]
    request["draft_digest"] = fake
    update_request(run, request)
    assert (
        "mi_run_plan_digest_mismatch" in assess_b1_directory(run)["context"]["provenance_failures"]
    )


def test_final_repaired_digest_and_review_are_bound(tmp_path: Path) -> None:
    """Real repair/review admits revision two and rejects the valid but stale revision-one hash."""
    run = make_run(tmp_path, repaired=True)
    request = json.loads((run / "b1-request-record.json").read_text())
    assert request["draft_revision"] == 2
    assert request["repair_attempts"] == 1
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is True
    old_digest = request["review_history"][0]["draft_digest"]
    assert old_digest != request["draft_digest"]
    request["draft_digest"] = old_digest
    update_request(run, request)
    assert (
        "mi_run_plan_digest_mismatch" in assess_b1_directory(run)["context"]["provenance_failures"]
    )


def test_final_review_must_reference_final_revision(tmp_path: Path) -> None:
    """A genuine final hash does not make stale reviewer approval current."""
    run = make_run(tmp_path, repaired=True)
    request = json.loads((run / "b1-request-record.json").read_text())
    request["review_history"] = request["review_history"][:1]
    update_request(run, request)
    assert "final_review_mismatch" in assess_b1_directory(run)["context"]["provenance_failures"]


def test_mi_and_evaluation_canonicalization_are_identical(tmp_path: Path) -> None:
    """Unicode and normalized optional fields use the exact same canonical digest."""
    run = make_run(tmp_path)
    request = json.loads((run / "b1-request-record.json").read_text())
    plan = MissionRequestRecord.from_json(request).plan
    assert plan is not None
    assert _plan_digest(plan) == plan_digest(request["plan"]) == request["draft_digest"]
    assert request["draft_digest"] == "sha256:" + digest(request["plan"])


def test_benchmark_missing_does_not_break_execution_provenance(tmp_path: Path) -> None:
    """Removing even a previously linked benchmark keeps execution provenance independent."""
    run = make_run(tmp_path)
    (run / "evidence/shared-world-summary.json").unlink()
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["provenance_valid"] is True
    assert verdict["admission"]["valid_for_formal_population"] is True
    assert verdict["admission"]["valid_for_benchmark_population"] is False


@pytest.mark.parametrize(
    "missing",
    [
        "b1-provenance.json",
        "b1-request-observations.json",
        "b1-request-record.json",
        "b1-input-used.json",
    ],
)
def test_missing_chain_artifact_fails_closed(tmp_path: Path, missing: str) -> None:
    """An authoritative benchmark bool cannot replace a missing protocol link."""
    run = make_run(tmp_path)
    (run / missing).unlink()
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["valid_for_formal_population"] is False
    assert verdict["admission"]["valid_for_benchmark_population"] is False


def test_multi_mission_and_wrong_group_evidence_is_ignored(tmp_path: Path) -> None:
    """Only exact current Mission/Group registrations and attempts enter the identity."""
    run = make_run(tmp_path)
    before = json.loads((run / "b1-provenance.json").read_text())
    events = json.loads((run / "events.json").read_text())
    attempts = json.loads((run / "execution-attempts.json").read_text())
    current_mission = before["mission_id"]
    current_group = before["controller_submission_identity"]
    for mission_id, group_id in [
        ("other-mission", current_group),
        (current_mission, "other-group"),
        ("other-mission", "other-group"),
    ]:
        events["events"].insert(
            0,
            {
                "payload": {
                    "ExecutionGroupCreated": {"mission_id": mission_id, "group_id": group_id}
                }
            },
        )
        events["events"].append(
            {
                "payload": {
                    "TaskExecutionRegistered": {
                        "group_id": group_id,
                        "task_ref": {"mission_id": mission_id, "task_id": "foreign-task"},
                    }
                }
            }
        )
        attempts["attempts"].append(
            {"mission_id": mission_id, "group_id": group_id, "execution_id": "foreign-exec"}
        )
    write_json(run / "events.json", events)
    write_json(run / "execution-attempts.json", attempts)
    build_provenance(run)
    after = json.loads((run / "b1-provenance.json").read_text())
    assert before["execution_identity"] == after["execution_identity"]
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["provenance_valid"] is True
    assert "foreign-task" not in verdict["context"]["controller_receipt"]["registered_task_ids"]


def test_other_mission_attempt_cannot_rescue_missing_current_attempt(tmp_path: Path) -> None:
    """A foreign execution is never proof that this accepted Mission was attempted."""
    run = make_run(tmp_path)
    write_json(
        run / "execution-attempts.json",
        {"attempts": [{"mission_id": "other", "group_id": "other", "execution_id": "foreign"}]},
    )
    build_provenance(run)
    assert "execution_attempts_empty" in assess_b1_directory(run)["context"]["provenance_failures"]


def test_old_provenance_schema_rejected(tmp_path: Path) -> None:
    """v0.1 cannot silently acquire v0.2 semantics."""
    run = make_run(tmp_path)
    document = json.loads((run / "b1-provenance.json").read_text())
    document["schema_version"] = "roboguide.e1.b1-provenance/v0.1"
    write_json(run / "b1-provenance.json", document)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "input_digest",
        "mi_run_identity",
        "mission_id",
        "controller_submission_identity",
        "execution_identity",
        "request_record_digest",
        "observations_digest",
        "submission_evidence_digest",
    ],
)
def test_every_record_identity_link_is_checked(tmp_path: Path, field: str) -> None:
    """No independently claimed chain identity can silently bypass verification."""
    run = make_run(tmp_path)
    document = json.loads((run / "b1-provenance.json").read_text())
    document[field] = "tampered"
    write_json(run / "b1-provenance.json", document)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False


@pytest.mark.parametrize("name", ["events.json", "execution-attempts.json"])
def test_malformed_collections_fail_closed(tmp_path: Path, name: str) -> None:
    """Malformed execution evidence is rejected rather than crashing admission."""
    run = make_run(tmp_path)
    write_json(run / name, {"events": None, "attempts": None})
    build_provenance(run)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False


def test_static_b2_record_without_generation_cannot_enter_b1(tmp_path: Path) -> None:
    """Copying the B2 fixture and renaming its Mission cannot forge a generated final hash."""
    run = make_run(tmp_path)
    request = json.loads((run / "b1-request-record.json").read_text())
    copied = json.loads(
        (ROOT / "scenarios/e1-shared-world-episode-51/mission-plan.json").read_text()
    )
    copied["mission"]["id"] = request["mission_id"]
    request["plan"] = copied
    request["draft_digest"] = None
    update_request(run, request)
    assert assess_b1_directory(run)["admission"]["valid_for_formal_population"] is False
