"""State-driven MI lifecycle waiting for the B1 scenario runner.

The runner waits for Mission Intelligence with a monotonic-clock
deadline derived from the configuration this run's Mission Service
actually uses — never a stale constant. Every poll records bounded
JSONL evidence (request id, lifecycle, HTTP health, service liveness,
remaining budget), and the outcome separates MI's own terminal states
from runner observation timeouts, service-process exits, and unusable
responses, so the scenario can attribute failures without inventing an
MI ``Failed``.

Worst-case budget derivation (per ``mission`` and ``service`` config
actually passed to this run's Mission Service):

- grounding capture: 2 sources x ``grounding_acquisition_attempts`` x
  ``grounding_timeout_seconds``;
- Interpreter: 1 x ``timeout_seconds``;
- Planner: (1 + ``prevalidation_recovery_attempts``) x ``timeout_seconds``;
- Reviewer: (``max_repair_attempts`` + 1) x ``timeout_seconds``;
- Repairer: ``max_repair_attempts`` x ``timeout_seconds``;
- Controller submission: ``controller_timeout_seconds``;
- plus a fixed scheduling/polling margin.

Retry or clarification turns are separate HTTP calls that restart the
observation externally; the budget covers one deliberation pass, which
is what this runner triggers.
"""

from __future__ import annotations

import argparse
import json
import os
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
        components: The per-stage contributions actually used.
        sources: The configuration files the numbers came from.
    """

    total_seconds: float
    components: dict[str, float]
    sources: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        """Serialize the frozen budget evidence document."""
        return {
            "schema_version": BUDGET_SCHEMA,
            "total_seconds": self.total_seconds,
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
    return WaitBudget(
        total_seconds=sum(components.values()),
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
        if polled_lifecycle is not None:
            lifecycle = polled_lifecycle
        last_error = last_error if error is None else error
        record(deadline_clock(), lifecycle is not None, service_pid_alive(pid))
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


def _main(argv: list[str] | None = None) -> int:
    """Run one state-driven MI wait and print the JSON outcome."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-only", action="store_true")
    parser.add_argument("--mission-config", type=Path)
    parser.add_argument("--service-config", type=Path)
    parser.add_argument("--endpoint")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--budget-seconds", type=float, default=0.0)
    parser.add_argument("--service-pid", type=int, default=0)
    parser.add_argument("--log-path", type=Path, default=None)
    parser.add_argument("--poll-interval-seconds", type=float, default=POLL_INTERVAL_SECONDS)
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
    result = wait_for_lifecycle(
        endpoint=arguments.endpoint or "",
        request_id=arguments.request_id,
        budget_seconds=arguments.budget_seconds,
        pid=arguments.service_pid,
        poll_interval_seconds=arguments.poll_interval_seconds,
        log_path=arguments.log_path,
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
