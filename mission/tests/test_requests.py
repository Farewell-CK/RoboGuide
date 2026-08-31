"""Deterministic tests for the Mission Request grounding and submission state machine."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import pytest
from mission.api import MissionRequestHttpServer
from mission.controller import (
    InventoryCapability,
    InventoryNode,
    InventoryResource,
    InventorySnapshot,
    SubmissionReceipt,
)
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.requests import (
    IntentAssessment,
    MissionRequestEngine,
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestRecord,
    MissionRequestStore,
)

FIXTURE = Path("scenarios/phase1-mission-v0.2/mission-plan.json")


class FakeInterpreter:
    """Return scripted assessments and retain dialogue/inventory calls."""

    def __init__(self, assessments: list[IntentAssessment]) -> None:
        """Initialize a finite assessment queue."""
        self.assessments = assessments
        self.calls: list[tuple[str, tuple[str, ...], InventorySnapshot]] = []

    def interpret(
        self,
        instruction: str,
        messages: tuple[str, ...],
        inventory: InventorySnapshot,
    ) -> IntentAssessment:
        """Record one grounding call and return its scripted result."""
        self.calls.append((instruction, messages, inventory))
        if not self.assessments:
            raise AssertionError("fake interpreter assessment queue is empty")
        return self.assessments.pop(0)


class FakePlanner:
    """Create a valid fixture-shaped plan with engine-owned Mission identity."""

    def __init__(self) -> None:
        """Initialize an inspectable call list."""
        self.calls: list[tuple[str, GroundedIntent]] = []

    def plan(self, mission_id: str, grounded_intent: GroundedIntent) -> MissionPlan:
        """Return a strict plan while retaining the complete grounded Planner input."""
        self.calls.append((mission_id, grounded_intent))
        raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
        mission = cast(JSONObject, raw["mission"])
        mission["id"] = mission_id
        mission["objective"] = grounded_intent.objective
        return MissionPlan.from_json(raw)


class FakeController:
    """Expose mutable advisory inventory and scripted submission receipts."""

    def __init__(
        self,
        inventory: InventorySnapshot,
        receipts: list[SubmissionReceipt | Exception] | None = None,
    ) -> None:
        """Initialize inventory, receipts, and an inspectable submission list."""
        self.snapshot = inventory
        self.receipts = receipts or [SubmissionReceipt(True, 202, "Running")]
        self.submissions: list[MissionPlan] = []

    def inventory(self) -> InventorySnapshot:
        """Return the current fake snapshot exactly once per engine processing pass."""
        return self.snapshot

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Record a plan and return the next explicit Controller outcome."""
        self.submissions.append(plan)
        if not self.receipts:
            raise AssertionError("fake Controller receipt queue is empty")
        receipt = self.receipts.pop(0)
        if isinstance(receipt, Exception):
            raise receipt
        return receipt


class SequenceIds:
    """Generate deterministic UUID-shaped tokens for Request and Mission identities."""

    def __init__(self) -> None:
        """Start before the first deterministic identity."""
        self.value = 0

    def __call__(self) -> str:
        """Return the next zero-padded lowercase hexadecimal token."""
        self.value += 1
        return f"{self.value:032x}"


class SequenceClock:
    """Generate deterministic increasing persistence timestamps."""

    def __init__(self) -> None:
        """Start before the first timestamp."""
        self.value = 100

    def __call__(self) -> int:
        """Return the next millisecond value."""
        self.value += 1
        return self.value


def _assessment(*questions: str) -> IntentAssessment:
    """Build one normalized objective with optional open questions."""
    return IntentAssessment(
        objective="deliver the payload through the approved route",
        constraints=("preserve local safety",),
        assumptions=(),
        open_questions=tuple(questions),
    )


def _inventory(*contracts: str) -> InventorySnapshot:
    """Build one healthy reachable node advertising the supplied canonical contracts."""
    node = InventoryNode(
        node_id="dog-a",
        reported_health="Online",
        source_observed_at_ms=8,
        received_at_ms=9,
        liveness="Reachable",
        liveness_observed_at_ms=10,
        capabilities=tuple(
            InventoryCapability(kind, True)
            for kind in ("mobility", "transport", "compute", "observation")
        ),
        contracts=tuple(contracts),
        resources=(
            InventoryResource("compute-a", "compute", 1),
            InventoryResource("space-a", "space", 1),
        ),
    )
    return InventorySnapshot(observed_at_ms=10, nodes=(node,))


def _fixture_contracts() -> tuple[str, ...]:
    """Return every exact contract required by the Phase 1 fixture planner."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    tasks = cast(list[JSONObject], raw["tasks"])
    contracts: set[str] = set()
    for task in tasks:
        roles = cast(list[JSONObject], task["roles"])
        for role in roles:
            contract = cast(JSONObject, role["contract"])
            contracts.add(f"{contract['namespace']}.{contract['name']}@{contract['version']}")
    return tuple(sorted(contracts))


def _engine(
    tmp_path: Path,
    interpreter: FakeInterpreter,
    planner: FakePlanner,
    controller: FakeController,
    risk_contracts: frozenset[str] = frozenset(),
) -> MissionRequestEngine:
    """Compose one deterministic engine over a real temporary SQLite store."""
    return MissionRequestEngine(
        MissionRequestStore(tmp_path / "requests.sqlite3"),
        interpreter,
        planner,
        controller,
        risk_contracts,
        SequenceIds(),
        SequenceClock(),
    )


def test_ambiguous_instruction_loops_before_planning_then_auto_accepts(tmp_path: Path) -> None:
    """Open questions prevent planning until a user message resolves the missing goal."""
    interpreter = FakeInterpreter([_assessment("Which destination?"), _assessment()])
    planner = FakePlanner()
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, interpreter, planner, controller)

    initial = engine.create("一只可以运输的机器狗")
    assert initial.lifecycle is MissionRequestLifecycle.NEEDS_CLARIFICATION
    assert planner.calls == []
    assert controller.submissions == []

    accepted = engine.add_message(initial.request_id, "把物品送到实验室入口")
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.messages == ("把物品送到实验室入口",)
    assert len(planner.calls) == 1
    assert planner.calls[0][1] == GroundedIntent(
        "deliver the payload through the approved route",
        ("preserve local safety",),
        (),
    )
    assert len(controller.submissions) == 1


def test_assessment_with_questions_cannot_form_a_grounded_planner_input() -> None:
    """The typed handoff rejects unresolved ambiguity even outside the engine loop."""
    with pytest.raises(MissionRequestError, match="open questions"):
        _assessment("Which destination?").grounded_intent()


def test_risk_policy_requires_revision_bound_approval(tmp_path: Path) -> None:
    """A risky clear plan waits, rejects stale approval, then submits the current draft once."""
    interpreter = FakeInterpreter([_assessment()])
    planner = FakePlanner()
    controller = FakeController(_inventory(*_fixture_contracts()))
    risk = frozenset({_fixture_contracts()[0]})
    engine = _engine(tmp_path, interpreter, planner, controller, risk)

    waiting = engine.create("执行明确的运输任务")
    assert waiting.lifecycle is MissionRequestLifecycle.AWAITING_APPROVAL
    assert waiting.approval_required is True
    assert waiting.draft_digest is not None
    with pytest.raises(MissionRequestError, match="stale"):
        engine.approve(waiting.request_id, waiting.draft_revision + 1, waiting.draft_digest)

    accepted = engine.approve(waiting.request_id, waiting.draft_revision, waiting.draft_digest)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(controller.submissions) == 1


def test_missing_capability_blocks_without_submission_and_retry_rechecks_inventory(
    tmp_path: Path,
) -> None:
    """Advisory preflight blocks missing contracts and a later retry reads fresh inventory."""
    interpreter = FakeInterpreter([_assessment(), _assessment()])
    planner = FakePlanner()
    controller = FakeController(_inventory())
    engine = _engine(tmp_path, interpreter, planner, controller)

    blocked = engine.create("执行明确的运输任务")
    assert blocked.lifecycle is MissionRequestLifecycle.BLOCKED
    assert blocked.issues[0].startswith("role requirement unavailable")
    assert controller.submissions == []

    controller.snapshot = _inventory(*_fixture_contracts())
    accepted = engine.retry(blocked.request_id)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.draft_revision == 2


def test_unavailable_coarse_capability_blocks_even_when_contract_is_registered(
    tmp_path: Path,
) -> None:
    """Advisory preflight checks availability instead of trusting registration alone."""
    inventory = _inventory(*_fixture_contracts())
    node = inventory.nodes[0]
    controller = FakeController(
        InventorySnapshot(
            inventory.observed_at_ms,
            (
                InventoryNode(
                    node.node_id,
                    node.reported_health,
                    node.source_observed_at_ms,
                    node.received_at_ms,
                    node.liveness,
                    node.liveness_observed_at_ms,
                    tuple(
                        InventoryCapability(capability.kind, capability.kind != "compute")
                        for capability in node.capabilities
                    ),
                    node.contracts,
                    node.resources,
                ),
            ),
        )
    )
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)

    record = engine.create("执行明确的运输任务")

    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert any("capability=compute" in issue for issue in record.issues)
    assert controller.submissions == []


def test_controller_rejection_remains_blocked_instead_of_fabricating_acceptance(
    tmp_path: Path,
) -> None:
    """A rejected internal MissionPlan remains inspectable and never becomes Running locally."""
    controller = FakeController(
        _inventory(*_fixture_contracts()),
        [SubmissionReceipt(False, 409, "resource conflict")],
    )
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("执行明确的运输任务")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert record.issues == ("Controller HTTP 409: resource conflict",)


def test_submission_retry_reuses_exact_draft_without_replanning(tmp_path: Path) -> None:
    """A lost Controller response retries the persisted plan rather than asking the model again."""
    interpreter = FakeInterpreter([_assessment()])
    planner = FakePlanner()
    controller = FakeController(
        _inventory(*_fixture_contracts()),
        [RuntimeError("response lost"), SubmissionReceipt(True, 202, "Running")],
    )
    engine = _engine(tmp_path, interpreter, planner, controller)

    failed = engine.create("执行明确的运输任务")
    assert failed.lifecycle is MissionRequestLifecycle.FAILED
    assert failed.issues == ("submission failed: response lost",)
    accepted = engine.retry(failed.request_id)

    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(planner.calls) == 1
    assert len(interpreter.calls) == 1
    assert controller.submissions[0].to_json() == controller.submissions[1].to_json()


def test_restart_fences_interrupted_submission_as_failed(tmp_path: Path) -> None:
    """Startup never resumes an ambiguous model or Controller side effect implicitly."""
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    record = MissionRequestRecord(
        request_id="request-" + "1" * 32,
        mission_id="mission-" + "2" * 32,
        instruction="test",
        messages=(),
        lifecycle=MissionRequestLifecycle.SUBMITTING,
        assessment=None,
        plan=None,
        draft_revision=0,
        draft_digest=None,
        approval_required=False,
        issues=(),
        created_at_ms=1,
        updated_at_ms=1,
    )
    store.save(record)
    MissionRequestEngine(
        store,
        FakeInterpreter([]),
        FakePlanner(),
        FakeController(_inventory()),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
    )
    restored = store.get(record.request_id)
    assert restored is not None
    assert restored.lifecycle is MissionRequestLifecycle.FAILED
    assert "submission interrupted" in restored.issues[0]


def test_restart_fences_received_request_for_explicit_retry(tmp_path: Path) -> None:
    """A crash before interpretation turns Received into a visible retryable failure."""
    store = MissionRequestStore(tmp_path / "received.sqlite3")
    record = MissionRequestRecord(
        request_id="request-" + "3" * 32,
        mission_id="mission-" + "4" * 32,
        instruction="test",
        messages=("clarified target",),
        lifecycle=MissionRequestLifecycle.RECEIVED,
        assessment=None,
        plan=None,
        draft_revision=0,
        draft_digest=None,
        approval_required=False,
        issues=(),
        created_at_ms=1,
        updated_at_ms=1,
    )
    store.save(record)

    MissionRequestEngine(
        store,
        FakeInterpreter([]),
        FakePlanner(),
        FakeController(_inventory()),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
    )

    restored = store.get(record.request_id)
    assert restored is not None
    assert restored.lifecycle is MissionRequestLifecycle.FAILED
    assert "deliberation interrupted" in restored.issues[0]


def test_http_api_accepts_only_instruction_and_returns_durable_projection(tmp_path: Path) -> None:
    """The public API creates internal IDs and GET returns the same persisted Accepted request."""
    engine = _engine(
        tmp_path,
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        FakeController(_inventory(*_fixture_contracts())),
    )
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        payload = json.dumps({"instruction": "执行明确的运输任务"}).encode()
        request = urllib.request.Request(
            f"{endpoint}/v1/mission-requests",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
            created = cast(JSONObject, json.loads(response.read()))
        assert created["lifecycle"] == "Accepted"
        assert cast(str, created["request_id"]).startswith("request-")
        injected = urllib.request.Request(
            f"{endpoint}/v1/mission-requests",
            data=json.dumps({"instruction": "test", "mission_id": "caller-owned"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(injected, timeout=2)  # noqa: S310
        assert failure.value.code == 400
        with urllib.request.urlopen(  # noqa: S310
            f"{endpoint}/v1/mission-requests/{created['request_id']}", timeout=2
        ) as response:
            fetched = cast(JSONObject, json.loads(response.read()))
        assert fetched == created
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_inventory_availability_requires_online_and_reachable() -> None:
    """Mission preflight accepts schedulable health but rejects offline or unreachable nodes."""
    available = (InventoryCapability("mobility", True),)
    online = InventoryNode(
        "dog-a", "Online", 1, 2, "Reachable", 3, available, ("mobility.move@v1",), ()
    )
    degraded = InventoryNode(
        "dog-d", "Degraded", 1, 2, "Reachable", 3, available, ("mobility.slow@v1",), ()
    )
    offline = InventoryNode(
        "dog-b", "Offline", 1, 2, "Reachable", 3, available, ("compute.infer@v1",), ()
    )
    stale = InventoryNode(
        "dog-c",
        "Online",
        1,
        2,
        "Unreachable",
        3,
        available,
        ("observation.scan@v1",),
        (),
    )
    snapshot = InventorySnapshot(1, (online, degraded, offline, stale))
    assert snapshot.available_contracts() == frozenset({"mobility.move@v1", "mobility.slow@v1"})


def test_inventory_wire_parser_preserves_observation_and_readiness_facts() -> None:
    """The Python boundary retains the complete Rust inventory v0.1 shape."""
    wire: JSONObject = {
        "schema_version": "roboguide.inventory/v0.1",
        "observed_at_ms": 10,
        "nodes": [
            {
                "node_id": "dog-a",
                "reported_health": "Degraded",
                "source_observed_at_ms": 7,
                "received_at_ms": 8,
                "liveness": "Reachable",
                "liveness_observed_at_ms": 9,
                "capabilities": [{"kind": "transport", "available": True}],
                "contracts": ["mobility.move@v1"],
                "resources": [{"resource_id": "space-a", "kind": "space", "capacity": 2}],
            }
        ],
    }

    snapshot = InventorySnapshot.from_json(wire)

    assert snapshot.to_json() == wire
    assert snapshot.supports_requirement("transport", "mobility.move@v1", "space")
