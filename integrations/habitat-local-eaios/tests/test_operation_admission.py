"""Offline source isolation and versioned relocation admission regressions."""

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

# Imports follow the same standalone integration-test path bootstrap as the adapter suite.
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.operation_admission import (  # noqa: E402
    OPERATION_FEASIBILITY_SCHEMA,
    attach_relocation_admission,
)
from habitat_local_eaios.preassignment_feasibility import (  # noqa: E402
    PREASSIGNMENT_FEASIBILITY_SCHEMA,
    preassignment_digest,
)
from habitat_local_eaios.relocation_preflight import preflight_relocation  # noqa: E402
from habitat_local_eaios.relocation_start import _digest  # noqa: E402
from test_relocation_start import preflight_run  # noqa: E402


def admission_sources(run: Path) -> tuple[dict[str, Any], ...]:
    """Recover the actual synthetic reset's input documents without replacing any evidence."""
    evidence = run / "evidence"
    matrix = json.loads((evidence / "preassignment-feasibility.json").read_text())
    navigation = {
        key: value for key, value in matrix.items() if key not in {"operation_admission", "digest"}
    }
    navigation["schema_version"] = PREASSIGNMENT_FEASIBILITY_SCHEMA
    navigation["digest"] = preassignment_digest(navigation)
    return (
        navigation,
        json.loads((evidence / "authoritative-semantic-evidence.json").read_text()),
        json.loads((evidence / "relocation-episode-start.json").read_text()),
        json.loads((run / "relocation-registration-profile.json").read_text()),
    )


def test_operation_source_is_separate_from_navigation_decisions(tmp_path: Path) -> None:
    """Unknown relocation routes keep exact canonical inputs and never rewrite navigation facts."""
    run = preflight_run(tmp_path)
    sources = admission_sources(run)
    before = copy.deepcopy(sources)
    matrix = attach_relocation_admission(*sources)
    assert sources == before
    assert matrix["schema_version"] == OPERATION_FEASIBILITY_SCHEMA
    assert matrix["records"] == sources[0]["records"]
    admission = matrix["operation_admission"]
    assert admission["route_reachability"] == "unknown"
    assert admission["parameter_names"] == ["destination", "object", "source"]
    assert admission["object_sources"] == {
        record["entity_id"]: record["source_entity_id"] for record in sources[2]["objects"]
    }
    assert matrix["digest"] != sources[0]["digest"]
    assert preflight_relocation(run)["deployment_admission_digest"] == matrix["digest"]


@pytest.mark.parametrize(
    "change", ["schema", "digest", "scene", "seed", "position", "node", "capacity"]
)
def test_operation_projection_rejects_cross_source_changes(tmp_path: Path, change: str) -> None:
    """Fresh body hashes cannot conceal another reset or Node source in the operation profile."""
    navigation, semantic, start, registration = admission_sources(preflight_run(tmp_path))
    if change == "schema":
        navigation["schema_version"] = "roboguide.deployment-intent-feasibility/v0.2"
    elif change == "digest":
        start["objects"][0]["source_entity_id"] = "initial-location:" + "f" * 64
    elif change == "scene":
        navigation["identity"]["scene_id"] = "another-scene"
    elif change == "seed":
        navigation["identity"]["habitat_seed"] = 41
    elif change == "position":
        navigation["initial_agent_positions"]["0"][0] += 1.0
    elif change == "node":
        navigation["records"][0]["profile"]["source_digest"] = "sha256:" + "f" * 64
    else:
        registration["agents"][0]["resource"]["capacity"] = 2
        from habitat_local_eaios.relocation_deployment import _digest as profile_digest

        registration["digest"] = profile_digest(
            {key: value for key, value in registration.items() if key != "digest"}
        )
        start["registration_profile_digest"] = registration["digest"]
        start["digest"] = _digest({key: value for key, value in start.items() if key != "digest"})
    navigation["digest"] = preassignment_digest(
        {key: value for key, value in navigation.items() if key != "digest"}
    )
    with pytest.raises(IntegrationError):
        attach_relocation_admission(navigation, semantic, start, registration)


@pytest.mark.parametrize(
    "change", ["missing", "downgrade", "source", "destination", "node", "reachability"]
)
def test_relocation_preflight_rejects_missing_or_resealed_admission(
    tmp_path: Path, change: str
) -> None:
    """The B1 launch boundary checks the actual source projection despite a recomputed digest."""
    run = preflight_run(tmp_path)
    path = run / "evidence/preassignment-feasibility.json"
    matrix = json.loads(path.read_text())
    if change == "missing":
        matrix.pop("operation_admission")
    elif change == "downgrade":
        matrix.pop("operation_admission")
        matrix["schema_version"] = PREASSIGNMENT_FEASIBILITY_SCHEMA
    elif change == "source":
        matrix["operation_admission"]["object_sources"]["object:0"] = "initial-location:" + "f" * 64
    elif change == "destination":
        matrix["operation_admission"]["destination_entities"] = ["invented-target"]
    elif change == "node":
        matrix["operation_admission"]["endpoint_profiles"][0]["node_id"] = "invented-node"
    else:
        matrix["operation_admission"]["route_reachability"] = "proven"
    matrix["digest"] = preassignment_digest(
        {key: value for key, value in matrix.items() if key != "digest"}
    )
    path.write_text(json.dumps(matrix))
    before = path.read_bytes()
    with pytest.raises(IntegrationError, match="operation admission"):
        preflight_relocation(run)
    assert path.read_bytes() == before
