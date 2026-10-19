"""Closure flags follow actual publication outcomes, including failed physical execution."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.live_coordinator import LiveWorldCoordinator  # noqa: E402
from habitat_local_eaios.live_session import LiveSlotLedger  # noqa: E402
from habitat_local_eaios.task_verifier import build_task_verifier_source  # noqa: E402
from test_live_world import invocation  # noqa: E402
from test_task_verifier import _semantic  # noqa: E402


def coordinator(directory: Path) -> LiveWorldCoordinator:
    """Exercise real publication methods without starting a simulator or worker."""
    result = LiveWorldCoordinator.__new__(LiveWorldCoordinator)
    result._condition = threading.Condition()
    result._evidence_dir = directory
    result.registry = {"digest": "sha256:" + "a" * 64}
    result._ledger = LiveSlotLedger((0,))
    result._ledger.admit(0, invocation(0, ("actor",)))
    result._history = []
    result._finished = True
    result._retain_stopped_session = False
    result._closure_phase = "closed"
    result._closure_error = None
    result._execution_error = "execution:Stage2ContractViolation"
    result._state_archived = False
    result._closure_archived = False
    result._summary_published = False
    result._verifier_published = False
    result._verifier_required = False
    semantic = _semantic()
    (directory / "authoritative-semantic-evidence.json").write_text(json.dumps(semantic))
    (directory / "task-verifier-source.json").write_text(
        json.dumps(build_task_verifier_source(semantic))
    )
    return result


def test_failed_physical_execution_can_have_complete_negative_evidence(tmp_path: Path) -> None:
    """Actual negative official truth publishes normally despite an execution exception."""
    producer = coordinator(tmp_path)
    request = invocation(0, ("actor",))
    producer._publish_terminal(
        {},
        {"identity": {"simulator_steps": 2}, "official_metrics": {"pddl_success": False}},
        [request],
    )
    producer._write_state("failed")
    assert producer.closure()["evidence_flushed"] is False
    producer._archive_closure()
    status = producer.closure()
    assert status["evidence_flushed"] is True
    assert status["execution_error"] == "execution:Stage2ContractViolation"
    verdict = json.loads((tmp_path / "task-verifier-verdict.json").read_bytes())
    assert verdict["satisfied"] is False


def test_failed_terminal_write_never_sets_publication_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling the best-effort writers is insufficient to claim complete archival."""
    producer = coordinator(tmp_path)

    def fail_write(name: str, value: object) -> None:
        """Reject every publication while preserving the physical failure identity."""
        raise OSError("write unavailable")

    monkeypatch.setattr(producer, "_write_json", fail_write)
    producer._publish_terminal(
        {},
        {"identity": {}, "official_metrics": {"pddl_success": False}},
        [invocation(0, ("actor",))],
    )
    producer._write_state("failed")
    producer._archive_closure()
    assert producer.closure()["summary_published"] is False
    assert producer.closure()["verifier_published"] is False
    assert producer.closure()["evidence_flushed"] is False
    assert producer.closure()["execution_error"] == "execution:Stage2ContractViolation"


def test_exit_export_binds_complete_original_group_generations(tmp_path: Path) -> None:
    """The real exporter freezes all original worker invocations and returns defensive copies."""
    from dataclasses import replace
    from types import SimpleNamespace

    producer = coordinator(tmp_path)
    producer._process_exit_observation = None
    producer._ledger = LiveSlotLedger((0, 1))
    for agent in (0, 1):
        producer._ledger.admit(
            agent, replace(invocation(agent, ("left", "right")), execution_generation=agent + 4)
        )
    producer._world = SimpleNamespace(
        close_and_observe=lambda: {"instance_id": "test-original", "pid": 17, "exit_code": 0}
    )
    producer._close_process_evidence()
    proof = producer.process_exit_observation()
    assert proof is not None
    assert proof["group_id"] == "group"
    assert proof["attempts"] == [
        {"execution_id": "attempt-0", "execution_generation": 4},
        {"execution_id": "attempt-1", "execution_generation": 5},
    ]
    proof["pid"] = 999
    preserved = producer.process_exit_observation()
    assert preserved is not None and preserved["pid"] == 17
    assert json.loads((tmp_path / "worker-exit-observation.json").read_text())["pid"] == 17


@pytest.mark.parametrize("retained", [False, True])
def test_missing_generation_or_retained_worker_cannot_gain_exit_authority(
    tmp_path: Path, retained: bool
) -> None:
    """Missing original generation stays unavailable; retained sessions must not be stopped."""
    from types import SimpleNamespace

    producer = coordinator(tmp_path)
    producer._process_exit_observation = None
    producer._retain_stopped_session = retained
    calls: list[bool] = []

    def observe() -> dict[str, object]:
        """Count explicit fixture closes without inventing a missing attempt generation."""
        calls.append(True)
        return {"instance_id": "test-original", "pid": 17, "exit_code": 0}

    producer._world = SimpleNamespace(close_and_observe=observe)
    producer._close_process_evidence()
    assert producer.process_exit_observation() is None
    assert not (tmp_path / "worker-exit-observation.json").exists()
    assert calls == ([] if retained else [True])
