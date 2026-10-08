"""Opt-in, bounded navigation observations; never execution or success authority."""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .evidence_io import write_text_atomic
from .model import CanonicalInvocation

if TYPE_CHECKING:
    from .store import StoredExecution

PROGRESS_SCHEMA = "roboguide.execution-progress/v0.1"
_LOCAL_SCHEMA = "roboguide.habitat-navigation-progress/v0.1"
_MAX_BYTES = 4096
_SAMPLE_PERIOD_NS = 250_000_000
_FRESH_FOR_NS = 2_000_000_000
_NAVIGATION_SKILLS = frozenset(
    {"direct-oracle-nav", "nav_to_obj", "nav_to_goal", "nav_to_receptacle_by_name"}
)
_LOG = logging.getLogger(__name__)


class NavigationProgressPublisher:
    """Sample existing post-reset/post-step state into one bounded file per endpoint.

    A navigation stage measures the best observed Euclidean distance improvement
    to the committed entity, in whole centimetres. This is not geodesic progress,
    arrival, official predicate truth, or evidence that a detour cannot succeed.
    Wait and unsupported skills have no counter. Read/write failures never escape
    into physical execution; missing observations expire at the reader.
    """

    def __init__(
        self,
        directory: Path | None,
        invocation: CanonicalInvocation,
        agent_id: int,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        """Bind fixed deployment output to the exact supplied physical attempt."""
        attempt = invocation.attempt_id
        self._path = (
            directory / f"agent-{agent_id}.json"
            if directory is not None and attempt is not None and len(attempt) <= 256
            else None
        )
        self._invocation = invocation
        self._agent_id = agent_id
        self._clock_ns = clock_ns
        self._next_sample_ns = 0
        self._stage: tuple[str, tuple[float, float, float] | None] | None = None
        self._stage_epoch = 0
        self._initial_distance = 0.0
        self._best_distance = 0.0
        self._warned = False

    def observe(self, habitat_env: Any, skill: str) -> None:
        """Read existing positions at most four times a second, without stepping or RNG."""
        if self._path is None:
            return
        try:
            now = self._clock_ns()
            if now < self._next_sample_ns:
                return
            self._next_sample_ns = now + _SAMPLE_PERIOD_NS
            distance: float | None = None
            target_point: tuple[float, float, float] | None = None
            if skill in _NAVIGATION_SKILLS:
                try:
                    problem = habitat_env.task.pddl_problem
                    entity = problem.get_entity(self._invocation.destination)
                    if entity is not None:
                        target = problem.sim_info.get_entity_pos(entity)
                        base = habitat_env.sim.get_agent_data(
                            self._agent_id
                        ).articulated_agent.base_pos
                        target_point = _point(target)
                        distance = math.dist(_point(base), target_point)
                except Exception:  # noqa: BLE001 - optional observation only
                    distance = None
                    target_point = None
            stage = (skill, target_point)
            if stage != self._stage:
                self._stage_epoch += 1
                self._stage = stage
                self._initial_distance = distance if distance is not None else 0.0
                self._best_distance = self._initial_distance
            units: int | None = None
            if distance is not None:
                self._best_distance = min(self._best_distance, distance)
                units = math.floor((self._initial_distance - self._best_distance) * 100)
            activity = (
                "waiting"
                if skill == "wait"
                else "working"
                if distance is not None
                else "unavailable"
            )
            namespace_name, version = self._invocation.operation.rsplit("@", 1)
            namespace, name = namespace_name.rsplit(".", 1)
            document = {
                "schema_version": _LOCAL_SCHEMA,
                "observed_at_monotonic_ns": now,
                "agent_id": self._agent_id,
                "destination": self._invocation.destination,
                "skill": skill,
                "unit": "centimetres-best-distance-improvement" if units is not None else None,
                "sample": {
                    "execution_id": self._invocation.attempt_id,
                    "operation": {"namespace": namespace, "name": name, "version": version},
                    "stage_epoch": self._stage_epoch,
                    "completed_units": units,
                    "activity": activity,
                },
            }
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(encoded.encode("utf-8")) > _MAX_BYTES:
                return
            write_text_atomic(self._path, encoded)
        except Exception:  # noqa: BLE001 - progress cannot change actions or outcomes
            if not self._warned:
                self._warned = True
                _LOG.warning("optional navigation progress publication unavailable", exc_info=True)


def read_execution_progress(
    directory: Path | None,
    agent_id: int,
    execution: StoredExecution | None,
    *,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, object]:
    """Read one fresh exact-attempt sample; absent/stale/malformed evidence stays unknown.

    Accepted work reports intentional deployment wait, without a work counter.
    Terminal work has no active progress. Repeated HTTP polls cannot renew the
    producer timestamp. The timestamp is process-local to this one machine;
    Controller freshness still uses its own independent receive time and TTL.
    """
    empty: dict[str, object] = {"schema_version": PROGRESS_SCHEMA, "executions": []}
    if directory is None or execution is None:
        return empty
    invocation = execution["invocation"]
    attempt = invocation.attempt_id
    if attempt is None or len(attempt) > 256:
        return empty
    namespace_name, version = invocation.operation.rsplit("@", 1)
    namespace, name = namespace_name.rsplit(".", 1)
    operation = {"namespace": namespace, "name": name, "version": version}
    if execution["state"] == "ACCEPTED":
        return {
            "schema_version": PROGRESS_SCHEMA,
            "executions": [
                {
                    "execution_id": attempt,
                    "operation": operation,
                    "stage_epoch": 0,
                    "completed_units": None,
                    "activity": "waiting",
                }
            ],
        }
    if execution["state"] != "RUNNING":
        return empty
    try:
        with (directory / f"agent-{agent_id}.json").open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return empty
        document = json.loads(raw)
        if not isinstance(document, dict) or document.get("schema_version") != _LOCAL_SCHEMA:
            return empty
        timestamp = document.get("observed_at_monotonic_ns")
        now = clock_ns()
        if type(timestamp) is not int or not 0 <= now - timestamp < _FRESH_FOR_NS:
            return empty
        sample = document.get("sample")
        if (
            type(document.get("agent_id")) is not int
            or document.get("agent_id") != agent_id
            or document.get("destination") != invocation.destination
            or not isinstance(sample, dict)
            or set(sample)
            != {"execution_id", "operation", "stage_epoch", "completed_units", "activity"}
            or sample.get("execution_id") != attempt
            or sample.get("operation") != operation
        ):
            return empty
        stage = sample["stage_epoch"]
        units = sample["completed_units"]
        if (
            type(stage) is not int
            or not 1 <= stage <= 2**64 - 1
            or (units is not None and (type(units) is not int or not 0 <= units <= 2**64 - 1))
            or sample["activity"] not in {"working", "waiting", "unavailable"}
            or (sample["activity"] != "working" and units is not None)
        ):
            return empty
        return {"schema_version": PROGRESS_SCHEMA, "executions": [sample]}
    except Exception:  # noqa: BLE001 - optional observation must not change lifecycle
        return empty


def _point(value: Any) -> tuple[float, float, float]:
    """Reject missing/non-finite three-dimensional positions without inventing geometry."""
    if len(value) != 3:
        raise ValueError("position needs three coordinates")
    point = (float(value[0]), float(value[1]), float(value[2]))
    if not all(math.isfinite(component) for component in point):
        raise ValueError("position is not finite")
    return point
