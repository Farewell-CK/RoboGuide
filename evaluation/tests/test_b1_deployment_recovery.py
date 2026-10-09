"""B1 provenance compatibility and tamper fences for pre-submission MI decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from b1_helpers import build_provenance, make_run, write_json
from mission.deployment_assessment import (
    DIAGNOSTIC_SCHEMA,
    AssessedRole,
    CandidateDiagnostics,
    InitialOperationAssessment,
    plan_body_digest,
)
from mission.deployment_recovery import (
    DeploymentRecoveryAction,
    DeploymentRecoveryAttempt,
    DeploymentRecoveryDecision,
    DeploymentRecoverySession,
    frozen_document,
)
from mission.models import MissionPlan
from mission.submission_evidence import canonical_plan_digest
from roboguide_eval.b1_run import assess_b1_directory


def attach_recovery(run: Path) -> dict[str, Any]:
    """Attach an exact-reviewed-plan session to actual offline MI/HTTP execution evidence."""
    request = json.loads((run / "b1-request-record.json").read_text())
    plan = MissionPlan.from_json(request["plan"])
    slots = [(task, role) for task in plan.tasks if not task.depends_on for role in task.roles]
    assessment = InitialOperationAssessment(
        plan.mission.mission_id,
        plan_body_digest(plan),
        "blocked",
        "scoped_static_support_shortage",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        0,
        600000,
        100,
        2,
        tuple(
            AssessedRole(
                task.task_id,
                role.role_id,
                f"{role.execution.operation.namespace}.{role.execution.operation.name}@{role.execution.operation.version}",
                0,
                0,
                2,
                0,
            )
            for task, role in slots
        ),
        tuple(CandidateDiagnostics(task.task_id, role.role_id, 2, 2, ()) for task, role in slots),
        None,
        DIAGNOSTIC_SCHEMA,
    )
    decision = DeploymentRecoveryDecision(DeploymentRecoveryAction.WAIT, "Keep the original goal.")
    attempt = DeploymentRecoveryAttempt(
        1,
        frozen_document(plan.to_json()),
        canonical_plan_digest(plan.to_json()),
        request["grounding_context"]["context_digest"],
        assessment,
        200,
        201,
        "decided",
        decision,
        frozen_document(decision.to_json()),
    )
    session = DeploymentRecoverySession(
        request["request_id"], request["mission_id"], 2, 200, 900200, (attempt,)
    )
    observations = cast(
        dict[str, Any], json.loads((run / "b1-request-observations.json").read_text())
    )
    observations.update(
        schema_version="roboguide.mission-request-observations/v0.4",
        deployment_recovery=session.to_json(),
    )
    write_json(run / "b1-request-observations.json", observations)
    build_provenance(run)
    return observations


@pytest.mark.parametrize("omit_goal", [False, True])
def test_bound_session_preserves_population_and_semantic_diagnostic(
    tmp_path: Path, omit_goal: bool
) -> None:
    """Pre-submission model evidence adds no new Formal goal-coverage or benchmark success rule."""
    run = make_run(tmp_path, repaired=True, omit_second_goal=omit_goal)
    before = assess_b1_directory(run)
    attach_recovery(run)
    after = assess_b1_directory(run)
    assert after["admission"] == before["admission"]
    assert after["checks"] == before["checks"]
    assert after["admission"]["provenance_valid"] is True


def test_held_session_is_still_a_formal_system_failure_without_benchmark(tmp_path: Path) -> None:
    """An unused deployment decision never invents execution registrations or official metrics."""
    run = make_run(tmp_path, case="preflight", repaired=True)
    before = assess_b1_directory(run)
    attach_recovery(run)
    after = assess_b1_directory(run)
    assert after["admission"] == before["admission"]
    assert after["admission"]["valid_for_formal_population"] is True
    assert after["admission"]["valid_for_benchmark_population"] is False


@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "context",
        "input_digest",
        "feedback_body",
        "source",
        "budget",
        "pending",
        "unreviewed",
        "missing_session",
    ],
)
def test_detached_or_invalid_session_still_fails_closed(tmp_path: Path, damage: str) -> None:
    """Rehashed outer artifacts cannot hide a wrong context, source, budget or reviewed draft."""
    run = make_run(tmp_path, repaired=True)
    observations = attach_recovery(run)
    session = observations["deployment_recovery"]
    attempt = session["attempts"][0]
    if damage == "request":
        session["request_id"] = "another-request"
    elif damage == "context":
        attempt["grounding_context_digest"] = "sha256:" + "d" * 64
    elif damage == "input_digest":
        attempt["input_plan_digest"] = "sha256:" + "d" * 64
    elif damage == "feedback_body":
        attempt["assessment"]["plan_body_sha256"] = "sha256:" + "d" * 64
    elif damage == "source":
        attempt["assessment"]["source_digest"] = None
    elif damage == "budget":
        session["max_attempts"] = 4
    elif damage == "pending":
        attempt.update(outcome="pending", finished_at_ms=None, decision=None, provider_output=None)
    elif damage == "missing_session":
        observations["deployment_recovery"] = None
    else:
        request = json.loads((run / "b1-request-record.json").read_text())
        request["review_history"][-1]["review"]["approved"] = False
        write_json(run / "b1-request-record.json", request)
        observations["request_record_digest"] = canonical_plan_digest(request)
    write_json(run / "b1-request-observations.json", observations)
    build_provenance(run)
    verdict = assess_b1_directory(run)
    assert verdict["admission"]["provenance_valid"] is False
    assert "mi_deployment_recovery_invalid" in verdict["context"]["provenance_failures"]


@pytest.mark.parametrize("damage", ["downgrade", "missing_field"])
def test_session_cannot_be_silently_hidden_by_observation_version(
    tmp_path: Path, damage: str
) -> None:
    """A versioned session cannot ride in legacy metadata or vanish from a v0.4 envelope."""
    run = make_run(tmp_path, repaired=True)
    observations = attach_recovery(run)
    if damage == "downgrade":
        observations["schema_version"] = "roboguide.mission-request-observations/v0.3"
    else:
        observations.pop("deployment_recovery")
    write_json(run / "b1-request-observations.json", observations)
    build_provenance(run)
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False
