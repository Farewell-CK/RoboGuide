"""Canonical evaluation metric schema shared by every experiment and system.

The registry is deliberately not E1-specific: it already reserves the metric
names that later experiments (scheduling, recovery, resources, state
freshness, control-plane overhead) will emit, so extending the harness never
requires reshaping the metrics contract. Systems under test are responsible
for converting their own raw output into these canonical values; the harness
never decides what "success" means for Habitat-MAS or RoboGuide.

Raw evidence always travels beside the summarized numbers: a
:class:`MetricsPayload` references the untouched artifact files inside the run
directory instead of replacing them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from roboguide_eval.models import JSONObject, JSONValue

METRICS_SCHEMA: Final = "roboguide-eval.metrics/v0.1"
METRICS_FILE_NAME: Final = "metrics.json"

type MetricValueKind = Literal["boolean", "integer", "number"]


class MetricsError(ValueError):
    """Report an invalid canonical metric name, type, or payload."""


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """Describe one canonical metric: its value kind, unit, and meaning."""

    name: str
    value_kind: MetricValueKind
    unit: str | None
    description: str


CANONICAL_METRICS: Final[tuple[MetricDefinition, ...]] = (
    MetricDefinition(
        "success", "boolean", None, "episode-level task success judged by the system under test"
    ),
    MetricDefinition(
        "subgoal_success",
        "boolean",
        None,
        "episode-level subgoal completion judged by the system under test",
    ),
    MetricDefinition(
        "simulation_steps", "integer", "steps", "simulator steps consumed by the episode"
    ),
    MetricDefinition(
        "token_usage", "integer", "tokens", "total LLM tokens consumed by the episode"
    ),
    MetricDefinition("wall_time", "number", "s", "wall-clock duration of the episode run"),
    MetricDefinition(
        "coordination_latency",
        "number",
        "s",
        "latency of coordination decisions during the episode",
    ),
    MetricDefinition(
        "invalid_assignment_count",
        "integer",
        "count",
        "assignments rejected or undone during the episode",
    ),
    MetricDefinition("scheduling_latency", "number", "s", "reserved: scheduler decision latency"),
    MetricDefinition("recovery_latency", "number", "s", "reserved: failure recovery latency"),
    MetricDefinition(
        "resource_conflict_count", "integer", "count", "reserved: shared resource conflicts"
    ),
    MetricDefinition("deadline_miss_count", "integer", "count", "reserved: missed task deadlines"),
    MetricDefinition(
        "state_freshness_seconds", "number", "s", "reserved: observed state staleness"
    ),
    MetricDefinition(
        "control_plane_cpu_seconds", "number", "s", "reserved: control-plane CPU overhead"
    ),
    MetricDefinition(
        "control_plane_memory_bytes", "integer", "bytes", "reserved: control-plane memory overhead"
    ),
)

_REGISTRY: Final[Mapping[str, MetricDefinition]] = {
    definition.name: definition for definition in CANONICAL_METRICS
}


def metric_registry() -> Mapping[str, MetricDefinition]:
    """Return the canonical metric registry keyed by metric name.

    Returns:
        An immutable mapping from metric name to its definition.
    """
    return _REGISTRY


def validate_metric_names(names: tuple[str, ...] | list[str]) -> None:
    """Reject unknown or duplicate metric names in an experiment spec.

    Args:
        names: Metric names requested by an experiment.

    Raises:
        MetricsError: If any name is not part of the canonical registry or
            appears more than once.
    """
    seen: set[str] = set()
    for name in names:
        if name not in _REGISTRY:
            raise MetricsError(f"unknown canonical metric: {name!r}")
        if name in seen:
            raise MetricsError(f"duplicate canonical metric: {name!r}")
        seen.add(name)


@dataclass(frozen=True, slots=True)
class RawEvidenceRef:
    """Reference one untouched artifact file that backs a metric value."""

    path: str
    description: str
    media_type: str = "application/octet-stream"

    def to_json(self) -> JSONObject:
        """Return the JSON object form of this evidence reference.

        Returns:
            A JSON object with the reference path, description, and media type.
        """
        return {"path": self.path, "description": self.description, "media_type": self.media_type}

    @classmethod
    def from_json(cls, value: JSONValue) -> RawEvidenceRef:
        """Rebuild one evidence reference from its JSON object form.

        Args:
            value: Decoded JSON value expected to be an evidence object.

        Returns:
            The reconstructed :class:`RawEvidenceRef`.

        Raises:
            MetricsError: If the value is not a well-formed evidence object.
        """
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise MetricsError("raw evidence reference must be an object")
        path = value.get("path")
        description = value.get("description")
        if not isinstance(path, str) or not path:
            raise MetricsError("raw evidence reference needs a nonblank 'path'")
        if not isinstance(description, str):
            raise MetricsError("raw evidence reference needs a 'description'")
        media_type = value.get("media_type", "application/octet-stream")
        if not isinstance(media_type, str) or not media_type:
            raise MetricsError("raw evidence reference needs a nonblank 'media_type'")
        return cls(path=path, description=description, media_type=media_type)


@dataclass(frozen=True, slots=True)
class MetricsPayload:
    """Summarized canonical metric values plus pointers to raw evidence.

    ``values`` only ever contains canonical registry names. Anything extra a
    system reports is preserved under ``details`` so no information is lost
    while the canonical contract stays closed.
    """

    values: Mapping[str, bool | int | float]
    details: Mapping[str, JSONValue]
    raw_evidence: tuple[RawEvidenceRef, ...]

    @classmethod
    def empty(cls) -> MetricsPayload:
        """Return a payload with no values and no evidence.

        Returns:
            An empty payload used when a run produced no metrics (for example
            because the external process failed before reporting anything).
        """
        return cls(values={}, details={}, raw_evidence=())

    def validate(self) -> None:
        """Check value names and types against the canonical registry.

        Raises:
            MetricsError: If a value uses an unknown name or a value whose
                JSON type does not match the declared metric kind.
        """
        registry = metric_registry()
        for name, value in self.values.items():
            definition = registry.get(name)
            if definition is None:
                raise MetricsError(f"unknown canonical metric value: {name!r}")
            if definition.value_kind == "boolean" and isinstance(value, bool):
                continue
            if (
                definition.value_kind == "integer"
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                continue
            if (
                definition.value_kind == "number"
                and isinstance(value, int | float)
                and not isinstance(value, bool)
            ):
                continue
            raise MetricsError(
                f"metric {name!r} expects {definition.value_kind}, got {type(value).__name__}"
            )

    def to_json(self) -> JSONObject:
        """Serialize the payload into its versioned JSON form.

        Returns:
            A JSON object carrying the schema, values, details, and evidence.
        """
        return {
            "schema": METRICS_SCHEMA,
            "values": dict(sorted(self.values.items())),
            "details": dict(self.details),
            "raw_evidence": [evidence.to_json() for evidence in self.raw_evidence],
        }

    @classmethod
    def from_json(cls, value: JSONValue) -> MetricsPayload:
        """Rebuild and validate a payload from its JSON form.

        Args:
            value: Decoded JSON value expected to be a metrics payload object.

        Returns:
            The reconstructed, validated :class:`MetricsPayload`.

        Raises:
            MetricsError: If the envelope, values, or evidence are malformed
                or violate the canonical metric contract.
        """
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise MetricsError("metrics payload must be an object")
        schema = value.get("schema")
        if schema != METRICS_SCHEMA:
            raise MetricsError(f"unsupported metrics schema: {schema!r}")
        raw_values = value.get("values", {})
        if not isinstance(raw_values, dict) or not all(isinstance(key, str) for key in raw_values):
            raise MetricsError("metrics 'values' must be an object")
        values: dict[str, bool | int | float] = {}
        for name, metric_value in raw_values.items():
            if not isinstance(metric_value, int | float):
                raise MetricsError(f"metric value {name!r} must be a boolean, integer, or number")
            values[name] = metric_value
        details = value.get("details", {})
        if not isinstance(details, dict):
            raise MetricsError("metrics 'details' must be an object")
        raw_evidence = value.get("raw_evidence", [])
        if not isinstance(raw_evidence, list):
            raise MetricsError("metrics 'raw_evidence' must be an array")
        payload = cls(
            values=values,
            details=details,
            raw_evidence=tuple(RawEvidenceRef.from_json(entry) for entry in raw_evidence),
        )
        payload.validate()
        return payload
