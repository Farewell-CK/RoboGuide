"""Deterministic tests for the shared-world coordinator and node endpoints."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.backend import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.shared_world import (  # noqa: E402
    InProcessWorldService,
    NodeEndpoint,
    SharedWorldCoordinator,
)
from habitat_local_eaios.store import ExecutionStore  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class StubRuntime:
    """Deterministic shared-world runtime double."""

    def __init__(self, pair_wait_outcome: str = "COMPLETED") -> None:
        """Record calls and preset the terminal outcome."""
        self.calls = 0
        self.pair_wait_outcome = pair_wait_outcome

    def initialize(self) -> None:
        """Accept thread-owning initialization without simulator work."""

    def is_ready(self) -> bool:
        """Report the stub as always ready."""
        return True

    def readiness_detail(self) -> str:
        """Describe the stub world."""
        return "stub shared world"

    def final_metrics(self) -> dict[str, object]:
        """Return official benchmark metrics."""
        return {"pddl_success": True}

    def execute_pair(
        self,
        invocations: dict[int, Any],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, object]]:
        """Run one fake shared episode for both committed assignments."""
        self.calls += 1
        for agent_id in invocations:
            running(agent_id, "stub running")
        return (
            {
                agent_id: LocalExecutionOutcome(
                    state=self.pair_wait_outcome,
                    detail="stub",
                    episode_id="51",
                    scene_id="scene",
                    destination=invocation.destination,
                    simulator_steps=10,
                    initial_position=(0.0, 0.0, 0.0),
                    final_position=(1.0, 0.0, 0.0),
                    local_skill_completed=True,
                    benchmark_task_achieved=True,
                    local_llm_calls=1,
                    terminal_basis="stub",
                )
                for agent_id, invocation in invocations.items()
            },
            {"identity": {"simulator_worlds": 1, "episode_reset_count": 1}},
        )


def _execution(store: ExecutionStore, execution_id: str) -> dict[str, object]:
    """Narrow one store read to a present execution for assertions."""
    execution = store.get(execution_id)
    assert execution is not None
    return dict(execution)


def _request(mission: str, destination: str, task: str) -> dict[str, object]:
    """Build one canonical workflow request."""
    return {
        "invocation": {
            "mission_id": mission,
            "task_id": task,
            "group_id": "group",
            "role_id": "role",
            "operation": "mobility.navigate@v1",
            "objective": "objective",
            "parameters": {"destination": destination},
            "resource_ids": ["slot"],
        }
    }


def _world(
    tmp_path: Path, pair_wait: float = 5.0
) -> tuple[Any, Any, NodeEndpoint, NodeEndpoint, Path]:
    """Create one coordinator with two endpoints over stub runtime."""
    runtime = StubRuntime()
    evidence = tmp_path / "evidence"
    coordinator = SharedWorldCoordinator(InProcessWorldService(runtime), pair_wait, evidence)
    endpoint_a = NodeEndpoint("node-a", 0, ExecutionStore(tmp_path / "a.sqlite3"), coordinator)
    endpoint_b = NodeEndpoint("node-b", 1, ExecutionStore(tmp_path / "b.sqlite3"), coordinator)
    return runtime, coordinator, endpoint_a, endpoint_b, evidence


def test_lone_assignment_waits_then_fails_closed(tmp_path: Path) -> None:
    """A sibling-less assignment must fail closed without a shared episode."""
    runtime, _, endpoint_a, _, evidence = _world(tmp_path, pair_wait=1.0)
    response = endpoint_a.submit(_request("m", "any_targets|0", "t"))
    time.sleep(2.0)
    execution = _execution(endpoint_a.store(), str(response["execution_id"]))
    assert execution is not None
    assert str(execution["state"]) == "FAILED"
    assert "pair never assembled" in str(execution["detail"])
    assert runtime.calls == 0
    assert not (evidence / "shared-world-summary.json").exists()


def test_pair_runs_one_episode_with_two_handles(tmp_path: Path) -> None:
    """Both assignments must share exactly one episode with distinct handles."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    handle_a = endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    time.sleep(0.5)
    assert _execution(endpoint_a.store(), str(handle_a["execution_id"]))["state"] == "ACCEPTED", (
        "pre-pair assignment must not start alone"
    )
    handle_b = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(80):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            break
        time.sleep(0.1)
    assert state_a["state"] == "COMPLETED"
    assert state_b["state"] == "COMPLETED"
    assert handle_a["execution_id"] != handle_b["execution_id"]
    assert runtime.calls == 1
    summary = json.loads((evidence / "shared-world-summary.json").read_text(encoding="utf-8"))
    assert summary["identity"]["episode_reset_count"] == 1
    assert summary["identity"]["simulator_worlds"] == 1
    assert summary["official_pddl_success"] is True


def test_duplicate_assignment_is_idempotent(tmp_path: Path) -> None:
    """Duplicate accept and dispatch reuse one handle and never re-run."""
    runtime, _, endpoint_a, endpoint_b, _ = _world(tmp_path)
    request = _request("m", "any_targets|0", "ta")
    handle = endpoint_a.submit(request)
    endpoint_a.dispatch(str(handle["execution_id"]))
    duplicate = endpoint_a.accept(request)
    assert duplicate["execution_id"] == handle["execution_id"]
    endpoint_b.submit(_request("m2", "TARGET_any_targets|0", "tb"))
    for _ in range(80):
        execution = _execution(endpoint_a.store(), str(handle["execution_id"]))
        if str(execution["state"]) in TERMINAL:
            break
        time.sleep(0.1)
    assert str(execution["state"]) == "COMPLETED"
    assert runtime.calls == 1
    repeat = endpoint_a.accept(request)
    assert repeat["execution_id"] == handle["execution_id"]
    time.sleep(0.3)
    assert runtime.calls == 1


def test_new_invocation_after_consumed_episode_rejected(tmp_path: Path) -> None:
    """A fresh third assignment cannot fabricate a second shared episode."""
    runtime, _, endpoint_a, endpoint_b, _ = _world(tmp_path)
    endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    handle_b = endpoint_b.submit(_request("m2", "TARGET_any_targets|0", "tb"))
    for _ in range(80):
        execution = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if str(execution["state"]) in TERMINAL:
            break
        time.sleep(0.1)
    with pytest.raises(IntegrationError, match="consumed"):
        endpoint_a.submit(_request("m3", "other|0", "tc"))
    assert runtime.calls == 1


def test_conflicting_active_assignment_rejected(tmp_path: Path) -> None:
    """One endpoint refuses a second distinct active execution."""
    _, _, endpoint_a, _, _ = _world(tmp_path)
    endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    with pytest.raises(IntegrationError, match="another active execution"):
        endpoint_a.submit(_request("m-different", "elsewhere|0", "tb"))
