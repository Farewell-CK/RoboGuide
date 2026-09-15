"""Deterministic lifecycle tests for the Habitat Local EAIOS boundary."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import (  # noqa: E402
    CanonicalMobilityInvocation,
    ExecutionStore,
    HabitatLocalAdapter,
    IntegrationError,
    LocalExecutionOutcome,
)


class ControlledBackend:
    """Deterministic backend that exposes explicit running, finish, and cancel gates."""

    def __init__(self) -> None:
        """Create synchronization points without starting any work."""
        self.finish = threading.Event()
        self.started = threading.Event()
        self.closed = False

    def initialize(self) -> None:
        """Accept initialization without external Habitat dependencies."""

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Stay running until completion or an explicitly observed cancel request."""
        running(f"navigating to {invocation.destination}")
        self.started.set()
        while not self.finish.wait(0.01):
            if cancellation_requested():
                return _outcome("CANCELLED", "cancelled after local stop", invocation.destination)
        if cancellation_requested():
            return _outcome("CANCELLED", "cancelled after local stop", invocation.destination)
        return _outcome("COMPLETED", "destination reached", invocation.destination)

    def readiness_detail(self) -> str:
        """Return deterministic readiness evidence."""
        return "controlled Habitat backend ready"

    def close(self) -> None:
        """Record that adapter shutdown reached the backend."""
        self.closed = True


class FailingBackend(ControlledBackend):
    """Backend fixture that proves failed initialization still releases local ownership."""

    def initialize(self) -> None:
        """Reject initialization before any execution can be accepted."""
        raise IntegrationError("configured Habitat environment is unavailable")


def _request() -> dict[str, object]:
    """Build one exact Node workflow invocation with every identity and semantic field."""
    return {
        "invocation": {
            "mission_id": "mission-c1-s0",
            "task_id": "navigate-target",
            "group_id": "group-c1-s0",
            "role_id": "navigator",
            "operation": "mobility.navigate@v1",
            "objective": "Navigate the Habitat robot to the semantic target.",
            "parameters": {"destination": "any_targets|0"},
            "resource_ids": ["habitat-navigation-slot"],
        }
    }


def _outcome(state: str, detail: str, destination: str) -> LocalExecutionOutcome:
    """Build deterministic terminal evidence for adapter lifecycle tests."""
    return LocalExecutionOutcome(
        state=state,
        detail=detail,
        episode_id="51",
        scene_id="scene-test",
        destination=destination,
        simulator_steps=7,
        initial_position=(0.0, 0.0, 0.0),
        final_position=(1.0, 0.0, 2.0),
    )


def _adapter(tmp_path: Path) -> tuple[HabitatLocalAdapter, ControlledBackend]:
    """Create one adapter with a durable temporary store and controlled backend."""
    backend = ControlledBackend()
    adapter = HabitatLocalAdapter(
        ExecutionStore(tmp_path / "habitat.sqlite3"),
        lambda: backend,
        initialization_timeout_s=1.0,
    )
    return adapter, backend


def _wait_state(adapter: HabitatLocalAdapter, execution_id: str, expected: str) -> None:
    """Poll one local handle within a bounded deterministic test budget."""
    for _ in range(100):
        if adapter.status({"execution_id": execution_id})["state"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"execution {execution_id} did not reach {expected}")


def test_invocation_preserves_semantic_identity_and_committed_resources() -> None:
    """The bridge retains canonical What while accepting no Habitat-specific wire fields."""
    invocation = CanonicalMobilityInvocation.from_request(_request())
    assert invocation.operation == "mobility.navigate@v1"
    assert invocation.objective == "Navigate the Habitat robot to the semantic target."
    assert invocation.parameters == {"destination": "any_targets|0"}
    assert invocation.resource_ids == ("habitat-navigation-slot",)
    assert invocation.mission_id == "mission-c1-s0"
    assert invocation.task_id == "navigate-target"
    assert invocation.group_id == "group-c1-s0"
    assert invocation.role_id == "navigator"


def test_invocation_rejects_unknown_operation_and_local_how() -> None:
    """Unsupported operations and undeclared pose-level fields fail the bridge closed."""
    unknown = _request()
    invocation = unknown["invocation"]
    assert isinstance(invocation, dict)
    invocation["operation"] = "habitat.oracle_nav@v1"
    with pytest.raises(IntegrationError, match="unsupported canonical operation"):
        CanonicalMobilityInvocation.from_request(unknown)

    local_how = _request()
    local_invocation = local_how["invocation"]
    assert isinstance(local_invocation, dict)
    local_invocation["oracle_entity_index"] = 4
    with pytest.raises(IntegrationError, match="fields do not match"):
        CanonicalMobilityInvocation.from_request(local_how)


def test_real_async_shape_reports_running_before_completed(tmp_path: Path) -> None:
    """Execute returns a stable handle while status progresses through a true running fact."""
    adapter, backend = _adapter(tmp_path)
    try:
        accepted = adapter.accept(_request())
        execution_id = str(accepted["execution_id"])
        assert accepted["state"] == "ACCEPTED"
        assert not backend.started.is_set()
        adapter.dispatch(execution_id)
        assert backend.started.wait(1.0)
        _wait_state(adapter, execution_id, "RUNNING")
        running = adapter.status({"execution_id": execution_id})
        assert running["objective"] == _request()["invocation"]["objective"]  # type: ignore[index]
        assert running["resource_ids"] == ["habitat-navigation-slot"]

        backend.finish.set()
        _wait_state(adapter, execution_id, "COMPLETED")
        completed = adapter.status({"execution_id": execution_id})
        assert (
            completed["local_outcome"]
            == _outcome("COMPLETED", "destination reached", "any_targets|0").as_dict()
        )
    finally:
        backend.finish.set()
        adapter.close()
    assert backend.closed


def test_cancel_acceptance_does_not_immediately_report_cancelled(tmp_path: Path) -> None:
    """Cancel remains a request until the local execution loop observes and terminates it."""
    adapter, backend = _adapter(tmp_path)
    try:
        execution_id = str(adapter.submit(_request())["execution_id"])
        assert backend.started.wait(1.0)
        _wait_state(adapter, execution_id, "RUNNING")

        accepted = adapter.cancel({"execution_id": execution_id})
        assert accepted["accepted"] is True
        assert accepted["cancel_requested"] is True
        assert accepted["state"] == "RUNNING"

        _wait_state(adapter, execution_id, "CANCELLED")
        terminal = adapter.status({"execution_id": execution_id})
        assert terminal["state"] == "CANCELLED"
        assert (
            terminal["local_outcome"]
            == _outcome("CANCELLED", "cancelled after local stop", "any_targets|0").as_dict()
        )
    finally:
        backend.finish.set()
        adapter.close()


def test_duplicate_exact_invocation_reuses_one_local_handle(tmp_path: Path) -> None:
    """Repeated exact Execute calls cannot authorize a second local physical execution."""
    adapter, backend = _adapter(tmp_path)
    try:
        first = adapter.submit(_request())
        second = adapter.submit(_request())
        assert first["execution_id"] == second["execution_id"]
        assert backend.started.wait(1.0)
    finally:
        backend.finish.set()
        adapter.close()


def test_initialization_failure_closes_backend(tmp_path: Path) -> None:
    """Adapter construction closes partial backend state before reporting initialization failure."""
    backend = FailingBackend()
    with pytest.raises(IntegrationError, match="configured Habitat environment is unavailable"):
        HabitatLocalAdapter(
            ExecutionStore(tmp_path / "habitat.sqlite3"),
            lambda: backend,
            initialization_timeout_s=1.0,
        )
    assert backend.closed
