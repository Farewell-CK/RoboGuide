"""Exercise the actual MI observer lifecycle using Controller-shaped software evidence."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from mission.api import MissionRequestHttpServer
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import MissionPlan
from mission.posture_verifier import WINDOW_MS, PostureGoalVerifier
from mission.posture_verifier_store import PostureVerificationError, PostureVerificationStore
from robonix_task.posture_goal import VERIFIER, predicate_for
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _inventory,
)

IDENTITY = {
    "source_id": "fixture-posture",
    "source_revision": 1,
    "tenant_id": "local",
    "certificate_sha256": "sha256:" + "c" * 64,
}


class Clock:
    """Expose a controllable suspend-inclusive clock without sleeping or authorizing actions."""

    def __init__(self) -> None:
        """Start in one fixed test boot and time namespace."""
        self.ms = 1001
        self.boot = "fixture-boot"

    def __call__(self) -> dict[str, Any]:
        """Return the original clock domain and current monotonic elapsed time."""
        return {"boot": self.boot, "namespace": [1, 2], "elapsed_ns": self.ms * 1_000_000}


def plan_document() -> dict[str, Any]:
    """Declare one posture verifier on a normal actor-aware Mission graph before admission."""
    raw: dict[str, Any] = json.loads(
        Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_bytes()
    )
    raw["mission"]["id"] = "fixture-mission"
    task = raw["tasks"][0]
    task["id"] = "fixture-posture"
    task["roles"][0]["execution_intent"]["parameters"]["expected_posture"] = "standing"
    task["satisfaction"] = {
        "basis": "verifier-evidence",
        "expected_effect": predicate_for("standing"),
        "verifier": {
            "contract": deepcopy(VERIFIER),
            "predicate": predicate_for("standing"),
            "max_evidence_age_ms": 5000,
        },
    }
    raw["tasks"] = [task]
    return raw


class Controller:
    """Offer only reads and evidence publication, with no task execution method."""

    def __init__(self, clock: Clock) -> None:
        """Keep a finite fixture projection and a complete original publication trace."""
        self.clock = clock
        self.posture = "down"
        self.epoch = "epoch-1"
        self.sequence = 1
        self.received = 1000
        self.calls: list[dict[str, Any]] = []
        self.fail_publication = False
        self.offline = False
        self.reads = 0
        self.view: dict[str, Any] = {
            "mission": {
                "mission_id": "fixture-mission",
                "group_id": "fixture-group",
                "plan_revision": 0,
                "status": "Running",
                "tasks": [{"task_id": "fixture-posture", "status": "AwaitingSatisfaction"}],
            },
            "tenant": {
                "mission_id": "fixture-mission",
                "tenant_id": "local",
                "source_plan_digest": "sha256:" + "d" * 64,
            },
            "admission": {
                "mission_id": "fixture-mission",
                "group_id": "fixture-group",
                "accepted_request_body_sha256": "",
            },
            "attempts": [
                {
                    "mission_id": "fixture-mission",
                    "task_id": "fixture-posture",
                    "role_id": "",
                    "node_id": "fixture-dog",
                    "execution_id": "fixture-attempt",
                    "status": "Completed",
                }
            ],
        }

    def posture_goal_view(self, mission_id: str) -> dict[str, Any]:
        """Return the exact synthetic original admission and live attempt set."""
        assert mission_id == "fixture-mission"
        return deepcopy(self.view)

    def posture_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return cached sample identity, spending freshness as the fixture clock advances."""
        self.reads += 1
        state = {
            "schema": "roboguide.state-query/v0.1",
            "observed_at_ms": self.clock.ms,
            "records": [
                {
                    "object": {"class": "node", "object_type": "robot", "object_id": "fixture-dog"},
                    "source": "node:fixture-dog/robot-observer",
                    "channel_id": "robot-state",
                    "semantic": "reported",
                    "stale": False,
                    "value": {
                        "payload_schema": "lite3.robot-state/v0.1",
                        "received_at_ms": self.received,
                        "valid_for_ms": 5000,
                        "source_epoch": self.epoch,
                        "sequence": self.sequence,
                        "value": {
                            "chassis": {
                                "contract_id": "robonix/primitive/chassis/state",
                                "availability": "current",
                                "age_ms": 1,
                                "provider_id": "fixture-provider",
                                "registration_id": "reg-1",
                                "received_at_ns": self.received * 1_000_000,
                                "value": {
                                    "posture": self.posture,
                                    "stale": False,
                                    "stale_ms": 5000,
                                },
                            }
                        },
                    },
                }
            ],
        }
        inventory = {
            "schema_version": "roboguide.inventory/v0.1",
            "nodes": [
                {
                    "node_id": "fixture-dog",
                    "liveness": "Unreachable" if self.offline else "Reachable",
                }
            ],
        }
        return state, inventory

    def publish_posture_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Record a single generic evidence effect, optionally losing its reply."""
        self.calls.append(deepcopy(evidence))
        if self.fail_publication:
            raise OSError("fixture lost reply")
        return {
            "schema_version": "roboguide.semantic-verifier-receipt/v0.1",
            "receipt": {"evidence": evidence},
        }

    def sample(self, ms: int, posture: str) -> None:
        """Advance both native sample identity and Controller receive identity."""
        self.clock.ms = ms
        self.sequence += 1
        self.received = ms - 1
        self.posture = posture


def setup(tmp_path: Path) -> tuple[PostureGoalVerifier, Controller, Clock]:
    """Compose the production observer and SQLite queue around a read-only software fixture."""
    clock = Clock()
    client = Controller(clock)
    worker = PostureGoalVerifier(
        PostureVerificationStore(tmp_path / "mi.sqlite3"), client, IDENTITY, clock=clock
    )
    plan = MissionPlan.from_json(plan_document())
    worker.prepare(plan, "local")
    original = worker.store.jobs()[0]["input"]
    client.view["admission"]["accepted_request_body_sha256"] = original["wire_plan_digest"]
    client.view["attempts"][0]["role_id"] = original["role_ids"][0]
    return worker, client, clock


def test_two_new_samples_publish_once_and_retain_original_receipt(tmp_path: Path) -> None:
    """Cached polls cannot count twice, and restarting a completed job cannot repeat an effect."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    assert worker.store.jobs()[0]["state"]["reports"] == []
    client.sample(1201, "standing")
    worker.step()
    worker.step()
    assert client.calls == []
    assert len(worker.store.jobs()[0]["state"]["reports"]) == 1
    client.sample(1501, "standing")
    worker.step()
    assert len(client.calls) == 1
    evidence = client.calls[0]
    assert evidence["verdict"] == "satisfied"
    assert evidence["attempts"][0]["attempt_id"] == "fixture-attempt"
    assert evidence["plan_digest"] == client.view["tenant"]["source_plan_digest"]
    assert evidence["source_id"] == IDENTITY["source_id"]
    assert 0 < evidence["valid_for_ms"] < 5000
    restarted = PostureGoalVerifier(worker.store, client, IDENTITY, clock=clock)
    restarted.step()
    assert len(client.calls) == 1
    assert restarted.store.jobs()[0]["status"] == "complete"


def test_negative_waits_for_original_window_with_two_live_reports(tmp_path: Path) -> None:
    """A fresh mismatch waits ten seconds; a poll slightly after the deadline still closes it."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    client.sample(9001, "down")
    worker.step()
    client.sample(10001, "down")
    worker.step()
    assert client.calls == []
    clock.ms = 1001 + WINDOW_MS + 51
    worker.step()
    assert len(client.calls) == 1
    assert client.calls[0]["verdict"] == "unsatisfied"
    assert worker.store.jobs()[0]["status"] == "complete"


@pytest.mark.parametrize(
    "failure",
    [
        "offline",
        "stale",
        "missing-second",
        "late-second",
        "epoch",
        "boot",
        "attempt",
        "plan",
        "admission",
        "tenant",
    ],
)
def test_uncertainty_never_publishes_final_evidence(tmp_path: Path, failure: str) -> None:
    """Missing, stale, foreign or expired inputs remain manual and cause no retry or action."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    client.sample(9001, "down")
    worker.step()
    if failure != "missing-second":
        client.sample(10001, "down")
    if failure == "offline":
        client.offline = True
    elif failure == "stale":
        client.received = 1000
    elif failure == "epoch":
        client.epoch = "epoch-2"
    elif failure == "boot":
        clock.boot = "another-boot"
    elif failure == "attempt":
        client.view["attempts"][0]["execution_id"] = "replacement-attempt"
    elif failure == "plan":
        client.view["tenant"]["source_plan_digest"] = "sha256:" + "e" * 64
    elif failure == "admission":
        client.view["admission"]["accepted_request_body_sha256"] = "sha256:" + "f" * 64
    elif failure == "tenant":
        client.view["tenant"]["tenant_id"] = "foreign"
    if failure != "late-second":
        worker.step()
    clock.ms = 1001 + WINDOW_MS + 10
    worker.step()
    assert client.calls == []
    assert worker.store.jobs()[0]["status"] == "manual"


def test_restart_preserves_anchor_and_deadline(tmp_path: Path) -> None:
    """A same-boot restart can collect the second sample without renewing the original window."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    anchor = deepcopy(worker.store.jobs()[0]["state"]["anchor"])
    client.sample(1201, "standing")
    worker.step()
    restarted = PostureGoalVerifier(worker.store, client, IDENTITY, clock=clock)
    client.sample(1501, "standing")
    restarted.step()
    assert len(client.calls) == 1
    assert restarted.store.jobs()[0]["state"]["anchor"] == anchor


def test_lost_publication_reply_is_retained_without_automatic_resend(tmp_path: Path) -> None:
    """Keep an interrupted effect uncertain without republishing it with fresh receive age."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    client.sample(1201, "standing")
    worker.step()
    client.fail_publication = True
    client.sample(1501, "standing")
    worker.step()
    assert worker.store.jobs()[0]["status"] == "publishing"
    restarted = PostureGoalVerifier(worker.store, client, IDENTITY, clock=clock)
    restarted.step()
    assert len(client.calls) == 1
    assert restarted.store.jobs()[0]["status"] == "manual"
    assert "uncertain" in restarted.store.jobs()[0]["state"]["reason"]


@pytest.mark.parametrize("change", ["basis", "predicate", "missing-posture", "tenant", "contract"])
def test_invalid_goal_refuses_before_admission(tmp_path: Path, change: str) -> None:
    """The queue never silently turns a legacy Task into a goal-verified Task."""
    clock = Clock()
    worker = PostureGoalVerifier(
        PostureVerificationStore(tmp_path / "mi.sqlite3"), Controller(clock), IDENTITY, clock=clock
    )
    raw = plan_document()
    task = raw["tasks"][0]
    tenant = "local"
    if change == "basis":
        task["satisfaction"] = {
            "basis": "execution-report",
            "expected_effect": "call returned",
            "verifier": None,
        }
    elif change == "predicate":
        task["satisfaction"]["verifier"]["predicate"] = "different effect"
    elif change == "missing-posture":
        del task["roles"][0]["execution_intent"]["parameters"]["expected_posture"]
    elif change == "tenant":
        tenant = "foreign"
    else:
        task["satisfaction"]["verifier"]["contract"]["name"] = "unrelated"
    with pytest.raises(PostureVerificationError):
        worker.prepare(MissionPlan.from_json(raw), tenant)
    assert worker.store.jobs() == []


def test_legacy_execution_report_is_not_enrolled_or_rewritten(tmp_path: Path) -> None:
    """Old execution-report history stays outside the new observer's queue."""
    worker, _, _ = setup(tmp_path)
    raw = plan_document()
    raw["mission"]["id"] = "legacy-mission"
    task = raw["tasks"][0]
    del task["roles"][0]["execution_intent"]["parameters"]["expected_posture"]
    task["satisfaction"] = {
        "basis": "execution-report",
        "expected_effect": "call returned",
        "verifier": None,
    }
    plan = MissionPlan.from_json(raw)
    before = plan.to_json()
    worker.prepare(plan, "local")
    assert plan.to_json() == before
    assert len(worker.store.jobs()) == 1


@pytest.mark.parametrize(
    "column,value",
    [("state_json", '{"reports":[]}'), ("status", "observing"), ("input_json", "{}")],
)
def test_corrupt_retained_job_is_refused(tmp_path: Path, column: str, value: str) -> None:
    """Corrupt source/progress/status cannot become a newly justified verification input."""
    worker, client, _ = setup(tmp_path)
    with sqlite3.connect(worker.store.database) as connection:
        connection.execute(f"UPDATE mission_posture_jobs SET {column}=?", (value,))
    with pytest.raises(PostureVerificationError, match="digest differs"):
        worker.step()
    assert client.calls == []


def test_rejected_admission_closes_queue_without_execution(tmp_path: Path) -> None:
    """A definitive Controller rejection frees active observation capacity without retrying it."""
    worker, client, _ = setup(tmp_path)
    worker.admission_result(MissionPlan.from_json(plan_document()), accepted=False)
    assert worker.store.jobs()[0]["status"] == "manual"
    worker.step()
    assert client.calls == []
    assert client.reads == 0


def test_sql_wait_cannot_renew_before_io_evidence_budget(tmp_path: Path) -> None:
    """A delayed publication journal causes manual handling before any HTTP effect."""
    worker, client, clock = setup(tmp_path)
    worker.step()
    client.sample(1201, "standing")
    worker.step()
    original = worker.store.prepare_publication

    def delayed(job: dict[str, Any], evidence: dict[str, Any]) -> None:
        """Spend the original SQLite waiting time without changing source observation time."""
        original(job, evidence)
        clock.ms += 1000

    worker.store.prepare_publication = delayed  # type: ignore[method-assign]
    client.sample(1501, "standing")
    worker.step()
    assert client.calls == []
    assert worker.store.jobs()[0]["state"]["reason"] == "before_io_publication_budget_exhausted"


@pytest.mark.parametrize("refuse", [False, True])
def test_request_engine_checks_observer_before_sole_admission(tmp_path: Path, refuse: bool) -> None:
    """The ordinary MI submission path must run the guard before any Controller POST."""
    controller = FakeController(_inventory())
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    calls: list[str] = []

    class Guard:
        """Inspect the existing admission boundary without authorizing any action itself."""

        def prepare(self, plan: MissionPlan, tenant: str | None) -> None:
            """Assert that no admission has occurred and preserve the authenticated tenant."""
            assert plan.mission.mission_id
            assert tenant == "local"
            assert controller.submissions == []
            calls.append("prepare")
            if refuse:
                raise PostureVerificationError("fixture invalid goal")

        def admission_result(self, plan: MissionPlan, *, accepted: bool) -> None:
            """Observe the sole Controller response after a successful guard."""
            assert plan.mission.mission_id
            assert accepted
            assert len(controller.submissions) == 1
            calls.append("receipt")

    engine.posture_verifier = Guard()  # type: ignore[assignment]
    with engine.authenticated_command("local", False):
        result = engine.create("fixture declared work")
    assert calls == (["prepare"] if refuse else ["prepare", "receipt"])
    assert len(controller.submissions) == (0 if refuse else 1)
    assert result.lifecycle.value == ("Failed" if refuse else "Accepted")


def test_service_readiness_and_close_own_observer_lifetime(tmp_path: Path) -> None:
    """The existing MI service holds its writer lease until the observation thread stops."""
    worker, client, _ = setup(tmp_path)
    controller = FakeController(_inventory())
    engine = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), controller)
    engine.posture_verifier = worker
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 65536)
    try:
        assert engine.readiness()["ready"] is False
        worker.start()
        assert engine.readiness()["posture_verifier"] == "ready"
        with pytest.raises(PostureVerificationError, match="already started"):
            worker.start()
    finally:
        server.server_close()
    assert not worker.ready()
    lease = engine.acquire_http_service_lock()
    lease.close()
    assert client.calls == []


def test_opt_in_posture_catalog_admits_declared_profile_and_goal(tmp_path: Path) -> None:
    """The deployment vocabulary must admit the exact new metadata and independent verifier."""
    raw = plan_document()
    role = raw["tasks"][0]["roles"][0]
    operation = {"namespace": "task", "name": "delegate", "version": "v1"}
    role["requirements"]["capabilities"] = [{"contract": operation, "constraints": []}]
    role["execution_intent"].update(
        operation=operation, parameters={"profile": "local-task-v1", "expected_posture": "standing"}
    )
    plan = MissionPlan.from_json(raw)
    catalog = CanonicalCapabilityCatalog.load(
        Path("integrations/robonix-task/deploy/posture-capability-catalog.json")
    )
    catalog.validate_plan(plan)
    clock = Clock()
    worker = PostureGoalVerifier(
        PostureVerificationStore(tmp_path / "mi.sqlite3"), Controller(clock), IDENTITY, clock=clock
    )
    worker.prepare(plan, "local")
    assert worker.store.jobs()[0]["input"]["expected_posture"] == "standing"
