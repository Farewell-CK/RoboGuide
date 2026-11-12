"""Check fixed runtime acceptance math without claiming Linux process acceptance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from check_data_governance_runtime import (
    DAY_SECONDS,
    RSS_LIMIT_BYTES,
    check_disk_capacity,
    cpu_percent,
    evaluate_runtime,
    fresh_node_receipts,
    parser,
    percentile,
    validate_cgroup_limits,
)


def valid_result(**changes: Any) -> dict[str, Any]:
    """Build a complete original-duration boundary case for strict acceptance checks."""
    parameters = {
        "mode": "soak",
        "elapsed_seconds": float(DAY_SECONDS),
        "requested_seconds": float(DAY_SECONDS),
        "warmup_seconds": 3600.0,
        "peak_rss_bytes": RSS_LIMIT_BYTES,
        "baseline_rss_bytes": 100_000_000.0,
        "final_rss_bytes": 105_000_000.0,
        "cpu_average_percent": 10.0,
        "api_p95_ms": 200.0,
        "api_errors": 0,
        "node_errors": 0,
        **changes,
    }
    return evaluate_runtime(**parameters)


def test_nearest_rank_quantile_retains_thirty_sample_p95() -> None:
    """P95 is the twenty-ninth ordered result for thirty normal restarts."""
    assert percentile([float(value) for value in range(1, 31)], 0.95) == 29
    assert percentile([], 0.95) is None


def test_cpu_uses_single_core_time_and_original_process_identity() -> None:
    """Ten CPU seconds across one hundred elapsed seconds is ten percent of one core."""
    before = {"start_ticks": 100, "cpu_seconds": 1, "sample_monotonic_ns": 1_000_000_000}
    after = {"start_ticks": 100, "cpu_seconds": 11, "sample_monotonic_ns": 101_000_000_000}
    assert cpu_percent(before, after) == 10
    with pytest.raises(ValueError, match="identity"):
        cpu_percent(before, {**after, "start_ticks": 101})
    with pytest.raises(ValueError, match="clock"):
        cpu_percent(before, {**after, "sample_monotonic_ns": 0})


def test_original_user_limits_are_inclusive_at_exact_boundary() -> None:
    """The configured 24-hour, 512 MiB, five-percent, ten-percent and 200 ms limits apply."""
    assert valid_result()["accepted"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"peak_rss_bytes": RSS_LIMIT_BYTES + 1},
        {"final_rss_bytes": 105_000_001.0},
        {"cpu_average_percent": 10.00001},
        {"api_p95_ms": 200.001},
        {"api_errors": 1},
        {"node_errors": 1},
        {"warmup_seconds": 0},
        {"baseline_rss_bytes": None},
        {"final_rss_bytes": None},
        {"cpu_average_percent": None},
        {"api_p95_ms": None},
    ],
)
def test_missing_or_failed_original_gate_cannot_be_accepted(changes: dict[str, Any]) -> None:
    """No missing measurement or failed gate is rounded down or silently dropped."""
    assert valid_result(**changes)["accepted"] is False


@pytest.mark.parametrize("seconds", [1.0, 60.0, 3600.0, DAY_SECONDS - 0.001])
def test_short_soak_is_explicitly_incomplete(seconds: float) -> None:
    """A successful short smoke never becomes real 24-hour stability evidence."""
    result = valid_result(elapsed_seconds=seconds, requested_seconds=seconds)
    assert result["accepted"] is False
    assert result["complete_24_hour_acceptance"] is False
    assert result["criteria"]["postwarmup_rss_growth_le_5_percent"] is None


def test_stress_does_not_claim_two_node_or_twenty_four_hour_acceptance() -> None:
    """The separate hundred-Node stress outcome cannot substitute for two-Node acceptance."""
    result = valid_result(mode="stress", requested_seconds=300, elapsed_seconds=300)
    assert result["accepted"] is True
    assert result["complete_24_hour_acceptance"] is False
    assert result["criteria"]["actual_24_hour_duration"] is None


def test_parser_requires_explicit_binaries_and_preserves_resource_defaults() -> None:
    """Nothing is started or probed while reading explicit private workload arguments."""
    arguments = parser().parse_args(
        [
            "soak",
            "--controller",
            "/private/controller",
            "--node",
            "/private/node",
            "--output",
            "/private/new-soak",
        ]
    )
    assert arguments.duration_seconds is None  # main resolves this to exactly one day.
    assert arguments.heartbeat_ms == 1000
    assert arguments.state_interval_ms == 2000
    assert arguments.payload_bytes == 4096
    assert arguments.cpu_cores == 2
    assert arguments.address_space_mib == 2048
    assert arguments.minimum_free_mib == 2048
    assert arguments.maximum_output_mib == 2048
    assert arguments.emergency_free_mib == 512


@pytest.mark.parametrize(
    "limits",
    [
        ("max", "0", "200000 100000"),
        (str(4 * 1024**3 + 1), "0", "200000 100000"),
        (str(4 * 1024**3), "max", "200000 100000"),
        (str(4 * 1024**3), "0", "max 100000"),
        (str(4 * 1024**3), "0", "200001 100000"),
        (str(4 * 1024**3), "0", "0 100000"),
    ],
)
def test_unbounded_whole_run_cgroup_is_refused(limits: tuple[str, str, str]) -> None:
    """Hundred per-process limits cannot substitute for one finite shared resource budget."""
    with pytest.raises(ValueError, match="whole runtime cgroup"):
        validate_cgroup_limits(*limits)


def test_bounded_whole_run_cgroup_accepts_original_resource_boundary() -> None:
    """A four-GiB zero-swap two-core scope bounds the owner, source and all child Nodes."""
    result = validate_cgroup_limits(str(4 * 1024**3), "0", "200000 100000")
    assert result["memory_max_bytes"] == 4 * 1024**3
    assert result["cpu_quota_cores"] == 2


def test_output_budget_keeps_original_files_and_refuses_more_load(tmp_path: Path) -> None:
    """Budget exhaustion stops the experiment without deleting already collected SQL evidence."""
    original = tmp_path / "controller.sqlite3"
    original.write_bytes(b"original evidence")
    with pytest.raises(RuntimeError, match="disk budget"):
        check_disk_capacity(tmp_path, minimum_free_bytes=0, maximum_output_bytes=1)
    assert original.read_bytes() == b"original evidence"


def reported_record() -> dict[str, Any]:
    """Build one Controller-shaped exact source report for freshness identity tests."""
    return {
        "object": {"class": "node", "object_type": "host-status", "object_id": "software-001"},
        "source": "node:software-001/rg-host",
        "channel_id": "host-state",
        "stale": False,
        "semantic": "reported",
        "value": {
            "payload_schema": "roboguide.host-inspection/v0.1",
            "source_epoch": "original",
            "sequence": 1,
            "received_at_ms": 100,
            "valid_for_ms": 1000,
            "value": {"node_id": "software-001"},
        },
    }


@pytest.mark.parametrize(
    "failure", ["source", "channel", "epoch", "sequence", "stale", "foreign-payload"]
)
def test_wrong_node_evidence_cannot_count_as_hundred_fresh_nodes(failure: str) -> None:
    """Matching object IDs alone cannot prove original source ownership or a new report."""
    record = reported_record()
    if failure == "source":
        record["source"] = "node:foreign/rg-host"
    elif failure == "channel":
        record["channel_id"] = "foreign"
    elif failure == "epoch":
        record["value"]["source_epoch"] = ""
    elif failure == "sequence":
        record["value"]["sequence"] = 0
    elif failure == "stale":
        record["stale"] = True
    else:
        record["value"]["value"]["node_id"] = "foreign"
    assert fresh_node_receipts({"records": [record], "observed_at_ms": 101}) == {}


def test_fresh_node_receipt_is_exact_and_ambiguity_is_refused() -> None:
    """A single source counts once; duplicate same-name reports are never silently selected."""
    record = reported_record()
    assert fresh_node_receipts({"records": [record], "observed_at_ms": 101}) == {
        "software-001": 100
    }
    with pytest.raises(ValueError, match="ambiguous"):
        fresh_node_receipts({"records": [record, deepcopy(record)], "observed_at_ms": 101})


@pytest.mark.parametrize("now,ttl", [(99, 1000), (1100, 1000), (101, 0), (101, True), (None, 1000)])
def test_state_ttl_and_controller_time_are_checked_independently(now: Any, ttl: Any) -> None:
    """A false stale label cannot make expired or future timestamps valid runtime evidence."""
    record = reported_record()
    record["value"]["valid_for_ms"] = ttl
    assert fresh_node_receipts({"records": [record], "observed_at_ms": now}) == {}
