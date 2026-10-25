"""Synthetic SQL counterexamples independently validate topology, never device deployment facts."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.topology_recovery import WRAPPER, validate
from test_capacity_recovery import database, wrapper


def topology() -> dict[str, Any]:
    """Create one original synthetic region and exact same-reservation policy evidence."""
    result = wrapper()
    result["schema"] = WRAPPER
    control = json.loads(result["integration_json"])["control"]
    entry: dict[str, Any] = {
        "resource_id": "unit-region",
        "node_id": "unit-node",
        "kind": "Space",
        "time_domain": None,
        "space": {
            "conflict_domain": "unit-world",
            "frame": "unit-frame",
            "anchor_revision": "unit-anchor",
            "lower_mm": [0, 0, 0],
            "upper_mm": [10, 10, 10],
            "clearance_mm": 0,
        },
        "windows": [],
    }
    registry = {
        "schema": "roboguide.resource-topology/v0.1",
        "registry_id": "unit-policy",
        "revision": 1,
        "clock": "controller_receive_time",
        "stop_timeout_ms": 100,
        "booking_duration_ms": 20,
        "booking_max_age_ms": 1000,
        "resources": [entry],
    }
    control.update(resource_topology_registry=registry, resource_topology_history={"1": registry})
    control["reservations"]["unit-region"] = {
        "task_ref": {"mission_id": "unit-mission", "task_id": "unit-task"},
        "role_id": "worker",
        "group_id": "unit-group",
        "scope": "Task",
        "owner": {"Task": {"mission_id": "unit-mission", "task_id": "unit-task"}},
        "topology": {"revision": 1, "entry": entry, "admitted_at": 10, "planned_end": 30},
    }
    # Use the actual integration envelope, preserving the original independent capacity fixture.
    result["integration_json"] = json.dumps({"control": control})
    return result


def test_original_topology_sql_validation_preserves_exact_rows(tmp_path: Path) -> None:
    """Backup verification can inspect held geometry without changing original SQL authority."""
    value = topology()
    with closing(database(tmp_path / "topology.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        before = list(sql.iterdump())
        validate(sql)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing-table",
        "missing-proof",
        "revision",
        "entry",
        "frame",
        "overflow",
        "clock",
        "duplicate",
        "overlap",
        "interval",
        "old-wrapper",
    ],
)
def test_corrupt_topology_refuses_before_bundle_publication(tmp_path: Path, damage: str) -> None:
    """Original declarations, clocks and active conflicts cannot be supplied by a bundle label."""
    value = copy.deepcopy(topology())
    control = json.loads(value["integration_json"])["control"]
    registry = control["resource_topology_registry"]
    held = control["reservations"]["unit-region"]
    if damage == "missing-table":
        del control["resource_topology_history"]
    elif damage == "missing-proof":
        del held["topology"]
    elif damage == "revision":
        held["topology"]["revision"] = 2
    elif damage == "entry":
        held["topology"]["entry"]["node_id"] = "different-owner"
    elif damage == "frame":
        another = copy.deepcopy(registry["resources"][0])
        another["resource_id"] = "unit-region-z"
        another["space"]["frame"] = "untranslated"
        registry["resources"].append(another)
        control["resource_topology_history"]["1"] = registry
    elif damage == "overflow":
        registry["resources"][0]["space"].update(lower_mm=[-(1 << 63), 0, 0], clearance_mm=1)
        control["resource_topology_history"]["1"] = registry
    elif damage == "clock":
        registry["clock"] = "remote-wall-clock"
        control["resource_topology_history"]["1"] = registry
    elif damage == "duplicate":
        registry["resources"].append(copy.deepcopy(registry["resources"][0]))
        control["resource_topology_history"]["1"] = registry
    elif damage == "overlap":
        another = copy.deepcopy(held)
        another["topology"]["entry"]["resource_id"] = "unit-region-z"
        registry["resources"].append(another["topology"]["entry"])
        control["resource_topology_history"]["1"] = registry
        control["reservations"]["unit-region-z"] = another
    elif damage == "old-wrapper":
        value["schema"] = "roboguide.controller-checkpoint/v57"
    else:
        held["topology"]["planned_end"] = 9
    value["integration_json"] = json.dumps({"control": control})
    with closing(database(tmp_path / "corrupt.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (value["schema"],))
        sql.commit()
        with pytest.raises(ValueError):
            validate(sql)
