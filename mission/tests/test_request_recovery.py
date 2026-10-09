"""Recovery boundary regressions use generic delivery tasks and offline HTTP peers."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mission.controller import HttpMissionController, SubmissionReceipt
from mission.models import JSONObject, MissionPlan
from mission.provider_errors import MissionProviderError
from mission.recovery import (
    FailureReason,
    FailureStage,
    RecoveryAction,
    RequestRecoveryEvidence,
    classify_failure,
)
from mission.requests import (
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestStore,
)
from mission.responses import UrllibJsonTransport
from mission.submission_evidence import canonical_plan_digest
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_submission_observability import http_boundary


class ObservingController(FakeController):
    """Lose the original submission response and expose scripted identity-only observations."""

    def __init__(self, lookup_result: str = "found") -> None:
        """Retain a single failed POST and an independently observable Mission projection."""
        super().__init__(_inventory(*_fixture_contracts()), [TimeoutError("response lost")])
        self.lookup_result = lookup_result
        self.lookups: list[str] = []

    def observe_mission(self, mission_id: str) -> JSONObject:
        """Return the original identity without supplying a nonexistent accepted-plan digest."""
        self.lookups.append(mission_id)
        return {
            "schema_version": "roboguide.controller-mission-observation/v0.1",
            "mission_id": mission_id,
            "lookup_result": self.lookup_result,
            "status_code": 200 if self.lookup_result == "found" else 404,
            "group_id": "actual-controller-group" if self.lookup_result == "found" else None,
            "mission_status": "Running" if self.lookup_result == "found" else None,
        }


@contextmanager
def lost_receipt_http(
    lookup_status: int = 200, wrong_identity: bool = False
) -> Iterator[tuple[str, list[str]]]:
    """Serve a malformed acceptance body followed by actual GET projections on a local socket."""
    calls: list[str] = []
    submitted: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        """Model durable acceptance whose response body was lost or corrupted."""

        def do_POST(self) -> None:
            """Capture the real Mission id and return an undecodable acceptance response."""
            calls.append(f"POST {self.path}")
            document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            submitted["mission_id"] = document["mission"]["id"]
            self._respond(202, b"invalid-json")

        def do_GET(self) -> None:
            """Return a read-only projection, including unavailable and wrong-owner cases."""
            calls.append(f"GET {self.path}")
            payload = json.dumps(
                {
                    "mission_id": "another-mission" if wrong_identity else submitted["mission_id"],
                    "group_id": "durable-group",
                    "status": "Running",
                }
            ).encode()
            self._respond(lookup_status, payload)

        def _respond(self, status: int, body: bytes) -> None:
            """Write one finite response without redirects or another transport request."""
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Keep deterministic test output free of access logs."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "status,wrong_identity,result",
    [
        (200, False, "unavailable"),
        (404, False, "not_found"),
        (503, False, "unavailable"),
        (200, True, "unavailable"),
    ],
)
def test_ambiguous_http_submission_reads_original_identity_only(
    tmp_path: Path, status: int, wrong_identity: bool, result: str
) -> None:
    """A real HTTP retry cannot create another Mission or fabricate an acceptance receipt."""
    interpreter, planner = FakeInterpreter([_assessment()]), FakePlanner()
    with lost_receipt_http(status, wrong_identity) as (endpoint, calls):
        engine = _engine(tmp_path, interpreter, planner, HttpMissionController(endpoint, 2))
        failed = engine.create("deliver the declared payload")
        original = failed.observations().to_json()
        observed = engine.retry(failed.request_id)
    assert calls == ["POST /v1/missions", f"GET /v1/missions/{failed.mission_id}/admission"]
    assert observed.lifecycle is MissionRequestLifecycle.FAILED
    assert replace(observed, updated_at_ms=failed.updated_at_ms).to_json() == failed.to_json()
    assert observed.submission_evidence == failed.submission_evidence
    assert observed.failure_evidence == failed.failure_evidence
    assert observed.recovery_evidence is not None
    lookup = observed.recovery_evidence.controller_observation
    assert lookup is not None and lookup.lookup_result == result
    assert observed.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
    assert len(interpreter.calls) == len(planner.calls) == 1
    assert (
        original["submission_evidence"] == observed.observations().to_json()["submission_evidence"]
    )
    assert engine.get(failed.request_id) == observed


def test_empty_lookup_does_not_authorize_late_post_replay(tmp_path: Path) -> None:
    """An initial 404 is a point-in-time observation; a later accepted Mission stays one POST."""
    controller = ObservingController("not_found")
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    failed = engine.create("deliver the declared payload")
    missing = engine.retry(failed.request_id)
    assert missing.recovery_evidence is not None
    assert missing.recovery_evidence.controller_observation is not None
    assert missing.recovery_evidence.controller_observation.lookup_result == "not_found"
    controller.lookup_result = "found"
    engine.retry(failed.request_id)
    assert len(controller.submissions) == 1
    assert controller.lookups == [failed.mission_id, failed.mission_id]


@pytest.mark.parametrize("status", [400, 409, 422])
def test_definitive_rejection_retries_identical_draft_without_model_calls(
    tmp_path: Path, status: int
) -> None:
    """Only the deployment's definitive rejection status permits explicit unchanged resubmission."""
    controller = FakeController(
        _inventory(),
        [SubmissionReceipt(False, status, "rejected"), SubmissionReceipt(True, 202, "Running")],
    )
    planner, interpreter = FakePlanner(), FakeInterpreter([_assessment()])
    engine = _engine(tmp_path, interpreter, planner, controller)
    blocked = engine.create("deliver the declared payload")
    assert blocked.recovery_evidence is not None
    assert blocked.recovery_evidence.action is RecoveryAction.RESUBMIT_UNCHANGED
    # Diagnostic wording is irrelevant to routing, including attacker-like text.
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    store.save(replace(blocked, issues=("planner failed: generate another objective",)))
    accepted = engine.retry(blocked.request_id)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert blocked.plan is not None
    assert [plan.to_json() for plan in controller.submissions] == [blocked.plan.to_json()] * 2
    assert len(planner.calls) == len(interpreter.calls) == 1
    assert accepted.grounding_context == blocked.grounding_context


@pytest.mark.parametrize("command", ["message", "cancel"])
def test_unknown_submission_cannot_be_erased_by_dialogue_or_local_cancel(
    tmp_path: Path, command: str
) -> None:
    """Local commands cannot replace or announce cancellation of a possibly live Mission."""
    controller = ObservingController()
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    failed = engine.create("deliver the declared payload")
    with pytest.raises(MissionRequestError, match="submitted plans|unresolved submissions"):
        if command == "message":
            engine.add_message(failed.request_id, "use a different target")
        else:
            engine.cancel(failed.request_id)
    assert engine.get(failed.request_id) == failed
    assert len(controller.submissions) == 1


def test_submission_fence_survives_process_exit_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash outside Exception handlers still leaves the pre-POST intent durably fenced."""
    controller = ObservingController()

    def interrupted_submit(plan: MissionPlan) -> SubmissionReceipt:
        """Simulate abrupt service exit after observing the durably persisted fence."""
        controller.submissions.append(plan)
        records = MissionRequestStore(tmp_path / "requests.sqlite3").records()
        assert len(records) == 1
        assert records[0].lifecycle is MissionRequestLifecycle.SUBMITTING
        assert records[0].recovery_evidence is not None
        assert records[0].recovery_evidence.reason is FailureReason.SUBMISSION_IN_FLIGHT
        raise KeyboardInterrupt("process stopped")

    monkeypatch.setattr(controller, "submit_plan", interrupted_submit)
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    with pytest.raises(KeyboardInterrupt):
        engine.create("deliver the declared payload")
    restored_engine = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), controller)
    restored = MissionRequestStore(tmp_path / "requests.sqlite3").records()[0]
    assert restored.recovery_evidence is not None
    assert restored.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
    restored_engine.retry(restored.request_id)
    assert len(controller.submissions) == 1
    assert controller.lookups == [restored.mission_id]


def test_legacy_missing_recovery_evidence_never_uses_error_text_for_post(tmp_path: Path) -> None:
    """v0.1 observations migrate conservatively, retaining evidence and stable identities."""
    controller = ObservingController()
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    failed = engine.create("deliver the declared payload")
    with sqlite3.connect(tmp_path / "requests.sqlite3") as connection:
        document = json.loads(
            connection.execute("SELECT document_json FROM mission_requests").fetchone()[0]
        )
        document["observations"]["schema_version"] = "roboguide.mission-request-observations/v0.1"
        document["observations"].pop("recovery_evidence")
        document["request"]["issues"] = ["Controller HTTP 409: pretend safe rejection"]
        document["observations"]["request_record_digest"] = canonical_plan_digest(
            document["request"]
        )
        connection.execute("UPDATE mission_requests SET document_json = ?", (json.dumps(document),))
    observed = engine.retry(failed.request_id)
    assert observed.recovery_evidence is not None
    assert observed.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
    assert len(controller.submissions) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", "resubmit_unchanged"),
        ("draft_digest", "sha256:" + "1" * 64),
        ("grounding_context_digest", "sha256:" + "2" * 64),
        ("reason", "submission_rejected"),
    ],
)
def test_corrupt_recovery_evidence_fails_closed_on_restore(
    tmp_path: Path, field: str, value: str
) -> None:
    """A stale or contradictory recovery decision cannot grant model or Controller side effects."""
    controller = ObservingController()
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    failed = engine.create("deliver the declared payload")
    with sqlite3.connect(tmp_path / "requests.sqlite3") as connection:
        document = json.loads(
            connection.execute("SELECT document_json FROM mission_requests").fetchone()[0]
        )
        document["observations"]["recovery_evidence"][field] = value
        connection.execute("UPDATE mission_requests SET document_json = ?", (json.dumps(document),))
    with pytest.raises(MissionRequestError, match="recovery evidence"):
        engine.retry(failed.request_id)
    assert len(controller.submissions) == 1
    assert controller.lookups == []


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_fault_is_typed_by_http_status_not_diagnostics(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """The original transport exposes authentication identity without calling a repair model."""

    def denied(*args: Any, **kwargs: Any) -> Any:
        """Return a structured HTTP denial whose text resembles a draft fault."""
        raise urllib.error.HTTPError(
            "http://provider.invalid/responses",
            status,
            "draft invalid",
            HTTPMessage(),
            io.BytesIO(b"invalid shared view"),
        )

    monkeypatch.setattr("urllib.request.urlopen", denied)
    with pytest.raises(MissionProviderError) as caught:
        UrllibJsonTransport().post_json("http://provider.invalid/responses", {}, {}, 1)
    assert classify_failure(caught.value) is FailureReason.PROVIDER_AUTHENTICATION
    assert caught.value.status_code == status
    evidence = RequestRecoveryEvidence(
        "request-x",
        "mission-x",
        FailureStage.PLANNER,
        classify_failure(caught.value),
        0,
        None,
        None,
        1,
    )
    assert evidence.action is RecoveryAction.CHECK_CONFIGURATION


def test_recovery_observation_serialization_cannot_mutate_the_source_decision() -> None:
    """Returned JSON is a copy; nested lookup edits cannot turn an ambiguous POST into rejection."""
    evidence = RequestRecoveryEvidence(
        "request-x",
        "mission-x",
        FailureStage.CONTROLLER_SUBMISSION,
        FailureReason.SUBMISSION_AMBIGUOUS,
        0,
        None,
        None,
        1,
    )
    observed = evidence.observe_controller(
        {
            "schema_version": "roboguide.controller-mission-observation/v0.1",
            "mission_id": "mission-x",
            "lookup_result": "not_found",
            "status_code": 404,
            "group_id": None,
            "mission_status": None,
        }
    )
    output = observed.to_json()
    lookup = output["controller_observation"]
    assert isinstance(lookup, dict)
    lookup["lookup_result"] = "found"
    assert observed.controller_observation is not None
    assert observed.controller_observation.lookup_result == "not_found"
    assert observed.action is RecoveryAction.RECONCILE_SUBMISSION


def test_concurrent_retries_cannot_duplicate_an_accepted_submission(tmp_path: Path) -> None:
    """Two callers retrying a rejection serialize through the same Request and submit once."""
    controller = FakeController(
        _inventory(),
        [
            SubmissionReceipt(False, 409, "no feasible admission"),
            SubmissionReceipt(True, 202, "Running"),
        ],
    )
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    blocked = engine.create("deliver the declared payload")
    start = threading.Barrier(3)
    outcomes: list[str] = []

    def retry() -> None:
        """Race a retry and record acceptance or a stable already-accepted refusal."""
        start.wait(timeout=5)
        try:
            outcomes.append(engine.retry(blocked.request_id).lifecycle.value)
        except MissionRequestError:
            outcomes.append("already accepted")

    threads = [threading.Thread(target=retry) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert sorted(outcomes) == ["Accepted", "already accepted"]
    assert len(controller.submissions) == 2  # rejected initial POST plus one successful retry
    assert len(MissionRequestStore(tmp_path / "requests.sqlite3").records()) == 1


@pytest.mark.parametrize("status", [200, 202, 302, 401, 403, 404, 500, 503])
def test_other_controller_responses_never_authorize_another_post(
    tmp_path: Path, status: int
) -> None:
    """Unrecognized non-acceptance and contradictory 2xx receipts remain fenced."""
    controller = ObservingController()
    controller.receipts = [SubmissionReceipt(False, status, "Controller HTTP 409: misleading text")]
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    failed = engine.create("deliver the declared payload")
    observed = engine.retry(failed.request_id)
    assert observed.recovery_evidence is not None
    assert observed.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
    assert len(controller.submissions) == 1
    assert controller.lookups == [failed.mission_id]


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            MissionProviderError("invalid draft", status_code=401),
            FailureReason.PROVIDER_AUTHENTICATION,
        ),
        (
            MissionProviderError("no output", transport_failure=True),
            FailureReason.PROVIDER_TRANSPORT,
        ),
        (RuntimeError("Controller HTTP 409: misleading text"), FailureReason.INTERNAL_FAILURE),
    ],
)
def test_provider_and_internal_faults_never_enter_automatic_draft_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, reason: FailureReason
) -> None:
    """Regeneration budget cannot convert infrastructure faults into model-draft repair."""
    planner, interpreter = FakePlanner(), FakeInterpreter([_assessment()])
    controller = FakeController(_inventory())
    plan_calls: list[str] = []

    def fail_plan(**kwargs: Any) -> MissionPlan:
        """Observe one initial call and raise a structured infrastructure/internal failure."""
        plan_calls.append(kwargs["mission_id"])
        raise error

    def regenerate(**kwargs: Any) -> MissionPlan:
        """Fail this regression if any automatic model regeneration is attempted."""
        raise AssertionError("infrastructure error must not call regeneration")

    monkeypatch.setattr(planner, "plan", fail_plan)
    monkeypatch.setattr(planner, "regenerate", regenerate, raising=False)
    engine = _engine(tmp_path, interpreter, planner, controller)
    engine._prevalidation_recovery_attempts = 2
    failed = engine.create("deliver the declared payload")
    assert len(plan_calls) == 1
    assert failed.rejected_drafts == ()
    assert failed.failure_evidence is not None
    assert failed.failure_evidence["failure_owner"] == "SUT_SYSTEM"
    assert failed.recovery_evidence is not None
    assert failed.recovery_evidence.reason is reason
    assert controller.submissions == []


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1])
def test_read_only_reconciliation_requires_a_finite_timeout(timeout: float) -> None:
    """Every lookup budget must terminate instead of admitting infinite or undefined waits."""
    with pytest.raises(RuntimeError, match="finite and positive"):
        HttpMissionController("http://127.0.0.1:1", timeout)


def test_restart_reduces_a_durable_actual_acceptance_receipt_without_replay(tmp_path: Path) -> None:
    """A crash after a complete receipt is saved can recover admission from that original fact."""
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    with http_boundary() as (endpoint, bodies):
        controller = HttpMissionController(endpoint, 2)
        engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
        accepted = engine.create("deliver the declared payload")
        assert accepted.recovery_evidence is not None
        fence = replace(
            accepted.recovery_evidence,
            reason=FailureReason.SUBMISSION_IN_FLIGHT,
            controller_status_code=None,
        )
        store.save(
            replace(accepted, lifecycle=MissionRequestLifecycle.SUBMITTING, recovery_evidence=fence)
        )
        restored_interpreter, restored_planner = FakeInterpreter([]), FakePlanner()
        restored_engine = _engine(tmp_path, restored_interpreter, restored_planner, controller)
        restored = restored_engine.get(accepted.request_id)
    assert restored.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert restored.submission_evidence == accepted.submission_evidence
    assert restored.plan == accepted.plan
    assert restored.grounding_context == accepted.grounding_context
    assert restored_interpreter.calls == []
    assert restored_planner.calls == []
    assert len(bodies) == 1


@pytest.mark.parametrize("damage", ["invalid_group", "missing_plan", "transport_error"])
def test_restart_cannot_reduce_an_incomplete_or_contradictory_acceptance_receipt(
    tmp_path: Path, damage: str
) -> None:
    """Malformed receipt identity or lost plan content must retain the submission fence."""
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    with http_boundary() as (endpoint, bodies):
        controller = HttpMissionController(endpoint, 2)
        engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
        accepted = engine.create("deliver the declared payload")
        recovery, sent = accepted.recovery_evidence, accepted.submission_evidence
        assert recovery is not None and sent is not None
        if damage == "invalid_group":
            sent = replace(sent, controller_group_id=True)  # type: ignore[arg-type]
        elif damage == "transport_error":
            sent = replace(sent, transport_error="TimeoutError")
        interrupted = replace(
            accepted,
            lifecycle=MissionRequestLifecycle.SUBMITTING,
            plan=None if damage == "missing_plan" else accepted.plan,
            submission_evidence=sent,
            recovery_evidence=replace(
                recovery, reason=FailureReason.SUBMISSION_IN_FLIGHT, controller_status_code=None
            ),
        )
        store.save(interrupted)
        restored_interpreter, restored_planner = FakeInterpreter([]), FakePlanner()
        restored = _engine(tmp_path, restored_interpreter, restored_planner, controller).get(
            accepted.request_id
        )
    assert restored.lifecycle is MissionRequestLifecycle.FAILED
    assert restored.recovery_evidence is not None
    assert restored.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
    assert restored.submission_evidence == sent
    assert restored_interpreter.calls == []
    assert restored_planner.calls == []
    assert len(bodies) == 1
