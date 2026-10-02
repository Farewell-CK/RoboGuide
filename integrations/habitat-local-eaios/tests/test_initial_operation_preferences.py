"""Deterministic neutral projection and archival-failure checks without Habitat."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import initial_operation_preferences as preferences  # noqa: E402
from habitat_local_eaios.preassignment_feasibility import preassignment_digest  # noqa: E402


def _seal(document: dict[str, Any]) -> None:
    """Recompute fixture identity to test semantic validation beyond the checksum."""
    document["digest"] = preassignment_digest(
        {key: value for key, value in document.items() if key != "digest"}
    )


def _sources() -> tuple[dict[str, Any], dict[str, Any]]:
    """Provide four exact costs with a positive witness, a miss and unavailability."""
    matrix: dict[str, Any] = {
        "schema_version": "roboguide.deployment-intent-feasibility/v0.3",
        "identity": {"run_id": "new-run", "episode_reset_count": 1},
        "initial_agent_positions": {"0": [0.0, 0.0, 0.0], "1": [1.0, 0.0, 0.0]},
        "records": [
            {
                "operation": "mobility.navigate@v1",
                "destination": goal,
                "node_id": node,
                "agent_id": agent,
            }
            for goal in ("object", "location")
            for agent, node in enumerate(("node-a", "node-b"))
        ],
    }
    _seal(matrix)
    routes: dict[str, Any] = {
        "schema_version": "roboguide.deployment-reset-route-support/v0.1",
        "purpose": "diagnostic_only",
        "identity": {**matrix["identity"], "preassignment_digest": matrix["digest"]},
        "initial_agent_positions": copy.deepcopy(matrix["initial_agent_positions"]),
        "scope_status": "available",
        "records": [
            {
                "node_id": record["node_id"],
                "agent_id": record["agent_id"],
                "destination": record["destination"],
                "status": status,
                "selection": {"path_length_m": 1.234567} if status == "supported" else None,
            }
            for record, status in zip(
                matrix["records"],
                ("supported", "not_found", "unavailable", "supported"),
                strict=True,
            )
        ],
    }
    _seal(routes)
    return routes, matrix


def test_projection_preserves_unknowns_and_does_not_change_sources() -> None:
    """Null cost cannot become a negative filter or a plan/Actor assignment."""
    routes, matrix = _sources()
    before = copy.deepcopy((routes, matrix))
    output = preferences.build_initial_operation_preferences(routes, matrix)
    assert (routes, matrix) == before
    assert output["schema_version"] == preferences.SCHEMA
    costs = {
        (item["node_id"], item["parameters"]["destination"]): item["cost_micrometers"]
        for item in output["records"]
    }
    assert costs == {
        ("node-a", "object"): 1_234_567,
        ("node-b", "object"): None,
        ("node-a", "location"): None,
        ("node-b", "location"): 1_234_567,
    }
    assert output["source_digest"] == routes["digest"]
    assert output["feasibility_digest"] == matrix["digest"]
    assert "actors" not in output and "mission_plan" not in output


def test_unavailable_scope_is_neutral() -> None:
    """An unavailable complete probe never excludes the deployment's endpoints."""
    routes, matrix = _sources()
    routes.update(scope_status="unavailable", records=[])
    _seal(routes)
    output = preferences.build_initial_operation_preferences(routes, matrix)
    assert len(output["records"]) == 4
    assert all(item["cost_micrometers"] is None for item in output["records"])


def test_geometry_projection_preserves_unknown_and_static_miss_as_preferences() -> None:
    """Static disjoint is advisory, a geometric intersection alone has no route cost."""
    routes, matrix = _sources()
    routes["schema_version"] = "roboguide.deployment-reset-route-support/v0.2"
    for record, status in zip(
        routes["records"], ("intersects", "disjoint", "intersects", "unknown"), strict=True
    ):
        record["region_analysis"] = {"status": status, "complete": True}
    _seal(routes)
    before = copy.deepcopy((routes, matrix))
    output = preferences.build_initial_operation_preferences(routes, matrix)
    assert output["schema_version"] == preferences.GEOMETRY_SCHEMA
    assert [r["static_support"] for r in output["records"]] == [
        "unknown",
        "witnessed",
        "witnessed",
        "static-disjoint",
    ]
    assert len(output["records"]) == 4 and (routes, matrix) == before
    assert all("excluded" not in r for r in output["records"])
    routes["records"][1]["region_analysis"]["complete"] = False
    _seal(routes)
    with pytest.raises(ValueError, match="complete miss"):
        preferences.build_initial_operation_preferences(routes, matrix)


def test_full_entity_catalog_and_operation_aliases_keep_unprobed_costs_unknown() -> None:
    """The matrix covers all entities/operations; the probe covers only official goals."""
    routes, matrix = _sources()
    catalog = [{**record, "destination": "unprobed-entity"} for record in matrix["records"][:2]]
    matrix["records"].extend(catalog)
    matrix["records"].extend(
        {**record, "operation": "mobility.move@v1"} for record in copy.deepcopy(matrix["records"])
    )
    _seal(matrix)
    routes["identity"]["preassignment_digest"] = matrix["digest"]
    _seal(routes)
    output = preferences.build_initial_operation_preferences(routes, matrix)
    assert len(output["records"]) == 12
    assert all(
        record["cost_micrometers"] is None
        for record in output["records"]
        if record["parameters"]["destination"] == "unprobed-entity"
    )
    known = [record for record in output["records"] if record["cost_micrometers"] is not None]
    assert len(known) == 4
    assert {record["operation"] for record in known} == {"mobility.move@v1", "mobility.navigate@v1"}


@pytest.mark.parametrize(
    "fault", ["identity", "endpoint", "missing", "duplicate", "schema", "digest"]
)
def test_projection_rejects_invalid_sources(fault: str) -> None:
    """A valid recomputed checksum cannot excuse wrong identities or coverage."""
    routes, matrix = _sources()
    if fault == "identity":
        routes["identity"]["run_id"] = "other-run"
    elif fault == "endpoint":
        routes["records"][0]["agent_id"] = 9
    elif fault == "missing":
        routes["records"].pop()
    elif fault == "duplicate":
        routes["records"].append(copy.deepcopy(routes["records"][0]))
    elif fault == "schema":
        routes["schema_version"] = "wrong"
    _seal(routes)
    if fault == "digest":
        routes["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        preferences.build_initial_operation_preferences(routes, matrix)


@pytest.mark.parametrize("cost", [True, -1, float("inf"), "1", 1e10])
def test_projection_rejects_false_or_unbounded_costs(cost: Any) -> None:
    """Only bounded numeric positive-witness lengths become integer cost hints."""
    routes, matrix = _sources()
    routes["records"][0]["selection"]["path_length_m"] = cost
    if cost != float("inf"):
        _seal(routes)
    with pytest.raises(ValueError):
        preferences.build_initial_operation_preferences(routes, matrix)


def test_cli_never_overwrites_existing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requested projection failures preserve the original sources and output."""
    routes, matrix = _sources()
    source = tmp_path / "routes.json"
    feasibility = tmp_path / "matrix.json"
    output = tmp_path / "preferences.json"
    source.write_text(json.dumps(routes), encoding="utf-8")
    feasibility.write_text(json.dumps(matrix), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preferences",
            "--route-support",
            str(source),
            "--feasibility",
            str(feasibility),
            "--output",
            str(output),
        ],
    )
    preferences.main()
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        preferences.main()
    assert output.read_bytes() == before
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        preferences._load(oversized)
