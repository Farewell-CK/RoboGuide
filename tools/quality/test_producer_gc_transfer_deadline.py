"""Check the producer GC handoff with virtual time and the actual response reader."""

from __future__ import annotations

import json
import os
import selectors
import signal
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import check_complete_producer_gc_processes as producer_gc
import check_network_content_processes as network_probe
import pytest
from check_network_content_processes import Actor

DEADLINE = 45.0
READY = {"epoch": 1, "quorum": True, "readiness": {"content_ready": True}}
CAUGHT_UP = {"caught_up": True, "progress": {"matched": True}}
RECONCILED = {"original_transfer_reconciled": True}


@contextmanager
def pipe_actor(
    member: int, replies: list[tuple[str, dict[str, Any]]]
) -> Iterator[tuple[Actor, Mock]]:
    """Feed small exact replies through a real pipe and capture every outbound write."""
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb") as writer:
        for action, result in replies:
            reply = {"pid": member, "action": action, "result": result}
            writer.write(json.dumps(reply).encode() + b"\n")
        writer.flush()
        process = Mock(pid=member, stdout=reader)
        process.poll.return_value = None
        process.wait.return_value = -signal.SIGKILL
        actor = Actor.__new__(Actor)
        actor.process = process
        actor.record = {"exchanges": []}
        actor.buffer = bytearray()
        yield actor, process


def writes(process: Mock) -> list[dict[str, Any]]:
    """Observe even requests whose replies are not retained as successful exchanges."""
    return [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]


@pytest.mark.parametrize("late_reply", [None, "status", "transfer-log-progress"])
def test_transfer_needs_timely_status_and_progress(
    monkeypatch: pytest.MonkeyPatch, late_reply: str | None
) -> None:
    """Allow exactly one on-time handoff and fence the pipe on either late observation."""
    clock = SimpleNamespace(monotonic=Mock(return_value=0.0))
    monkeypatch.setattr(producer_gc, "time", clock)
    monkeypatch.setattr(network_probe, "time", clock)

    def decode(raw: bytes) -> dict[str, Any]:
        """Advance to the exact cutoff while a valid reply is being decoded."""
        reply: dict[str, Any] = json.loads(raw)
        if reply["action"] == late_reply:
            clock.monotonic.return_value = DEADLINE
        return reply

    monkeypatch.setattr(network_probe, "json", SimpleNamespace(dumps=json.dumps, loads=decode))
    with ExitStack() as pipes:
        source, source_process = pipes.enter_context(
            pipe_actor(
                101,
                [
                    ("status", READY),
                    ("transfer-log-progress", CAUGHT_UP),
                    ("transfer-to", {}),
                    ("reconcile-transfer", RECONCILED),
                ],
            )
        )
        target, target_process = pipes.enter_context(
            pipe_actor(203, [("status", READY), ("reconcile-transfer", RECONCILED)])
        )
        actors = {101: source, 203: target}
        status = {"action": "status"}
        progress = {"action": "transfer-log-progress", "member": 203}
        if late_reply is None:
            assert producer_gc.transfer_leader(actors, 203, DEADLINE) == 203
            assert writes(source_process) == [
                status,
                progress,
                {
                    "action": "transfer-to",
                    "member": 203,
                    "transfer_id": "original-producer-gc-101-203",
                },
                {"action": "reconcile-transfer"},
            ]
            assert writes(target_process) == [status, {"action": "reconcile-transfer"}]
        else:
            with pytest.raises(ValueError, match="response deadline expired"):
                producer_gc.transfer_leader(actors, 203, DEADLINE)
            expected = [status] if late_reply == "status" else [status, progress]
            assert source.record["request_without_timely_response"] == expected[-1]
            with pytest.raises(ValueError, match="control pipe has an expired response"):
                source.ask(status)
            with pytest.raises(ValueError, match="infrastructure exit differs"):
                source.stop()
            assert source.record["forced_cleanup"] and not source.record["expected_native_kill"]
            source_process.kill.assert_called_once_with()
            assert writes(source_process) == expected
            assert writes(target_process) == []


@pytest.mark.parametrize(("deadline", "wait"), [(None, 120.0), (DEADLINE, DEADLINE)])
def test_response_wait_uses_caller_deadline_or_default(
    monkeypatch: pytest.MonkeyPatch, deadline: float | None, wait: float
) -> None:
    """Poll for at most the remaining budget even when the pipe never becomes readable."""
    monkeypatch.setattr(network_probe, "time", SimpleNamespace(monotonic=Mock(return_value=0.0)))
    selector = MagicMock()
    selector.__enter__.return_value.select.return_value = []
    monkeypatch.setattr(
        network_probe,
        "selectors",
        SimpleNamespace(
            DefaultSelector=Mock(return_value=selector), EVENT_READ=selectors.EVENT_READ
        ),
    )
    with pipe_actor(101, []) as (actor, process):
        request = {"action": "status"}
        with pytest.raises(ValueError, match="response deadline expired"):
            actor.ask(request, deadline=deadline)
        selector.__enter__.return_value.select.assert_called_once_with(wait)
        assert writes(process) == [request]
        assert actor.record["request_without_timely_response"] == request


def test_transfer_expires_before_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recheck the handoff cutoff after serializing, without fencing an unused pipe."""
    clock = SimpleNamespace(monotonic=Mock(return_value=0.0))
    monkeypatch.setattr(producer_gc, "time", clock)
    monkeypatch.setattr(network_probe, "time", clock)

    def encode(request: dict[str, Any]) -> str:
        """Expire only after both readonly observations but before writing the handoff."""
        if request["action"] == "transfer-to":
            clock.monotonic.return_value = DEADLINE
        return json.dumps(request)

    monkeypatch.setattr(network_probe, "json", SimpleNamespace(dumps=encode, loads=json.loads))
    with pipe_actor(101, [("status", READY), ("transfer-log-progress", CAUGHT_UP)]) as (
        source,
        process,
    ):
        with pytest.raises(ValueError, match="command deadline expired before sending"):
            producer_gc.transfer_leader({101: source}, 203, DEADLINE)
        assert writes(process) == [
            {"action": "status"},
            {"action": "transfer-log-progress", "member": 203},
        ]
        assert not source.record.get("response_deadline_expired")


def test_current_target_needs_no_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already-current target proceeds on timely leader status alone."""
    clock = SimpleNamespace(monotonic=Mock(return_value=0.0))
    monkeypatch.setattr(producer_gc, "time", clock)
    monkeypatch.setattr(network_probe, "time", clock)
    with pipe_actor(203, [("status", READY)]) as (target, process):
        assert producer_gc.transfer_leader({203: target}, 203, DEADLINE) == 203
        assert writes(process) == [{"action": "status"}]
