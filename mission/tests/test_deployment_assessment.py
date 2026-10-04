"""Offline deployment feedback and exact-plan retry tests; no model or robot calls."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mission.controller import HttpMissionController
from mission.deployment_assessment import (
    DIAGNOSTIC_SCHEMA,
    AssessedRole,
    CandidateDiagnostics,
    InitialOperationAssessment,
    plan_body_digest,
)
from mission.models import JSONObject, MissionPlan
from mission.recovery import FailureReason, FailureStage, RecoveryAction, RequestRecoveryEvidence
from mission.requests import MissionRequestError, MissionRequestLifecycle
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)


def feedback(plan: MissionPlan, decision: str = "blocked") -> InitialOperationAssessment:
    """Bind generic delivery slots to finite, non-authoritative synthetic source facts."""
    role = plan.tasks[0].roles[0]
    operation = role.execution.operation
    return InitialOperationAssessment(
        plan.mission.mission_id,
        plan_body_digest(plan),
        decision,
        "scoped_static_support_shortage" if decision == "blocked" else "no_scoped_shortage",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        0,
        600000,
        100,
        2,
        (
            AssessedRole(
                plan.tasks[0].task_id,
                role.role_id,
                f"{operation.namespace}.{operation.name}@{operation.version}",
                0,
                2 if decision == "not_blocked" else 0,
                2 if decision == "blocked" else 0,
                0,
            ),
        ),
    )


class AssessingController(FakeController):
    """Script bounded preflight replies independently of the actual submit port."""

    def __init__(self, decisions: list[str]) -> None:
        """Retain exact observed plans and the finite planned response sequence."""
        super().__init__(_inventory(*_fixture_contracts()))
        self.decisions = decisions
        self.assessments: list[JSONObject] = []

    def assess_initial_support(self, plan: MissionPlan) -> InitialOperationAssessment:
        """Return one reply without discovering nodes or modifying a plan."""
        self.assessments.append(plan.to_json())
        decision = self.decisions.pop(0)
        if decision == "transport":
            raise TimeoutError("assessment HTTP interrupted")
        result = feedback(plan, "not_blocked" if decision == "not_blocked" else "blocked")
        if decision == "unavailable":
            return replace(
                result,
                decision="unavailable",
                reason_code="source_expired",
                checked_combinations=0,
                roles=(),
            )
        if decision == "detached":
            return replace(result, plan_body_sha256="sha256:" + "d" * 64)
        return result


def test_disabled_preflight_retains_original_submission_path(tmp_path: Path) -> None:
    """A new optional port cannot silently change a deployment's default behavior."""
    controller = AssessingController([])
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert controller.assessments == [] and len(controller.submissions) == 1


def test_scoped_hold_is_durable_and_retry_rechecks_same_reviewed_plan(tmp_path: Path) -> None:
    """Retry does not call Interpreter/Planner or submit before fresh usable feedback."""
    controller = AssessingController(["blocked", "blocked", "not_blocked"])
    interpreter, planner = FakeInterpreter([_assessment()]), FakePlanner()
    engine = _engine(tmp_path, interpreter, planner, controller)
    engine._controller_preflight_enabled = True
    blocked = engine.create("deliver the declared payload")
    assert blocked.lifecycle is MissionRequestLifecycle.BLOCKED
    assert not controller.submissions and blocked.submission_evidence is None
    recovery = blocked.recovery_evidence
    assert recovery is not None and recovery.reason is FailureReason.DEPLOYMENT_SUPPORT_BLOCKED
    assert recovery.stage is FailureStage.CONTROLLER_PREFLIGHT
    assert recovery.action is RecoveryAction.RECHECK_DEPLOYMENT
    assert (
        blocked.failure_evidence is not None
        and blocked.failure_evidence["failure_owner"] == "SUT_SYSTEM"
    )
    assert blocked.observations().to_json()["recovery_evidence"] == recovery.to_json()
    assert engine.get(blocked.request_id) == blocked
    again = engine.retry(blocked.request_id)
    assert again.lifecycle is MissionRequestLifecycle.BLOCKED and not controller.submissions
    accepted = engine.retry(blocked.request_id)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(interpreter.calls) == len(planner.calls) == len(controller.submissions) == 1
    assert len(controller.assessments) == 3
    assert blocked.plan is not None
    assert all(document == blocked.plan.to_json() for document in controller.assessments)
    assert accepted.plan == blocked.plan and accepted.grounding_context == blocked.grounding_context
    assert (
        accepted.recovery_evidence is not None
        and accepted.recovery_evidence.deployment_assessment is not None
    )
    assert accepted.recovery_evidence.deployment_assessment.decision == "not_blocked"


@pytest.mark.parametrize("decision", ["unavailable", "transport", "detached"])
def test_unavailable_assessment_preserves_gap_without_a_post(tmp_path: Path, decision: str) -> None:
    """Required but untrustworthy preflight stops at its own boundary, not a model failure."""
    controller = AssessingController([decision])
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    engine._controller_preflight_enabled = True
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED and not controller.submissions
    assert record.recovery_evidence is not None
    assert record.recovery_evidence.reason is FailureReason.DEPLOYMENT_ASSESSMENT_UNAVAILABLE
    assert (
        record.failure_evidence is not None
        and record.failure_evidence["stage"] == "controller_preflight"
    )
    assert (record.recovery_evidence.deployment_assessment is not None) == (
        decision == "unavailable"
    )


def test_saved_deployment_hold_cannot_be_bypassed_by_disabling_preflight(tmp_path: Path) -> None:
    """A configuration downgrade cannot turn a held request's retry into an unobserved POST."""
    controller = AssessingController(["blocked"])
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    engine._controller_preflight_enabled = True
    record = engine.create("deliver the declared payload")
    engine._controller_preflight_enabled = False
    with pytest.raises(MissionRequestError, match="requires enabled preflight"):
        engine.retry(record.request_id)
    assert not controller.submissions
    assert engine.cancel(record.request_id).lifecycle is MissionRequestLifecycle.CANCELLED


def test_preflight_crash_restores_only_an_explicit_same_plan_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process interruption cannot become a new deliberation or ambiguous Mission POST."""
    controller = AssessingController(["not_blocked"])
    interpreter, planner = FakeInterpreter([_assessment()]), FakePlanner()
    engine = _engine(tmp_path, interpreter, planner, controller)
    engine._controller_preflight_enabled = True

    def crash(plan: MissionPlan) -> InitialOperationAssessment:
        """Check the pre-effect fence, then simulate termination beyond Exception handling."""
        record = engine._store.records()[0]
        assert record.lifecycle is MissionRequestLifecycle.SUBMITTING
        assert record.recovery_evidence is not None
        assert record.recovery_evidence.stage is FailureStage.CONTROLLER_PREFLIGHT
        assert record.plan == plan and record.submission_evidence is None
        raise KeyboardInterrupt("simulated process exit")

    with monkeypatch.context() as patch:
        patch.setattr(controller, "assess_initial_support", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.create("deliver the declared payload")
    interrupted = engine._store.records()[0]
    restored = _engine(tmp_path, interpreter, planner, controller)
    held = restored.get(interrupted.request_id)
    assert held.lifecycle is MissionRequestLifecycle.BLOCKED
    assert held.recovery_evidence is not None
    assert held.recovery_evidence.action is RecoveryAction.RECHECK_DEPLOYMENT
    assert held.plan == interrupted.plan and not controller.submissions
    assert len(interpreter.calls) == len(planner.calls) == 1
    restored._controller_preflight_enabled = True
    result = restored.retry(held.request_id)
    assert result.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(interpreter.calls) == len(planner.calls) == len(controller.submissions) == 1
    assert len(controller.assessments) == 1


def test_blocked_feedback_cannot_be_restored_as_submission_authority(tmp_path: Path) -> None:
    """Changing a persisted stage/reason cannot make negative feedback authorize a POST."""
    controller = AssessingController(["blocked"])
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    engine._controller_preflight_enabled = True
    record = engine.create("deliver the declared payload")
    assert record.recovery_evidence is not None
    with pytest.raises(ValueError, match="authorize"):
        replace(
            record.recovery_evidence,
            stage=FailureStage.CONTROLLER_SUBMISSION,
            reason=FailureReason.SUBMISSION_IN_FLIGHT,
        )
    legacy = record.recovery_evidence.to_json()
    legacy.pop("deployment_assessment")
    legacy["schema_version"] = "roboguide.mission-request-recovery/v0.1"
    with pytest.raises(ValueError, match="requires.*schema"):
        RequestRecoveryEvidence.from_json(legacy)


@contextmanager
def assessment_http(fault: str = "") -> Iterator[tuple[str, list[str]]]:
    """Serve the production assessment encoding on a bounded, isolated local socket."""
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        """Return finite synthetic replies without accepting a real Mission."""

        def do_POST(self) -> None:
            """Bind the received raw bytes, then optionally produce a detached reply."""
            calls.append(self.path)
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            plan = MissionPlan.from_json(json.loads(raw))
            body = feedback(plan, "not_blocked").to_json()
            body["plan_body_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
            if fault == "identity":
                body["mission_id"] = "wrong-mission"
            if fault == "digest":
                body["plan_body_sha256"] = "sha256:" + "f" * 64
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress generic HTTP access logs in deterministic tests."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("fault", ["", "identity", "digest"])
def test_http_assessment_uses_separate_read_only_route_and_exact_bytes(
    tmp_path: Path, fault: str
) -> None:
    """A malformed advisory reply never submits the plan or grants acceptance."""
    temporary = AssessingController([])
    record = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), temporary).create(
        "deliver the declared payload"
    )
    assert record.plan is not None
    with assessment_http(fault) as (endpoint, calls):
        controller = HttpMissionController(endpoint, 2)
        if fault:
            with pytest.raises(RuntimeError, match="detached"):
                controller.assess_initial_support(record.plan)
        else:
            result = controller.assess_initial_support(record.plan)
            assert result.matches_plan(record.plan) and result.decision == "not_blocked"
        assert calls == ["/v1/missions/assess-initial-support"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_body_sha256", "bad"),
        ("decision", "approved"),
        ("expires_at_ms", 100),
        ("checked_combinations", True),
        ("local_how_digest", None),
        ("source_digest", "bad"),
    ],
)
def test_assessment_rejects_corrupt_identity_lifetime_and_authority(
    tmp_path: Path, field: str, value: Any
) -> None:
    """A recomputed document checksum cannot validate contradictory typed feedback."""
    controller = AssessingController([])
    record = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller).create(
        "deliver the declared payload"
    )
    assert record.plan is not None
    document = feedback(record.plan).to_json()
    document[field] = value
    with pytest.raises(ValueError):
        InitialOperationAssessment.from_json(document)


def test_unknown_or_bounded_miss_alone_cannot_be_declared_blocked(tmp_path: Path) -> None:
    """False-negative promotion is rejected while the same unknown feedback can proceed."""
    record = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), AssessingController([])
    ).create("deliver the declared payload")
    assert record.plan is not None
    usable = feedback(record.plan, "not_blocked")
    with pytest.raises(ValueError, match="cannot block"):
        replace(usable, decision="blocked", reason_code="scoped_static_support_shortage")
    assert InitialOperationAssessment.from_json(usable.to_json()) == usable
    unknown = replace(
        usable,
        roles=(replace(usable.roles[0], bounded_miss_count=0, unknown_count=2),),
    )
    assert unknown.decision == "not_blocked" and unknown.matches_plan(record.plan)


def test_two_actor_feedback_requires_exact_initial_slot_coverage() -> None:
    """A detached or omitted peer cannot make a partial observation into usable coverage."""
    raw = json.loads(Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text())
    plan = MissionPlan.from_json(raw)
    incomplete = feedback(plan, "not_blocked")
    assert len([task for task in plan.tasks if not task.depends_on]) == 2
    assert not incomplete.matches_plan(plan)
    peer = plan.tasks[1].roles[0]
    operation = peer.execution.operation
    complete = replace(
        incomplete,
        roles=(
            *incomplete.roles,
            AssessedRole(
                plan.tasks[1].task_id,
                peer.role_id,
                f"{operation.namespace}.{operation.name}@{operation.version}",
                0,
                0,
                0,
                2,
            ),
        ),
    )
    assert complete.matches_plan(plan)
    assert not replace(
        complete, roles=(replace(complete.roles[0], task_id="foreign-task"), complete.roles[1])
    ).matches_plan(plan)


def test_neutral_schema_and_parser_reject_inventory_fields(tmp_path: Path) -> None:
    """The public closed reply surface cannot leak Node inventory into Request feedback."""
    schema = json.loads(
        Path(
            "contracts/mission/initial-operation-assessment-v0.1/assessment.schema.json"
        ).read_text()
    )
    controller = AssessingController([])
    record = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller).create(
        "deliver the declared payload"
    )
    assert record.plan is not None
    document = feedback(record.plan).to_json()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(schema["required"]) == set(document)
    roles = document["roles"]
    assert isinstance(roles, list) and isinstance(roles[0], dict)
    assert set(schema["$defs"]["role"]["properties"]) == set(roles[0])
    document["node_id"] = "invented-owner"
    with pytest.raises(ValueError, match="fields"):
        InitialOperationAssessment.from_json(document)


def test_recovery_schema_declares_assessment_and_preserves_legacy(tmp_path: Path) -> None:
    """Feedback cannot disappear on restore or be smuggled into the legacy schema."""
    controller = AssessingController(["blocked"])
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    engine._controller_preflight_enabled = True
    record = engine.create("deliver the declared payload")
    assert record.recovery_evidence is not None
    document = record.recovery_evidence.to_json()
    assert document["schema_version"] == "roboguide.mission-request-recovery/v0.2"
    assert RequestRecoveryEvidence.from_json(document) == record.recovery_evidence
    document["schema_version"] = "roboguide.mission-request-recovery/v0.1"
    with pytest.raises(ValueError, match="declare its schema"):
        RequestRecoveryEvidence.from_json(document)


def test_diagnostic_feedback_is_closed_bound_and_backward_compatible(tmp_path: Path) -> None:
    """Counter partitions and exact slots are required; legacy evidence stays diagnostic-free."""
    record = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), AssessingController([])
    ).create("deliver the declared payload")
    assert record.plan is not None
    original = feedback(record.plan)
    role = original.roles[0]
    report = CandidateDiagnostics(role.task_id, role.role_id, 3, 2, (("status_stale", 1),))
    upgraded = replace(original, schema_version=DIAGNOSTIC_SCHEMA, candidate_diagnostics=(report,))
    assert InitialOperationAssessment.from_json(upgraded.to_json()) == upgraded
    assert InitialOperationAssessment.from_json(original.to_json()) == original
    assert original.candidate_diagnostics == ()
    schema = json.loads(
        Path(
            "contracts/mission/initial-operation-assessment-v0.2/assessment.schema.json"
        ).read_text()
    )
    assert set(schema["properties"]) == set(upgraded.to_json())
    assert set(schema["$defs"]["candidate_diagnostics"]["properties"]) == set(report.to_json())
    with pytest.raises(ValueError, match="partition"):
        replace(report, eligible_count=3)
    with pytest.raises(ValueError, match="partition"):
        replace(report, exclusions=(("invented_reason", 1),))
    with pytest.raises(ValueError, match="disagree"):
        replace(
            upgraded, candidate_diagnostics=(replace(report, considered_count=2, eligible_count=1),)
        )
    detached = replace(report, task_id="foreign-task")
    gap = replace(
        upgraded,
        decision="unavailable",
        reason_code="current_candidates_unavailable",
        checked_combinations=0,
        roles=(),
        candidate_diagnostics=(detached,),
    )
    assert not gap.matches_plan(record.plan)
    raw = report.to_json()
    raw["node_id"] = "private-executor"
    with pytest.raises(ValueError, match="fields"):
        CandidateDiagnostics.from_json(raw)
    with pytest.raises(ValueError, match="v0.2"):
        replace(upgraded, schema_version="roboguide.initial-operation-assessment/v0.1")
