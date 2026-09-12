"""Recording infrastructure for the Mission Front-half eval.

The mission package keeps its LLM transport injectable
(``JsonTransport``) and its engine ports pluggable. The eval harness uses
those seams — without touching RoboGuide Core or the mission package — to
measure the front half:

- :class:`RecordingTransport` wraps the real HTTP transport and records, per
  LLM call, the calling stage, latency, byte sizes, and the provider ``usage``
  object when the response carries one;
- :class:`StageScope` marks which pipeline stage (interpreter / planner /
  reviewer / repairer) is active so transport records are attributable;
- :class:`StageTimedPort` proxies wrap the four engine ports to capture
  per-stage wall latency including prompt building and parsing;
- :class:`RecordingSubmitter` implements the ``MissionPlanSubmitter``
  protocol so an accepted plan terminates the pipeline without contacting a
  real Controller — front-half evaluation ends at plan acceptance.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Final

from mission.controller import SubmissionReceipt
from mission.models import JSONObject, MissionPlan
from mission.responses import JsonTransport

_TRANSPORT_STAGE_UNSET: Final = "unattributed"


@dataclass
class TransportCall:
    """Record one LLM transport call made through the recording transport."""

    stage: str
    url: str
    latency_ms: float
    request_bytes: int
    response_bytes: int
    ok: bool
    error: str | None
    usage: dict[str, object] | None


class StageScope:
    """Track the currently active pipeline stage for transport attribution."""

    def __init__(self) -> None:
        """Start with no stage attributed and a lock for sequential runs."""
        self._lock = threading.Lock()
        self._stage: str = _TRANSPORT_STAGE_UNSET

    def enter(self, stage: str) -> None:
        """Mark one pipeline stage as active.

        Args:
            stage: Stage name (``interpreter`` / ``planner`` / ``reviewer`` /
                ``repairer``).
        """
        with self._lock:
            self._stage = stage

    def leave(self) -> None:
        """Clear the active stage marker."""
        with self._lock:
            self._stage = _TRANSPORT_STAGE_UNSET

    @property
    def current(self) -> str:
        """Return the currently attributed stage name.

        Returns:
            The active stage, or ``unattributed`` outside port calls.
        """
        with self._lock:
            return self._stage


class RecordingTransport:
    """Wrap one real ``JsonTransport`` and record every LLM call."""

    def __init__(self, delegate: JsonTransport, stages: StageScope) -> None:
        """Create the recording transport.

        Args:
            delegate: The real transport (typically
                ``mission.responses.UrllibJsonTransport``) used for actual
                network I/O.
            stages: The stage scope providing call attribution.
        """
        self._delegate = delegate
        self._stages = stages
        self._lock = threading.Lock()
        self.calls: list[TransportCall] = []

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Forward one POST to the delegate and record the observed call.

        Args:
            url: The provider endpoint URL.
            headers: Request headers (credentials pass through, never stored).
            payload: The request JSON payload.
            timeout_seconds: Upstream timeout.

        Returns:
            The delegate's decoded JSON response.

        Raises:
            Exception: Whatever the delegate raises; the failure is recorded
                first so failed calls remain evidence.
        """
        stage = self._stages.current
        started = time.monotonic()
        response: JSONObject
        try:
            response = self._delegate.post_json(url, headers, payload, timeout_seconds)
        except Exception as transport_error:  # noqa: BLE001 - record then re-raise
            latency = (time.monotonic() - started) * 1000.0
            with self._lock:
                self.calls.append(
                    TransportCall(
                        stage=stage,
                        url=url,
                        latency_ms=round(latency, 1),
                        request_bytes=len(json_bytes(payload)),
                        response_bytes=0,
                        ok=False,
                        error=str(transport_error),
                        usage=None,
                    )
                )
            raise
        latency = (time.monotonic() - started) * 1000.0
        usage = response.get("usage") if isinstance(response, dict) else None
        usage_record = usage if isinstance(usage, dict) else None
        with self._lock:
            self.calls.append(
                TransportCall(
                    stage=stage,
                    url=url,
                    latency_ms=round(latency, 1),
                    request_bytes=len(json_bytes(payload)),
                    response_bytes=len(json_bytes(response)),
                    ok=True,
                    error=None,
                    usage=usage_record,
                )
            )
        return response


def json_bytes(value: object) -> bytes:
    """Serialize one JSON value for byte-size accounting.

    Args:
        value: The JSON-compatible value.

    Returns:
        The compact UTF-8 encoding of the value.
    """
    import json

    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


@dataclass
class StageTiming:
    """Accumulate one pipeline stage's wall-clock invocations."""

    stage: str
    durations_ms: list[float] = field(default_factory=list)

    def record(self, duration_ms: float) -> None:
        """Append one invocation duration.

        Args:
            duration_ms: The measured wall-clock duration in milliseconds.
        """
        self.durations_ms.append(round(duration_ms, 1))

    def summary(self) -> dict[str, object]:
        """Summarize the stage's timing.

        Returns:
            Call count, total, average, and maximum duration in milliseconds.
        """
        values = self.durations_ms
        if not values:
            return {"calls": 0, "total_ms": 0.0, "avg_ms": None, "max_ms": None}
        return {
            "calls": len(values),
            "total_ms": round(sum(values), 1),
            "avg_ms": round(sum(values) / len(values), 1),
            "max_ms": round(max(values), 1),
        }


class StageTimedPort:
    """Proxy one engine port: tag the stage scope and measure stage latency."""

    def __init__(self, delegate: object, stage: str, stages: StageScope) -> None:
        """Create the timed proxy.

        Args:
            delegate: The real port (Interpreter / Planner / Reviewer /
                Repairer adapter).
            stage: The stage name attributed to this port's calls.
            stages: The shared stage scope.
        """
        self._delegate = delegate
        self._stage = stage
        self._stages = stages
        self.timing = StageTiming(stage=stage)

    def _invoke(self, method_name: str, *args: object) -> object:
        """Delegate one port method with stage tagging and timing.

        Args:
            method_name: The port method to call.
            *args: Forwarded positional arguments.

        Returns:
            The delegate's return value.
        """
        self._stages.enter(self._stage)
        started = time.monotonic()
        try:
            return getattr(self._delegate, method_name)(*args)
        finally:
            self.timing.record((time.monotonic() - started) * 1000.0)
            self._stages.leave()

    def interpret(self, *args: object) -> object:
        """Delegate the Interpreter call.

        Args:
            *args: Forwarded arguments (dialogue turns).

        Returns:
            The delegate's assessment.
        """
        return self._invoke("interpret", *args)

    def plan(self, *args: object) -> object:
        """Delegate the Planner call.

        Args:
            *args: Forwarded arguments (mission id, grounded intent, catalog).

        Returns:
            The delegate's MissionPlan.
        """
        return self._invoke("plan", *args)

    def review(self, *args: object) -> object:
        """Delegate the Reviewer call.

        Args:
            *args: Forwarded arguments (intent, plan, catalog).

        Returns:
            The delegate's review.
        """
        return self._invoke("review", *args)

    def repair(self, *args: object) -> object:
        """Delegate the Repairer call.

        Args:
            *args: Forwarded arguments (mission id, intent, plan, review,
                catalog).

        Returns:
            The delegate's repaired MissionPlan.
        """
        return self._invoke("repair", *args)


class RecordingSubmitter:
    """Implement ``MissionPlanSubmitter`` by recording acceptance locally.

    Front-half evaluation ends at plan acceptance; no Controller is contacted
    and no execution state is created.
    """

    def __init__(self) -> None:
        """Create the submitter with an empty submission record."""
        self.submitted: list[MissionPlan] = []

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Record one submitted plan and accept it unconditionally.

        Args:
            plan: The semantically admitted MissionPlan.

        Returns:
            An accepted receipt; the eval never exercises Controller-side
            admission, which belongs to the execution half.
        """
        self.submitted.append(plan)
        return SubmissionReceipt(accepted=True, status_code=200, detail="eval recording submitter")
