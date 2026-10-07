"""Check external-attempt incident policy and ordered shutdown without real model calls."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from roboguide_eval.e1_attempt import provider_incidents, terminate_owned_session
from roboguide_eval.e1_batch import _terminate_owned
from roboguide_eval.models import JSONObject


def observation(status: int | None, model: str | None = None) -> JSONObject:
    """Produce an observed transport record without asserting retry or task success."""
    return {"channel": "chat", "seq": 1, "status": status, "response_model": model}


def test_502_then_success_preserves_incident_without_stopping() -> None:
    """A declared 502 remains visible while the client's later successful response is admitted."""
    rows = [observation(502), observation(200, "model")]
    before = json.dumps(rows)
    issues = provider_incidents(
        rows, expected_model="model", continue_http_statuses=frozenset({502})
    )
    assert issues == [
        {
            "channel": "chat",
            "sequence": 1,
            "kind": "provider-http-or-transport",
            "status": 502,
            "requires_stop": False,
        }
    ]
    assert json.dumps(rows) == before


def test_exhausted_502_does_not_invent_a_successful_response() -> None:
    """Continuing the batch after an exhausted request never changes its HTTP observations."""
    rows = [observation(502) for _ in range(3)]
    issues = provider_incidents(
        rows, expected_model="model", continue_http_statuses=frozenset({502})
    )
    assert len(issues) == 3
    assert all(row["status"] == 502 and row["requires_stop"] is False for row in issues)
    assert all(row["response_model"] is None for row in rows)


@pytest.mark.parametrize("status", [None, 401, 403, 429, 500, 503])
def test_other_provider_failures_remain_explicit(status: int | None) -> None:
    """A declaration for 502 grants no exception for missing credentials or other faults."""
    issues = provider_incidents(
        [observation(status)], expected_model="model", continue_http_statuses=frozenset({502})
    )
    assert len(issues) == 1 and issues[0]["requires_stop"] is True


@pytest.mark.parametrize("model", [None, "wrong-model"])
def test_retry_with_wrong_or_missing_model_identity_still_stops(model: str | None) -> None:
    """A transient HTTP exception never relaxes the successful response identity fence."""
    issues = provider_incidents(
        [observation(502), observation(200, model)],
        expected_model="model",
        continue_http_statuses=frozenset({502}),
    )
    assert issues[-1]["kind"] == "model-identity-unconfirmed"
    assert issues[-1]["requires_stop"] is True


def test_502_requires_stop_without_an_explicit_policy() -> None:
    """A deployment must explicitly authorize continuing after an HTTP incident."""
    assert provider_incidents([observation(502)], expected_model="model")[0]["requires_stop"]


@pytest.mark.parametrize("statuses", [frozenset({200}), frozenset({600})])
def test_invalid_continuation_policy_fails_before_use(statuses: frozenset[int]) -> None:
    """Malformed policy cannot hide a successful response identity or invalid HTTP value."""
    with pytest.raises(ValueError, match="policy"):
        provider_incidents([], expected_model="model", continue_http_statuses=statuses)


def await_file(path: Path) -> None:
    """Wait boundedly for an owned process barrier rather than assuming startup timing."""
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), "owned process did not reach its startup barrier"


def test_owned_supervisor_keeps_child_alive_until_archival(tmp_path: Path) -> None:
    """The actual Bash EXIT collector sees its still-live service before cleanup."""
    source = Path("scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh").read_text()
    functions = source[source.index("finish_run() {") : source.index("\nwait_http() {")]
    fixture = r"""
set -euo pipefail
RUN="$1" REPO="$1" REQUEST_ID=request CONTROLLER_PORT=1234 MISSION_PORT=1235
FAILURE_OWNER=NONE FAILURE_COMPONENT="" FAILURE_REASON=""
sleep 30 &
PIDS=("$!") COMPONENTS=(controller)
sleep 30 &
AUX_PIDS=("$!")
uv() {
    kill -0 "${PIDS[0]}" || return 9
    printf '%s\n' "$@" > "$RUN/collected.txt"
}
"""
    fixture += functions
    fixture += r"""
trap finish_run EXIT
trap 'stop_run 143' TERM
trap 'stop_run 130' INT
printf '%s\n' "${PIDS[0]}" > "$RUN/ready"
wait "${AUX_PIDS[0]}"
"""
    child = subprocess.Popen(
        ["bash", "-s", "--", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert child.stdin is not None
        child.stdin.write(fixture.encode())
        child.stdin.close()
        await_file(tmp_path / "ready")
        _terminate_owned(child)
        assert child.returncode == 143
        arguments = (tmp_path / "collected.txt").read_text().splitlines()
        assert arguments[arguments.index("--failure-owner") + 1] == "EXTERNAL_INFRA"
        assert arguments[arguments.index("--component") + 1] == "harness"
        assert arguments[arguments.index("--reason") + 1] == "runner_interrupted"
    finally:
        if child.poll() is None:
            terminate_owned_session(child, grace_seconds=0.1)
        if child.stderr is not None:
            child.stderr.close()


def test_unresponsive_owned_session_has_bounded_escalation(tmp_path: Path) -> None:
    """An unresponsive supervisor is force-stopped without signalling unrelated processes."""
    source = (
        "import signal,time,sys; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", source, str(tmp_path / "ready")], start_new_session=True
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        await_file(tmp_path / "ready")
        assert terminate_owned_session(child, grace_seconds=0.1)
        assert child.returncode == -signal.SIGKILL
        assert unrelated.poll() is None
        assert terminate_owned_session(child, grace_seconds=0.1) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_signal_handler_preserves_ordinary_failure_and_archival_error(tmp_path: Path) -> None:
    """The real EXIT path preserves SUT exit codes and never hides a failed collector."""
    source = Path("scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh").read_text()
    functions = source[source.index("finish_run() {") : source.index("\nwait_http() {")]
    for collector_exit, expected_exit in ((0, 7), (9, 9)):
        fixture = (
            'RUN="$1" REPO="$1" REQUEST_ID=request CONTROLLER_PORT=1234 MISSION_PORT=1235\n'
            "FAILURE_OWNER=SUT_SYSTEM FAILURE_COMPONENT=node FAILURE_REASON=actual_failure\n"
            "PIDS=() AUX_PIDS=() COMPONENTS=()\n"
            f"uv() {{ return {collector_exit}; }}\n"
            + functions
            + "\ntrap finish_run EXIT\nexit 7\n"
        )
        result = subprocess.run(
            ["bash", "-s", "--", str(tmp_path)],
            input=fixture,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == expected_exit
