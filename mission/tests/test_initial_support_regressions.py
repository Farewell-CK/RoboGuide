"""Language-shared synthetic Controller feedback and durable MI transition regressions."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.deployment_assessment import InitialOperationAssessment, plan_body_digest
from mission.deployment_recovery import DeploymentRecoveryAction
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.requests import (
    IntentAssessment,
    MissionRequestEngine,
    MissionRequestLifecycle,
    MissionRequestStore,
)
from mission.submission_evidence import canonical_plan_digest
from test_deployment_recovery import ReasoningRepairer, Reviewer
from test_requests import FakeController, FakeInterpreter, SequenceClock, SequenceIds, _inventory

FIXTURE = json.loads(
    Path(
        "contracts/mission/initial-operation-assessment-v0.2/fixtures/regression-cases.json"
    ).read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = FIXTURE["cases"]


def regression_plan(case: dict[str, Any], mission_id: str) -> MissionPlan:
    """Restore the shared exact intents with an optional explicit serial DAG."""
    raw = deepcopy(FIXTURE["plan"])
    raw["mission"]["id"] = mission_id
    if case["serial"]:
        raw["mission"]["actors"] = [{"id": "actor-a"}]
        raw["contexts"][1]["roles"][0]["actor"] = "actor-a"
        raw["tasks"][1]["depends_on"] = [raw["tasks"][0]["id"]]
    return MissionPlan.from_json(raw)


def assessment_body(case: dict[str, Any], plan: MissionPlan) -> JSONObject:
    """Bind the Rust-checked neutral counts to this exact plan without exposing the matrix."""
    expected = case["expected"]
    roles = [
        {
            "task_id": plan.tasks[index].task_id,
            "role_id": "navigator",
            "operation": "mobility.navigate@v1",
            **dict(zip(FIXTURE["count_order"], counts, strict=True)),
        }
        for index, counts in enumerate(expected["role_counts"])
    ]
    diagnostics = [
        {
            "task_id": role["task_id"],
            "role_id": role["role_id"],
            "considered_count": expected["considered_count"],
            "eligible_count": expected["eligible_count"],
            "exclusions": expected["exclusions"],
        }
        for role in roles
    ]
    return cast(
        JSONObject,
        {
            "schema_version": "roboguide.initial-operation-assessment/v0.2",
            "mission_id": plan.mission.mission_id,
            "plan_body_sha256": plan_body_digest(plan),
            "scope": "initial_static_world",
            "decision": expected["decision"],
            "reason_code": expected["reason_code"],
            "source_digest": "sha256:" + "a" * 64,
            "world_snapshot_digest": "sha256:" + "b" * 64,
            "local_how_digest": "sha256:" + "c" * 64,
            "received_at_ms": 0,
            "expires_at_ms": 600_000,
            "assessed_at_ms": 600_000 if case["condition"] == "expired" else 100,
            "checked_combinations": expected["checked_combinations"],
            "roles": roles,
            "candidate_diagnostics": diagnostics,
            "placement_failure": expected["placement_failure"],
        },
    )


class RegressionPlanner:
    """Return the shared synthetic plan; this fixture does not predict a real model's behavior."""

    def __init__(self, case: dict[str, Any]) -> None:
        """Freeze one case and retain an observable initial planning count."""
        self.case = case
        self.calls = 0

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Pass the ordinary local contract checks without choosing an executor."""
        self.calls += 1
        plan = regression_plan(self.case, mission_id)
        assert plan.mission.objective == grounded_intent.objective
        plan.validate_implementation_support()
        capability_catalog.validate_plan(plan)
        return plan


class RegressionController(FakeController):
    """Deliver the language-shared neutral fixture without providing live inventory to MI."""

    def __init__(self, case: dict[str, Any]) -> None:
        """Retain the finite preflight case and exact inspected plans."""
        super().__init__(_inventory("mobility.navigate@v1"))
        self.case = case
        self.assessments: list[JSONObject] = []

    def assess_initial_support(self, plan: MissionPlan) -> InitialOperationAssessment:
        """Use the production parser on the same expected wire fields checked by Rust."""
        self.assessments.append(plan.to_json())
        value = InitialOperationAssessment.from_json(assessment_body(self.case, plan))
        assert value.matches_plan(plan)
        return value


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_shared_support_cases_drive_durable_mi_transitions(
    tmp_path: Path, case: dict[str, Any]
) -> None:
    """Unknowns continue, gaps remain observable, and each approved plan has at most one POST."""
    expected = case["expected"]
    fixed = regression_plan(case, FIXTURE["plan"]["mission"]["id"])
    assert (
        json.dumps(fixed.to_json(), ensure_ascii=False, separators=(",", ":"))
        == FIXTURE["http_request_bodies"]["serial" if case["serial"] else "parallel"]
    )
    controller, planner, reviewer = RegressionController(case), RegressionPlanner(case), Reviewer()
    model = ReasoningRepairer(
        [DeploymentRecoveryAction.WAIT] if expected["reconsideration_calls"] else []
    )
    objective = FIXTURE["plan"]["mission"]["objective"]
    interpreter = FakeInterpreter([IntentAssessment(objective, (), (), ())])
    engine = MissionRequestEngine(
        MissionRequestStore(tmp_path / "requests.sqlite3"),
        interpreter,
        planner,
        controller,
        CanonicalCapabilityCatalog.load(Path("contracts/capability/v0.1/catalog.json")),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
        reviewer=reviewer,
        repairer=model,
        controller_preflight_enabled=True,
        max_deployment_recovery_attempts=2,
    )
    record = engine.create(objective)
    assert record.lifecycle.value == expected["request_lifecycle"], record.issues
    assert len(model.calls) == expected["reconsideration_calls"]
    assert len(controller.submissions) == expected["submissions"]
    assert len(controller.assessments) == planner.calls == len(interpreter.calls) == 1
    assert len(reviewer.plans) == len(record.review_history) == 1
    assert controller.inventory_calls == 0
    assert record.plan is not None and record.grounding_context is not None
    assert record.plan.to_json() == controller.assessments[0]
    assert len(record.plan.tasks) == 2
    assert dict(record.plan.tasks[0].roles[0].execution.parameters) == {"destination": "landmark-a"}
    assert dict(record.plan.tasks[1].roles[0].execution.parameters) == {"destination": "landmark-b"}
    review = record.review_history[0]
    assert review.draft_digest == canonical_plan_digest(record.plan.to_json())
    assert review.grounding_context_digest == record.grounding_context.context_digest
    assert engine.get(record.request_id) == record
    if controller.submissions:
        assert controller.submissions == [record.plan]
        assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
        assert record.deployment_recovery is None
    else:
        assert record.submission_evidence is None and record.admission_evidence is None
        assert record.failure_evidence is not None
        assert record.failure_evidence["stage"] == "controller_preflight"
        assert record.recovery_evidence is not None
        observed = record.recovery_evidence.deployment_assessment
        assert observed is not None
        assert observed.to_json() == assessment_body(case, record.plan)
        if model.calls:
            assert record.deployment_recovery is not None
            assert record.deployment_recovery.attempts[0].decision is not None
            assert (
                record.deployment_recovery.attempts[0].decision.action
                is DeploymentRecoveryAction.WAIT
            )
            assert model.calls[0][1] == observed
            assert model.calls[0][2] == record.grounding_context
        else:
            assert record.deployment_recovery is None
    encoded = json.dumps(assessment_body(case, record.plan))
    assert "node-a" not in encoded and "node-b" not in encoded


@pytest.mark.parametrize("fault", ["counter_partition", "missing_slot", "unknown_block", "expired"])
def test_shared_feedback_cannot_bypass_contract_fences(fault: str) -> None:
    """Corrupted counters, slot coverage, negative claims and expiry cannot authorize submission."""
    case = next(case for case in CASES if case["id"] == "all_unknown")
    plan = regression_plan(case, "mission-support-conformance")
    body = assessment_body(case, plan)
    if fault == "missing_slot":
        cast(list[JSONObject], body["roles"]).pop()
        value = InitialOperationAssessment.from_json(body)
        assert not value.matches_plan(plan)
        return
    if fault == "counter_partition":
        cast(list[JSONObject], body["candidate_diagnostics"])[0]["eligible_count"] = 1
    elif fault == "unknown_block":
        body["decision"] = "blocked"
        body["reason_code"] = "scoped_static_support_shortage"
    else:
        body["assessed_at_ms"] = 600_000
    with pytest.raises(ValueError):
        InitialOperationAssessment.from_json(body)
