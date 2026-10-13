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
