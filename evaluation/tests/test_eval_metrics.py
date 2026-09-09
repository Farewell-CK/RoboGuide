"""Tests for the canonical metric registry and metrics payload contract."""

from __future__ import annotations

import pytest
from roboguide_eval.metrics import (
    METRICS_SCHEMA,
    MetricsError,
    MetricsPayload,
    RawEvidenceRef,
    metric_registry,
    validate_metric_names,
)

E1_CORE_METRICS: tuple[str, ...] = (
    "success",
    "subgoal_success_rate",
    "simulation_steps",
    "token_usage",
    "wall_time",
    "coordination_latency",
)


def test_registry_covers_e1_core_metrics() -> None:
    """All E1 canonical metrics are registered with documented kinds."""
    registry = metric_registry()
    for name in E1_CORE_METRICS:
        assert name in registry
    assert registry["success"].value_kind == "boolean"
    assert registry["subgoal_success_rate"].value_kind == "number"
    assert registry["subgoal_success_rate"].bounds == (0.0, 1.0)
    assert registry["simulation_steps"].value_kind == "integer"
    assert registry["token_usage"].value_kind == "integer"
    assert registry["wall_time"].value_kind == "number"
    assert registry["coordination_latency"].unit == "s"


def test_registry_splits_assignment_metrics_as_reserved() -> None:
    """The vague invalid_assignment_count is replaced by reserved names."""
    registry = metric_registry()
    assert "invalid_assignment_count" not in registry
    assert "subgoal_success" not in registry
    for name in (
        "initial_infeasible_assignment_count",
        "final_infeasible_assignment_count",
        "reassignment_count",
    ):
        assert registry[name].value_kind == "integer"
        assert "reserved" in registry[name].description


def test_registry_reserves_future_experiment_metrics() -> None:
    """The registry already reserves names for E2+ experiments."""
    registry = metric_registry()
    for name in (
        "scheduling_latency",
        "recovery_latency",
        "resource_conflict_count",
        "deadline_miss_count",
        "state_freshness_seconds",
        "control_plane_cpu_seconds",
        "control_plane_memory_bytes",
    ):
        assert name in registry


def test_validate_metric_names_rejects_unknown_and_duplicates() -> None:
    """Name validation fails closed for unknown or duplicated metrics."""
    validate_metric_names(("success", "wall_time"))
    with pytest.raises(MetricsError, match="unknown canonical metric"):
        validate_metric_names(("not_a_metric",))
    with pytest.raises(MetricsError, match="duplicate canonical metric"):
        validate_metric_names(("success", "success"))


def test_payload_serialization_round_trip_preserves_values_and_evidence() -> None:
    """Payloads survive a JSON round trip including raw evidence refs."""
    payload = MetricsPayload(
        values={"success": True, "wall_time": 12.5, "token_usage": 42, "subgoal_success_rate": 0.5},
        details={"note": "fixture"},
        raw_evidence=(
            RawEvidenceRef(
                path="raw-metrics.json", description="raw output", media_type="application/json"
            ),
            RawEvidenceRef(path="stdout.log", description="stdout", media_type="text/plain"),
        ),
    )
    restored = MetricsPayload.from_json(payload.to_json())
    assert restored.values == payload.values
    assert restored.details == payload.details
    assert restored.raw_evidence == payload.raw_evidence
    document = payload.to_json()
    assert document["schema"] == METRICS_SCHEMA
    assert document["values"] == {
        "success": True,
        "wall_time": 12.5,
        "token_usage": 42,
        "subgoal_success_rate": 0.5,
    }


def test_payload_rejects_unknown_names_and_wrong_types() -> None:
    """Validation rejects unknown names and kind-mismatched value types."""
    with pytest.raises(MetricsError, match="unknown canonical metric value"):
        MetricsPayload.from_json({"schema": METRICS_SCHEMA, "values": {"bogus": 1}})
    with pytest.raises(MetricsError, match="expects integer"):
        MetricsPayload.from_json({"schema": METRICS_SCHEMA, "values": {"simulation_steps": 1.5}})
    with pytest.raises(MetricsError, match="expects boolean"):
        MetricsPayload.from_json({"schema": METRICS_SCHEMA, "values": {"success": 1}})
    with pytest.raises(MetricsError, match="must be a boolean, integer, or number"):
        MetricsPayload.from_json({"schema": METRICS_SCHEMA, "values": {"wall_time": "fast"}})


def test_payload_enforces_rate_bounds() -> None:
    """Rate metrics reject values outside their declared [0, 1] bounds."""
    with pytest.raises(MetricsError, match=r"must stay within \[0.0, 1.0\]"):
        MetricsPayload.from_json(
            {"schema": METRICS_SCHEMA, "values": {"subgoal_success_rate": 1.5}}
        )
    with pytest.raises(MetricsError, match=r"must stay within \[0.0, 1.0\]"):
        MetricsPayload.from_json(
            {"schema": METRICS_SCHEMA, "values": {"subgoal_success_rate": -0.1}}
        )
    bounded = MetricsPayload.from_json(
        {"schema": METRICS_SCHEMA, "values": {"subgoal_success_rate": 1.0}}
    )
    assert bounded.values["subgoal_success_rate"] == 1.0


def test_payload_rejects_superseded_schema_version() -> None:
    """v0.1 payloads (boolean subgoal_success) are rejected under v0.2."""
    with pytest.raises(MetricsError, match="unsupported metrics schema"):
        MetricsPayload.from_json({"schema": "roboguide-eval.metrics/v0.1", "values": {}})


def test_payload_accepts_integer_for_number_metrics() -> None:
    """Number-typed metrics accept integral values (JSON has one int type)."""
    payload = MetricsPayload.from_json({"schema": METRICS_SCHEMA, "values": {"wall_time": 3}})
    assert payload.values["wall_time"] == 3


def test_empty_payload_is_valid() -> None:
    """A failed run may produce a valid empty payload."""
    payload = MetricsPayload.empty()
    payload.validate()
    assert payload.to_json()["values"] == {}
