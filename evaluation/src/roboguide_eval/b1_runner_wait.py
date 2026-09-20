"""State-driven MI lifecycle waiting for the B1 scenario runner.

Mission Service's ``POST /v1/mission-requests`` is synchronous: the
response returns only after ``engine.create()`` finishes the whole
deliberation chain. The runner therefore faces two distinct windows:

1. the POST window (no request id yet) — bounded by the same frozen
   observation budget, with the Mission Service's own request store as
   the only trustworthy source for recovering this run's identity when
   the client budget expires while the server is still executing; and
2. the post-response window — the response already carries a stable
   lifecycle; only genuinely non-terminal states need further polling,
   funded by whatever budget the POST did not consume.

The budget is one deployment-chosen observation boundary for the run
(default 1800s, overridable via ``ROBOGUIDE_B1_MI_OBSERVATION_BUDGET_SECONDS``),
frozen before submission and persisted as evidence. It is deliberately
NOT the MI theoretical worst-case chain (that derivation remains
available via ``--budget-only`` for deployment reasoning); the historic
sample of completed E1 MI chains is minutes-scale (two observations,
insufficient for percentiles), so the boundary is a conservative
experiment-scoped choice, not a claim about MI execution time.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any

WAIT_OUTCOME_SCHEMA = "roboguide.e1.mi-wait-outcome/v0.1"
BUDGET_SCHEMA = "roboguide.e1.mi-wait-budget/v0.1"

TERMINAL_LIFECYCLES = frozenset({"Accepted", "Blocked", "Failed", "Cancelled"})
INTERACTION_LIFECYCLES = frozenset({"NeedsClarification", "AwaitingApproval"})
KNOWN_LIFECYCLES = (
    TERMINAL_LIFECYCLES
    | INTERACTION_LIFECYCLES
    | frozenset({"Received", "Interpreting", "Drafted", "Reviewing", "Repairing", "Submitting"})
)

POLL_INTERVAL_SECONDS = 2.0
FINAL_QUERY_TIMEOUT_SECONDS = 5.0
MAX_LOG_RECORDS = 5000
SCHEDULING_MARGIN_SECONDS = 300.0


class WaitConfigurationError(ValueError):
    """Report unusable mission configuration for budget derivation."""


@dataclass(frozen=True, slots=True)
class WaitBudget:
    """One frozen MI observation budget with its derivation evidence.

    Attributes:
        total_seconds: The monotonic deadline budget for one pass.
        stall_seconds: The no-progress bound: any lifecycle state held
            longer than this without a transition means MI is stuck in
            one provider call beyond its configured per-call ceiling
            (urllib timeouts are socket-level, so a slow-drip provider
            has no wall-clock cap of its own).
        components: The per-stage contributions actually used.
        sources: The configuration files the numbers came from.
    """

    total_seconds: float
    stall_seconds: float
    components: dict[str, float]
    sources: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        """Serialize the frozen budget evidence document."""
        return {
            "schema_version": BUDGET_SCHEMA,
            "total_seconds": self.total_seconds,
            "stall_seconds": self.stall_seconds,
            "components": self.components,
            "sources": self.sources,
            "derivation": (
                "grounding 2*attempts*timeout + interpreter timeout + "
                "planner (1+prevalidation)*timeout + reviewer (max_repair+1)*timeout "
                "+ repairer max_repair*timeout + controller timeout + margin"
            ),
        }


def derive_wait_budget(mission_config_path: Path, service_config_path: Path) -> WaitBudget:
    """Derive the MI observation budget from this run's actual configs.

    Raises:
        WaitConfigurationError: When required fields are missing or the
            files cannot be parsed; the runner then fails closed before
            launching Mission Intelligence.
    """
    try:
        mission_document = _load_toml(mission_config_path)
        service = _load_toml(service_config_path)
    except (OSError, ValueError) as error:
        raise WaitConfigurationError(f"cannot read MI configuration: {error}") from error
    mission = _section(mission_document, "mission", mission_config_path)
    llm = _section(mission, "llm", mission_config_path)
    timeout = _positive(llm, "timeout_seconds", "mission.llm.timeout_seconds")
    prevalidation = _nonnegative_int(
        mission, "prevalidation_recovery_attempts", "mission.prevalidation_recovery_attempts"
    )
    max_repair = _nonnegative_int(mission, "max_repair_attempts", "mission.max_repair_attempts")
    svc = _section(service, "service", service_config_path)
    grounding_timeout = _positive(
        svc, "grounding_timeout_seconds", "service.grounding_timeout_seconds"
    )
    grounding_attempts = _nonnegative_int(
        svc, "grounding_acquisition_attempts", "service.grounding_acquisition_attempts"
    )
    controller_timeout = _positive(
        svc, "controller_timeout_seconds", "service.controller_timeout_seconds"
    )
    components = {
        "grounding_capture": 2 * grounding_attempts * grounding_timeout,
        "interpreter": timeout,
        "planner_with_prevalidation": (1 + prevalidation) * timeout,
        "reviewer": (max_repair + 1) * timeout,
        "repairer": max_repair * timeout,
        "controller_submission": controller_timeout,
        "scheduling_margin": SCHEDULING_MARGIN_SECONDS,
    }
    # A slow-drip provider can hold one socket-level call open far past
    # timeout_seconds; two nominal call ceilings bound any legitimate gap
    # between observable lifecycle transitions.
    stall_seconds = 2.0 * timeout
    return WaitBudget(
        total_seconds=sum(components.values()),
        stall_seconds=stall_seconds,
        components=components,
        sources={
            "mission_config": str(mission_config_path),
            "service_config": str(service_config_path),
        },
    )


def _load_toml(path: Path) -> dict[str, Any]:
    """Parse one TOML document into a plain mapping."""
    with path.open("rb") as source:
        return tomllib.load(source)


def _section(document: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    """Require one TOML table.

    Raises:
        WaitConfigurationError: When the table is absent.
    """
    value = document.get(name)
    if not isinstance(value, dict):
        raise WaitConfigurationError(f"{path} lacks the [{name}] table")
    return value


def _positive(section: dict[str, Any], key: str, label: str) -> float:
    """Require one positive numeric field.

    Raises:
        WaitConfigurationError: When missing or not positive.
    """
    value = section.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise WaitConfigurationError(f"{label} must be a positive number")
    return float(value)


def _nonnegative_int(document: dict[str, Any], key: str, label: str) -> int:
    """Require one nonnegative integer field.

    Raises:
        WaitConfigurationError: When missing or negative.
    """
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WaitConfigurationError(f"{label} must be a nonnegative integer")
    return value


@dataclass(frozen=True, slots=True)
class WaitResult:
    """The terminal observation outcome for one MI wait.

    Attributes:
        outcome: One of ``accepted``, ``mi_terminal``, ``awaiting_interaction``,
            ``observation_timeout``, ``service_exited``, ``invalid_response``.
        lifecycle: The last observed lifecycle value (raw when unknown).
        request_id: The awaited request identity.
        last_error: The last HTTP error description, when any.
        records_logged: Poll evidence rows actually persisted.
        records_dropped: Poll rows skipped by the log bound.
        first_timeout_at: Monotonic timestamp when the deadline was first
            observed passed (only for ``observation_timeout``).
    """

    outcome: str
    lifecycle: str
    request_id: str
    last_error: str | None = None
    records_logged: int = 0
    records_dropped: int = 0
    first_timeout_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialize the outcome for scenario attribution and evidence."""
        document: dict[str, Any] = {
            "schema_version": WAIT_OUTCOME_SCHEMA,
            "outcome": self.outcome,
            "lifecycle": self.lifecycle,
            "request_id": self.request_id,
            "last_error": self.last_error,
            "records_logged": self.records_logged,
            "records_dropped": self.records_dropped,
        }
        if self.first_timeout_at is not None:
            document["first_timeout_at_monotonic"] = self.first_timeout_at
        return document


def _classify(lifecycle: str) -> str | None:
    """Map one lifecycle value to its wait outcome, or None to keep waiting."""
    if lifecycle == "Accepted":
        return "accepted"
    if lifecycle in {"Failed", "Blocked", "Cancelled"}:
        return "mi_terminal"
    if lifecycle in INTERACTION_LIFECYCLES:
        return "awaiting_interaction"
    if lifecycle in KNOWN_LIFECYCLES:
        return None
    return "invalid_response"


def fetch_lifecycle(
    endpoint: str, request_id: str, timeout_seconds: float
) -> tuple[str | None, str | None]:
    """Query one lifecycle snapshot; errors return (None, description)."""
    url = f"{endpoint.rstrip('/')}/v1/mission-requests/{request_id}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
            document = json.loads(response.read().decode("utf-8"))
    except (OSError, HTTPException, ValueError) as error:
        return None, f"{type(error).__name__}: {error}"
    if not isinstance(document, dict):
        return None, "response_not_object"
    lifecycle = document.get("lifecycle")
    if not isinstance(lifecycle, str) or not lifecycle:
        return None, "lifecycle_missing"
    return lifecycle, None


def service_pid_alive(pid: int) -> bool:
    """Report whether the Mission Service process is still alive.

    A zombie (exited but unreaped) counts as exited: the runner owns its
    children and reads their status only at cleanup, so an unreaped exit
    must still be attributed as a service exit rather than a wait timeout.
    """
    if pid <= 0:
        return True  # No process handle supplied; cannot claim exit.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        state = stat.rpartition(")")[2].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return True  # Unreadable state stays conservatively alive.


def wait_for_lifecycle(
    *,
    endpoint: str,
    request_id: str,
    budget_seconds: float,
    deadline_clock: Callable[[], float] = time.monotonic,
    poll: Callable[[str, str, float], tuple[str | None, str | None]] = fetch_lifecycle,
    pid: int = 0,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    log_path: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stall_seconds: float | None = None,
) -> WaitResult:
    """Wait until MI reaches a classified state or the frozen budget ends.

    The deadline is a monotonic timestamp captured once at entry. Polls
    are logged as bounded JSONL evidence; a final bounded query after the
    deadline resolves the boundary race; service-process exit and
    unusable responses are distinct outcomes, never MI failures.
    """
    deadline = deadline_clock() + budget_seconds
    lifecycle: str | None = None
    last_error: str | None = None
    logged = 0
    dropped = 0
    first_timeout_at: float | None = None
    last_transition_at = deadline_clock()
    observed_lifecycle: str | None = None

    def record(now: float, http_ok: bool, alive: bool) -> None:
        """Append one bounded poll row, counting skipped rows honestly."""
        nonlocal logged, dropped
        if logged + dropped >= MAX_LOG_RECORDS and (logged + dropped) % 10 != 0:
            dropped += 1
            return
        row = {
            "t_monotonic": now,
            "request_id": request_id,
            "lifecycle": lifecycle if lifecycle is not None else "unknown",
            "http_ok": http_ok,
            "service_alive": alive,
            "remaining_s": max(0.0, deadline - now),
        }
        if log_path is not None:
            with log_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row, sort_keys=True) + "\n")
        logged += 1

    while True:
        now = deadline_clock()
        alive = service_pid_alive(pid)
        if not alive:
            record(now, last_error is None, False)
            return WaitResult(
                outcome="service_exited",
                lifecycle=lifecycle if lifecycle is not None else "unknown",
                request_id=request_id,
                last_error=last_error,
                records_logged=logged,
                records_dropped=dropped,
            )
        if now >= deadline:
            if first_timeout_at is None:
                first_timeout_at = now
            # One bounded final query resolves the deadline race before
            # the run is attributed to an observation timeout.
            polled_lifecycle, error = poll(endpoint, request_id, FINAL_QUERY_TIMEOUT_SECONDS)
            if polled_lifecycle is not None:
                lifecycle = polled_lifecycle
            last_error = last_error if error is None else error
            if lifecycle is not None:
                classified = _classify(lifecycle)
                if classified is not None:
                    return WaitResult(
                        outcome=classified,
                        lifecycle=lifecycle,
                        request_id=request_id,
                        last_error=last_error,
                        records_logged=logged,
                        records_dropped=dropped,
                        first_timeout_at=first_timeout_at,
                    )
            record(now, lifecycle is not None, service_pid_alive(pid))
            return WaitResult(
                outcome="observation_timeout",
                lifecycle=lifecycle if lifecycle is not None else "unknown",
                request_id=request_id,
                last_error=last_error,
                records_logged=logged,
                records_dropped=dropped,
                first_timeout_at=first_timeout_at,
            )
        polled_lifecycle, error = poll(endpoint, request_id, 5.0)
        if polled_lifecycle is not None and polled_lifecycle != observed_lifecycle:
            observed_lifecycle = polled_lifecycle
            last_transition_at = deadline_clock()
            lifecycle = polled_lifecycle
        elif polled_lifecycle is not None:
            lifecycle = polled_lifecycle
        last_error = last_error if error is None else error
        now = deadline_clock()
        record(now, polled_lifecycle is not None, service_pid_alive(pid))
        if (
            stall_seconds is not None
            and lifecycle is not None
            and observed_lifecycle is not None
            and now - last_transition_at > stall_seconds
        ):
            # MI is alive but held one lifecycle state far past any
            # legitimate single-call ceiling: a slow-drip or wedged
            # provider call, not a legal deliberation still in progress.
            return WaitResult(
                outcome="stalled_no_progress",
                lifecycle=lifecycle,
                request_id=request_id,
                last_error=last_error,
                records_logged=logged,
                records_dropped=dropped,
            )
        if lifecycle is not None:
            classified = _classify(lifecycle)
            if classified is not None:
                return WaitResult(
                    outcome=classified,
                    lifecycle=lifecycle,
                    request_id=request_id,
                    last_error=last_error,
                    records_logged=logged,
                    records_dropped=dropped,
                )
        # Transient HTTP failures never fail the wait: they are retried
        # until the deadline; persistent failure surfaces as either
        # observation_timeout (service alive) or service_exited.
        sleep(poll_interval_seconds)


def recover_request_id_from_store(store_path: Path, instruction: str) -> str | None:
    """Read this run's persisted request identity from its Mission store.

    The store file lives inside this run's exclusive directory and this
    runner submits exactly one request, so at most one row exists; the
    first dialogue instruction must equal the submitted text before the
    identity is accepted. Never guesses latest-record across runs.
    """
    try:
        connection = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "SELECT request_id, document_json FROM mission_requests"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    # MissionRequestStore persists an atomic storage envelope containing the
    # public request projection under "request", not at document root. An
    # exclusive B1 run submits exactly one request; multiple rows are ambiguous.
    if len(rows) != 1:
        return None
    request_id, document_json = rows[0]
    try:
        document = json.loads(str(document_json))
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schema_version") == "roboguide.mission-request-storage/v0.1":
        request = document.get("request")
    else:
        request = document  # Legacy request rows predate the envelope.
    if not isinstance(request, dict) or request.get("request_id") != request_id:
        return None
    dialogue = request.get("dialogue")
    if not isinstance(dialogue, list) or not dialogue or not isinstance(dialogue[0], dict):
        return None
    if dialogue[0].get("content") != instruction.strip():
        return None
    return request_id if isinstance(request_id, str) and request_id else None


def post_instruction(
    endpoint: str, instruction: str, timeout_seconds: float
) -> tuple[dict[str, Any] | None, str | None]:
    """Submit the synchronous create POST under one client-side bound.

    Returns the decoded response object, or (None, error class) where the
    class names the failure: ``post_timeout`` (client budget expired;
    the server may still be executing), ``transport`` (connection broken,
    refused, or HTTP protocol error), or ``invalid_response`` (non-JSON
    or non-object body).
    """
    url = f"{endpoint.rstrip('/')}/v1/mission-requests"
    payload = json.dumps({"instruction": instruction}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except TimeoutError:
        return None, "post_timeout"
    except (OSError, HTTPException) as error:
        if isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
            return None, "post_timeout"
        return None, "transport"
    try:
        document = json.loads(body)
    except ValueError:
        return None, "invalid_response"
    if not isinstance(document, dict):
        return None, "invalid_response"
    return document, None


def submit_and_wait(
    *,
    endpoint: str,
    instruction: str,
    budget_seconds: float,
    store_path: Path,
    pid: int = 0,
    deadline_clock: Callable[[], float] = time.monotonic,
    poll: Callable[[str, str, float], tuple[str | None, str | None]] = fetch_lifecycle,
    post: Callable[[str, str, float], tuple[dict[str, Any] | None, str | None]] = post_instruction,
    recover: Callable[[Path, str], str | None] = recover_request_id_from_store,
    sleep: Callable[[float], None] = time.sleep,
    log_path: Path | None = None,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    stall_seconds: float | None = None,
) -> WaitResult:
    """Submit the synchronous MI request and observe it under one budget.

    The single frozen budget starts at submission and covers both the
    POST window and any post-response polling. A POST that exhausts the
    client budget does not fail MI: the run's identity is recovered from
    this run's request store and observation continues until a real
    state, the budget, or process exit ends it; without a trustworthy
    identity the run is archived as ``request_id_unavailable``. Nothing
    here ever resubmits the instruction.
    """
    start = deadline_clock()
    deadline = start + budget_seconds
    response, error = post(endpoint, instruction, budget_seconds)
    if error == "post_timeout":
        request_id = recover(store_path, instruction)
        if request_id is None:
            return WaitResult(
                outcome="request_id_unavailable",
                lifecycle="unknown",
                request_id="",
                last_error="post client budget expired before any response; "
                "no persisted request identity matched this instruction",
            )
        remaining = deadline - deadline_clock()
        if remaining <= 0:
            return WaitResult(
                outcome="observation_timeout",
                lifecycle="unknown",
                request_id=request_id,
                last_error="post client budget expired; identity recovered after deadline",
            )
        return wait_for_lifecycle(
            endpoint=endpoint,
            request_id=request_id,
            budget_seconds=remaining,
            deadline_clock=deadline_clock,
            poll=poll,
            pid=pid,
            poll_interval_seconds=poll_interval_seconds,
            log_path=log_path,
            sleep=sleep,
            stall_seconds=stall_seconds,
        )
    if error in {"transport", "invalid_response"}:
        # A dropped connection or unusable response does not prove that the
        # synchronous server-side create() failed. Recover this run's sole
        # persisted request before attributing a transport-only outcome.
        recovered_id = recover(store_path, instruction)
        if recovered_id is not None:
            remaining = deadline - deadline_clock()
            if remaining <= 0:
                return WaitResult(
                    outcome="observation_timeout",
                    lifecycle="unknown",
                    request_id=recovered_id,
                    last_error=f"submit returned {error}; request recovered after deadline",
                )
            return wait_for_lifecycle(
                endpoint=endpoint,
                request_id=recovered_id,
                budget_seconds=remaining,
                deadline_clock=deadline_clock,
                poll=poll,
                pid=pid,
                poll_interval_seconds=poll_interval_seconds,
                log_path=log_path,
                sleep=sleep,
                stall_seconds=stall_seconds,
            )
        alive = service_pid_alive(pid)
        if not alive:
            return WaitResult(
                outcome="service_exited",
                lifecycle="unknown",
                request_id="",
                last_error=f"mission service exited before responding ({error})",
            )
        return WaitResult(
            outcome="http_unusable",
            lifecycle="unknown",
            request_id="",
            last_error=f"submit failed with {error}",
        )
    assert response is not None
    request_id = response.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return WaitResult(
            outcome="http_unusable",
            lifecycle="unknown",
            request_id="",
            last_error="submit response lacks a request id",
        )
    lifecycle = response.get("lifecycle")
    if isinstance(lifecycle, str) and lifecycle:
        classified = _classify(lifecycle)
        if classified is not None:
            return WaitResult(
                outcome=classified,
                lifecycle=lifecycle,
                request_id=request_id,
                last_error=None,
            )
    # Synchronous create returns stable states; a non-terminal value here
    # means polling is genuinely required. Fund it with the unspent share
    # of the one frozen budget, never a fresh full window.
    remaining = deadline - deadline_clock()
    if remaining <= 0:
        return WaitResult(
            outcome="observation_timeout",
            lifecycle=str(lifecycle or "unknown"),
            request_id=request_id,
            last_error="budget exhausted by the synchronous submit itself",
        )
    return wait_for_lifecycle(
        endpoint=endpoint,
        request_id=request_id,
        budget_seconds=remaining,
        deadline_clock=deadline_clock,
        poll=poll,
        pid=pid,
        poll_interval_seconds=poll_interval_seconds,
        log_path=log_path,
        sleep=sleep,
        stall_seconds=stall_seconds,
    )


def _main(argv: list[str] | None = None) -> int:
    """Run one state-driven MI wait and print the JSON outcome."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-only", action="store_true")
    parser.add_argument("--submit-and-wait", action="store_true")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--mission-config", type=Path)
    parser.add_argument("--service-config", type=Path)
    parser.add_argument("--endpoint")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--budget-seconds", type=float, default=0.0)
    parser.add_argument("--service-pid", type=int, default=0)
    parser.add_argument("--log-path", type=Path, default=None)
    parser.add_argument("--poll-interval-seconds", type=float, default=POLL_INTERVAL_SECONDS)
    parser.add_argument("--stall-seconds", type=float, default=None)
    arguments = parser.parse_args(argv)
    if arguments.budget_only:
        return _budget_main(
            [
                "--mission-config",
                str(arguments.mission_config),
                "--service-config",
                str(arguments.service_config),
            ]
        )
    if arguments.submit_and_wait:
        result = submit_and_wait(
            endpoint=arguments.endpoint or "",
            instruction=arguments.instruction,
            budget_seconds=arguments.budget_seconds,
            store_path=arguments.store_path or Path("unused.sqlite3"),
            pid=arguments.service_pid,
            log_path=arguments.log_path,
            poll_interval_seconds=arguments.poll_interval_seconds,
            stall_seconds=arguments.stall_seconds,
        )
        print(json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True))
        return (
            0
            if result.outcome
            in {
                "accepted",
                "mi_terminal",
                "awaiting_interaction",
            }
            else 3
        )
    result = wait_for_lifecycle(
        endpoint=arguments.endpoint or "",
        request_id=arguments.request_id,
        budget_seconds=arguments.budget_seconds,
        pid=arguments.service_pid,
        poll_interval_seconds=arguments.poll_interval_seconds,
        log_path=arguments.log_path,
        stall_seconds=arguments.stall_seconds,
    )
    print(json.dumps(result.to_json(), ensure_ascii=False, sort_keys=True))
    return 0 if result.outcome in {"accepted", "mi_terminal", "awaiting_interaction"} else 3


def _budget_main(argv: list[str] | None = None) -> int:
    """Derive the frozen budget from real config files and print JSON."""
    parser = argparse.ArgumentParser(description="Derive the MI wait budget")
    parser.add_argument("--mission-config", type=Path, required=True)
    parser.add_argument("--service-config", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        budget = derive_wait_budget(arguments.mission_config, arguments.service_config)
    except WaitConfigurationError as error:
        print(f"cannot derive MI wait budget: {error}", file=sys.stderr)
        return 2
    print(json.dumps(budget.to_json(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
