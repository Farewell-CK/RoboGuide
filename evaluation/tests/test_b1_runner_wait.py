"""Deterministic tests for the state-driven B1 MI lifecycle wait."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.b1_runner_wait import (
    WaitConfigurationError,
    derive_wait_budget,
    wait_for_lifecycle,
)

RUNNER = Path("scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh")


class FakeClock:
    """Deterministic monotonic clock advanced explicitly by sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        """Begin at one arbitrary monotonic origin."""
        self.now = start

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance fake time instead of blocking the test."""
        self.now += seconds


def _poll_from(
    script: list[tuple[str | None, str | None]] | list[tuple[str, None]] | list[tuple[None, str]],
) -> Callable[[str, str, float], tuple[str | None, str | None]]:
    """Build a scripted poll function repeating the final scripted result."""
    _tail = script[-1]

    def poll(endpoint: str, request_id: str, timeout: float) -> tuple[str | None, str | None]:
        """Return the next scripted (lifecycle, error) pair."""
        del endpoint, request_id, timeout
        if script:
            return script.pop(0)
        return _tail

    return poll


def _wait(
    script: (list[tuple[str | None, str | None]] | list[tuple[str, None]] | list[tuple[None, str]]),
    *,
    budget: float = 5750.0,
    pid: int = 0,
    log_path: Path | None = None,
) -> Any:
    """Run one wait over scripted polls and a deterministic clock."""
    clock = FakeClock()
    return wait_for_lifecycle(
        endpoint="http://unused",
        request_id="request-x",
        budget_seconds=budget,
        deadline_clock=clock,
        poll=_poll_from(script),
        pid=pid,
        poll_interval_seconds=2.0,
        log_path=log_path,
        sleep=clock.sleep,
    )


def test_late_acceptance_after_old_480s_bound_is_still_waited(tmp_path: Path) -> None:
    """MI accepting after the former 480s bound still succeeds (case 1)."""
    script: list[tuple[str | None, str | None]] = [("Reviewing", None)] * 300
    script += [("Accepted", None)]
    result = _wait(script, log_path=tmp_path / "log.jsonl")
    assert result.outcome == "accepted"
    assert result.lifecycle == "Accepted"


def test_in_budget_failure_returns_mi_terminal(tmp_path: Path) -> None:
    """A real MI failure inside the budget is archived as itself (case 2)."""
    script: list[tuple[str | None, str | None]] = [
        ("Interpreting", None),
        ("Failed", None),
    ]
    result = _wait(script, log_path=tmp_path / "log.jsonl")
    assert result.outcome == "mi_terminal"
    assert result.lifecycle == "Failed"


def test_long_regeneration_sequence_then_accepted(tmp_path: Path) -> None:
    """Many pre-validation regenerations still reach acceptance (case 3)."""
    script = [("Interpreting", None), ("Drafted", None), ("Reviewing", None)] * 8
    script += [("Accepted", None)]
    result = _wait(script, log_path=tmp_path / "log.jsonl")
    assert result.outcome == "accepted"


@pytest.mark.parametrize("lifecycle", ["NeedsClarification", "AwaitingApproval"])
def test_interaction_states_wait_and_return_without_action(tmp_path: Path, lifecycle: str) -> None:
    """Interaction-required states return as stable outcomes (case 4)."""
    script: list[tuple[str | None, str | None]] = [
        ("Reviewing", None),
        (lifecycle, None),
    ]
    result = _wait(script, log_path=tmp_path / "log.jsonl")
    assert result.outcome == "awaiting_interaction"
    assert result.lifecycle == lifecycle


def test_deadline_race_acceptance_resolved_by_final_query(tmp_path: Path) -> None:
    """Acceptance observed by the bounded final query still counts (case 5)."""
    # The single scripted poll is consumed by the post-deadline final query.
    script = [("Accepted", None)]
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=0.0,
        deadline_clock=clock,
        poll=_poll_from(script),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
    )
    assert result.outcome == "accepted"
    assert result.first_timeout_at is not None


def test_budget_exhaustion_reports_observation_timeout(tmp_path: Path) -> None:
    """Non-terminal at the deadline yields a distinct timeout outcome (case 6)."""
    script: list[tuple[str | None, str | None]] = [("Reviewing", None)]
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=0.0,
        deadline_clock=clock,
        poll=_poll_from(script),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
    )
    assert result.outcome == "observation_timeout"
    assert result.lifecycle == "Reviewing"
    assert result.first_timeout_at is not None


def test_service_process_exit_is_distinct_from_timeout(tmp_path: Path) -> None:
    """A dead MI process reports service_exited, not timeout (case 7)."""
    import subprocess
    import time as real_time

    child = subprocess.Popen(["/bin/true"])
    real_time.sleep(0.3)  # exit completes; the unreaped child stays a zombie
    result = _wait([("Reviewing", None)], pid=child.pid, log_path=tmp_path / "log.jsonl")
    child.wait()
    assert result.outcome == "service_exited"


def test_transient_http_failures_do_not_fail_the_wait(tmp_path: Path) -> None:
    """Intermittent HTTP errors are retried, not judged MI-failed (case 8)."""
    script = [
        (None, "URLError: refused"),
        (None, "URLError: refused"),
        ("Drafted", None),
        ("Accepted", None),
    ]
    result = _wait(script, log_path=tmp_path / "log.jsonl")
    assert result.outcome == "accepted"
    assert result.last_error == "URLError: refused"


def test_persistent_http_failure_ends_bounded_as_observation_timeout(
    tmp_path: Path,
) -> None:
    """Unreachable MI with a live process ends at the deadline (case 9)."""
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=4.0,
        deadline_clock=clock,
        poll=_poll_from([(None, "URLError: refused")]),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
    )
    assert result.outcome == "observation_timeout"
    assert result.lifecycle == "unknown"
    assert result.last_error == "URLError: refused"


@pytest.mark.parametrize("lifecycle", ["Exploding", "terminé", "Accepted "])
def test_unknown_lifecycle_fails_closed(lifecycle: str, tmp_path: Path) -> None:
    """Unknown lifecycle values are invalid responses, never passes (case 10)."""
    result = _wait([(lifecycle, None)], log_path=tmp_path / "log.jsonl")
    assert result.outcome == "invalid_response"
    assert result.lifecycle == lifecycle


def test_observation_timeout_failure_is_scoped_external_infra() -> None:
    """The timeout attribution stays distinct from MI failure (case 11)."""
    from roboguide_eval.b1_run import scoped_run_failure

    record: dict[str, object] = {
        "request_id": "request-x",
        "mission_id": "m",
        "group_id": None,
        "lifecycle": "Reviewing",
        "issues": [],
    }
    failure = scoped_run_failure(
        Path("run-x"),
        {
            "schema_version": "roboguide.e1.run-failure/v0.1",
            "run_id": "run-x",
            "failure_owner": "EXTERNAL_INFRA",
            "component": "harness",
            "reason": "runner_mi_observation_timeout",
            "request_id": "request-x",
            "mission_id": "m",
            "group_id": "",
            "input_digest": None,
            "observation_source": "scenario_process_boundary",
        },
        record,
    )
    assert failure.get("reason") == "runner_mi_observation_timeout"


def test_mission_wait_budget_is_separate_from_mi_budget() -> None:
    """Accepted keeps its own mission wait, not the MI budget (case 12)."""
    script = RUNNER.read_text(encoding="utf-8")
    accepted_branch = script.index("accepted)")
    assert script.index("wait_mission_terminal", accepted_branch) > accepted_branch
    assert "1800" in script[accepted_branch : accepted_branch + 400]
    # The observation budget (POST + polling) is distinct from the mission
    # execution wait, and the MI worst-case derivation is archived, not used.
    assert script.count("MI_OBSERVATION_BUDGET_SECONDS") >= 2
    assert "MI_WAIT_BUDGET_SECONDS" in script  # derivation archived only


def test_budget_follows_the_actual_run_configuration(tmp_path: Path) -> None:
    """Derived budgets track config changes, not stale constants (case 13)."""
    mission = tmp_path / "mission.toml"
    service = tmp_path / "service.toml"
    mission.write_text(
        "[mission]\nprevalidation_recovery_attempts = 2\nmax_repair_attempts = 2\n"
        "[mission.llm]\ntimeout_seconds = 600\n",
        encoding="utf-8",
    )
    service.write_text(
        "[service]\ngrounding_timeout_seconds = 5\ngrounding_acquisition_attempts = 2\n"
        "controller_timeout_seconds = 30\n",
        encoding="utf-8",
    )
    baseline = derive_wait_budget(mission, service)
    assert baseline.total_seconds == pytest.approx(5750.0)
    mission.write_text(
        "[mission]\nprevalidation_recovery_attempts = 0\nmax_repair_attempts = 0\n"
        "[mission.llm]\ntimeout_seconds = 120\n",
        encoding="utf-8",
    )
    changed = derive_wait_budget(mission, service)
    assert changed.total_seconds == pytest.approx(120 + 120 + 120 + 20 + 30 + 300)
    with pytest.raises(WaitConfigurationError):
        derive_wait_budget(tmp_path / "missing.toml", service)


def test_exit_collector_runs_before_process_cleanup() -> None:
    """Collection order and outcome evidence precede any kill (case 14)."""
    script = RUNNER.read_text(encoding="utf-8")
    collector_index = script.index("roboguide_eval.b1_artifacts")
    kill_index = script.index('for pid in "${PIDS[@]}"; do kill "$pid"')
    assert collector_index < kill_index
    # The wait call writes its outcome evidence inside the script body,
    # before the EXIT trap can fire; the trap itself collects first, kills after.
    wait_call = script.index('--request-id "$REQUEST_ID"')
    outcome_write = script.index("mi-wait-outcome.json")
    assert wait_call < outcome_write


def test_poll_log_is_bounded_and_complete(tmp_path: Path) -> None:
    """Poll evidence rows are streamed, bounded, and counted honestly."""
    log = tmp_path / "log.jsonl"
    script: list[tuple[str | None, str | None]] = [
        ("Interpreting", None),
        ("Drafted", None),
        ("Accepted", None),
    ]
    result = _wait(script, log_path=log)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == result.records_logged
    first = json.loads(lines[0])
    assert first["request_id"] == "request-x"
    assert first["http_ok"] is True
    assert first["service_alive"] is True
    assert first["remaining_s"] > 0


def test_stalled_lifecycle_terminates_before_total_budget(tmp_path: Path) -> None:
    """A wedged provider call ends at the stall bound, not 5750s (drip)."""
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=5750.0,
        deadline_clock=clock,
        poll=_poll_from([("Interpreting", None)]),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
        stall_seconds=1200.0,
    )
    assert result.outcome == "stalled_no_progress"
    assert result.lifecycle == "Interpreting"
    # The wait ended near the stall bound, far before the total budget.
    assert clock.now < 1000.0 + 1300.0


def test_stall_resets_on_lifecycle_transitions(tmp_path: Path) -> None:
    """Slow but progressing deliberation never trips the stall bound."""
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=5750.0,
        deadline_clock=clock,
        poll=_poll_from(
            [("Interpreting", None)] * 500
            + [("Drafted", None)] * 500
            + [("Reviewing", None)] * 500
            + [("Accepted", None)]
        ),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
        stall_seconds=1200.0,
    )
    assert result.outcome == "accepted"


def test_stall_bound_derives_from_actual_config(tmp_path: Path) -> None:
    """The stall bound tracks the configured per-call ceiling."""
    mission = tmp_path / "mission.toml"
    service = tmp_path / "service.toml"
    mission.write_text(
        "[mission]\nprevalidation_recovery_attempts = 2\nmax_repair_attempts = 2\n"
        "[mission.llm]\ntimeout_seconds = 600\n",
        encoding="utf-8",
    )
    service.write_text(
        "[service]\ngrounding_timeout_seconds = 5\ngrounding_acquisition_attempts = 2\n"
        "controller_timeout_seconds = 30\n",
        encoding="utf-8",
    )
    budget = derive_wait_budget(mission, service)
    assert budget.stall_seconds == pytest.approx(1200.0)
    mission.write_text(
        "[mission]\nprevalidation_recovery_attempts = 2\nmax_repair_attempts = 2\n"
        "[mission.llm]\ntimeout_seconds = 120\n",
        encoding="utf-8",
    )
    assert derive_wait_budget(mission, service).stall_seconds == pytest.approx(240.0)


def _submit(
    *,
    post_result: tuple[dict[str, object] | None, str | None],
    post_calls: list[float] | None = None,
    poll_script: list[tuple[str | None, str | None]] | None = None,
    recovered_id: str | None = None,
    budget: float = 1800.0,
    pid: int = 0,
    stall: float | None = None,
) -> Any:
    """Run submit_and_wait with scripted boundaries and a fake clock."""
    from roboguide_eval.b1_runner_wait import submit_and_wait

    clock = FakeClock()
    poll_calls: list[str] = []

    def post(
        endpoint: str, instruction: str, timeout: float
    ) -> tuple[dict[str, object] | None, str | None]:
        """Record the single submit call and replay its scripted result."""
        if post_calls is not None:
            post_calls.append(timeout)
        clock.sleep(1.0)  # the POST consumes one fake second minimum
        return post_result

    def poll(endpoint: str, request_id: str, timeout: float) -> tuple[str | None, str | None]:
        """Record polls and replay the scripted tail."""
        poll_calls.append(request_id)
        return (poll_script or [("Reviewing", None)])[-1]

    return (
        submit_and_wait(
            endpoint="http://unused",
            instruction="do the task",
            budget_seconds=budget,
            store_path=Path("unused.sqlite3"),
            pid=pid,
            deadline_clock=clock,
            poll=poll,
            post=post,
            recover=lambda store, instruction: recovered_id,
            sleep=clock.sleep,
            stall_seconds=stall,
        ),
        poll_calls,
    )


def test_post_returning_accepted_never_polls() -> None:
    """A synchronous Accepted response skips polling entirely (case 2)."""
    result, polls = _submit(post_result=({"request_id": "r1", "lifecycle": "Accepted"}, None))
    assert result.outcome == "accepted"
    assert polls == []


def test_post_returning_failed_archives_immediately() -> None:
    """A synchronous Failed response is MI's own terminal state (case 3)."""
    result, polls = _submit(post_result=({"request_id": "r1", "lifecycle": "Failed"}, None))
    assert result.outcome == "mi_terminal"
    assert result.lifecycle == "Failed"
    assert polls == []


def test_post_nonterminal_polls_only_remaining_budget() -> None:
    """Polling after a slow POST uses the unspent budget share (cases 1, 4)."""
    result, polls = _submit(
        post_result=({"request_id": "r1", "lifecycle": "Reviewing"}, None),
        poll_script=[("Reviewing", None), ("Accepted", None)],
        budget=10.0,
    )
    assert result.outcome == "accepted"
    assert polls  # polling genuinely happened for the non-terminal response


def test_post_timeout_recovers_identity_and_continues() -> None:
    """Client POST timeout with a persisted identity keeps observing (cases 1, 5)."""
    result, polls = _submit(
        post_result=(None, "post_timeout"),
        poll_script=[("Interpreting", None), ("Accepted", None)],
        recovered_id="r-store",
        budget=60.0,
    )
    assert result.outcome == "accepted"
    assert result.request_id == "r-store"
    assert polls  # no resubmission: polling proceeds by recovered identity only


def test_post_timeout_without_identity_archives_unavailable() -> None:
    """No trustworthy identity means honest unavailability (case 6)."""
    result, polls = _submit(
        post_result=(None, "post_timeout"),
        recovered_id=None,
        budget=60.0,
    )
    assert result.outcome == "request_id_unavailable"
    assert result.request_id == ""
    assert polls == []


def test_post_timeout_recovery_after_deadline_is_timeout() -> None:
    """Identity recovered only after the budget ends is a timeout (case 4)."""
    result, _ = _submit(
        post_result=(None, "post_timeout"),
        recovered_id="r-late",
        budget=0.5,  # the fake POST consumes 1.0s, exceeding the budget
    )
    assert result.outcome == "observation_timeout"
    assert result.request_id == "r-late"


def test_post_transport_error_with_dead_service() -> None:
    """Connection failure with an exited process attributes service exit (case 7)."""
    import subprocess
    import time as real_time

    child = subprocess.Popen(["/bin/true"])
    real_time.sleep(0.3)
    result, polls = _submit(post_result=(None, "transport"), pid=child.pid)
    child.wait()
    assert result.outcome == "service_exited"
    assert polls == []


def test_post_transport_error_with_live_service() -> None:
    """Connection failure with a live process attributes HTTP unusable (case 8)."""
    result, _ = _submit(post_result=(None, "transport"))
    assert result.outcome == "http_unusable"
    assert "transport" in (result.last_error or "")


def test_post_malformed_response_fails_closed() -> None:
    """A non-JSON or non-object response is unusable, never a state (case 8)."""
    result, _ = _submit(post_result=(None, "invalid_response"))
    assert result.outcome == "http_unusable"


@pytest.mark.parametrize("post_error", ["transport", "invalid_response"])
def test_ambiguous_post_failure_recovers_single_persisted_identity(post_error: str) -> None:
    """A disconnected POST may still have created a durable request."""
    calls: list[float] = []
    result, polls = _submit(
        post_result=(None, post_error),
        post_calls=calls,
        recovered_id="request-durable",
        poll_script=[("Accepted", None)],
    )
    assert len(calls) == 1  # Never create a duplicate request.
    assert result.request_id == "request-durable"
    assert result.outcome == "accepted"
    assert polls == ["request-durable"]


def test_store_recovery_binds_exact_instruction(tmp_path: Path) -> None:
    """Recovery reads the real MissionRequestStore envelope, not a flat fake row."""
    import sqlite3

    from roboguide_eval.b1_runner_wait import recover_request_id_from_store

    store = tmp_path / "mission-service.sqlite3"
    connection = sqlite3.connect(store)
    connection.execute(
        "CREATE TABLE mission_requests(request_id TEXT, mission_id TEXT, "
        "document_json TEXT, updated_at_ms INTEGER)"
    )
    request = {
        "request_id": "request-ours",
        "mission_id": "mission-ours",
        "dialogue": [{"content": "do the task"}],
    }
    envelope = {
        "schema_version": "roboguide.mission-request-storage/v0.1",
        "request": request,
        "observations": {"request_id": "request-ours"},
    }
    connection.execute(
        "INSERT INTO mission_requests VALUES (?,?,?,?)",
        ("request-ours", "mission-ours", json.dumps(envelope), 1),
    )
    connection.commit()
    connection.close()
    assert recover_request_id_from_store(store, "do the task") == "request-ours"
    assert recover_request_id_from_store(store, "unsubmitted text") is None
    assert recover_request_id_from_store(store, "  do the task  ") == "request-ours"


def test_store_recovery_rejects_ambiguous_requests(tmp_path: Path) -> None:
    """Multiple rows cannot be attributed to one submission by instruction alone."""
    import sqlite3

    from roboguide_eval.b1_runner_wait import recover_request_id_from_store

    store = tmp_path / "mission-service.sqlite3"
    connection = sqlite3.connect(store)
    connection.execute(
        "CREATE TABLE mission_requests(request_id TEXT, mission_id TEXT, "
        "document_json TEXT, updated_at_ms INTEGER)"
    )
    for request_id in ("r1", "r2"):
        request = {"request_id": request_id, "dialogue": [{"content": "do the task"}]}
        envelope = {
            "schema_version": "roboguide.mission-request-storage/v0.1",
            "request": request,
            "observations": {},
        }
        connection.execute(
            "INSERT INTO mission_requests VALUES (?,?,?,?)",
            (request_id, f"m-{request_id}", json.dumps(envelope), 1),
        )
    connection.commit()
    connection.close()
    assert recover_request_id_from_store(store, "do the task") is None


def test_poll_failure_http_ok_is_false_in_log(tmp_path: Path) -> None:
    """A failed poll records http_ok=false even with a cached lifecycle (case 9)."""
    clock = FakeClock()
    result = wait_for_lifecycle(
        endpoint="http://unused",
        request_id="r",
        budget_seconds=6.0,
        deadline_clock=clock,
        poll=_poll_from([("Drafted", None), (None, "URLError: x"), ("Accepted", None)]),
        sleep=clock.sleep,
        log_path=tmp_path / "log.jsonl",
    )
    assert result.outcome == "accepted"
    rows = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [row["http_ok"] for row in rows] == [True, False, True]


def test_observation_fault_never_fakes_mi_failure() -> None:
    """The unavailable-identity attribution stays external (case 11)."""
    from roboguide_eval.b1_run import scoped_run_failure

    failure = scoped_run_failure(
        Path("run-x"),
        {
            "schema_version": "roboguide.e1.run-failure/v0.1",
            "run_id": "run-x",
            "failure_owner": "EXTERNAL_INFRA",
            "component": "harness",
            "reason": "runner_post_observation_timeout_request_id_unavailable",
            "request_id": "",
            "mission_id": "",
            "group_id": "",
            "input_digest": None,
            "observation_source": "scenario_process_boundary",
        },
        {},
    )
    assert failure.get("reason") == "runner_post_observation_timeout_request_id_unavailable"
