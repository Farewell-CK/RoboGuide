"""Deterministic tests for the deployment-independent canonical capability vocabulary."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog, CapabilityCatalogError
from mission.models import ExecutionIntent, JSONObject, MissionPlan

CATALOG = Path("contracts/capability/v0.1/catalog.json")
FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")


def _catalog_json() -> JSONObject:
    """Load one mutable Catalog object for malformed-artifact tests."""
    return cast(JSONObject, json.loads(CATALOG.read_text(encoding="utf-8")))


def _plan_json() -> JSONObject:
    """Load one mutable current Mission fixture for semantic-admission tests."""
    return cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))


def _first_role(value: JSONObject) -> JSONObject:
    """Return the first fixture Role while retaining a precise typed test boundary."""
    tasks = cast(list[JSONObject], value["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    return roles[0]


def test_catalog_loads_with_stable_contract_and_parameter_order() -> None:
    """Catalog parsing canonicalizes order without consulting registered Nodes."""
    catalog = CanonicalCapabilityCatalog.load(CATALOG)

    assert catalog.schema_version == "roboguide.capability-catalog/v0.1"
    assert catalog.contract_ids() == tuple(sorted(catalog.contract_ids()))
    assert "mobility.move@v1" in catalog.contract_ids()
    assert all(
        definition.parameters
        == tuple(sorted(definition.parameters, key=lambda parameter: parameter.name))
        for definition in catalog.contracts
    )
    assert catalog.to_json() == _catalog_json()


def test_catalog_covers_all_checked_in_mission_scenarios() -> None:
    """Every maintained MissionPlan fixture must use the configured semantic vocabulary."""
    catalog = CanonicalCapabilityCatalog.load(CATALOG)
    validated: list[Path] = []
    for path in sorted(Path("scenarios").rglob("*.json")):
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict) or not str(decoded.get("schema_version", "")).startswith(
            "roboguide.mission-plan/"
        ):
            continue
        tasks = cast(list[JSONObject], decoded["tasks"])
        for task_index, task in enumerate(tasks):
            roles = cast(list[JSONObject], task["roles"])
            for role_index, role in enumerate(roles):
                path_prefix = f"{path}:tasks[{task_index}].roles[{role_index}].execution"
                catalog.validate_intent(
                    ExecutionIntent.from_json(role["execution"], path_prefix),
                    path_prefix,
                )
        validated.append(path)

    assert validated


def test_catalog_rejects_duplicate_contract_identity() -> None:
    """A Catalog cannot define two conflicting meanings for one exact identity."""
    value = _catalog_json()
    contracts = cast(list[JSONObject], value["contracts"])
    contracts.append(deepcopy(contracts[0]))

    with pytest.raises(CapabilityCatalogError, match="duplicate identities"):
        CanonicalCapabilityCatalog.from_json(value)


def test_catalog_rejects_unknown_contract() -> None:
    """Syntactically canonical but unrecognized model output fails semantic admission."""
    value = _plan_json()
    role = _first_role(value)
    invented: JSONObject = {
        "namespace": "delivery",
        "name": "magic_move",
        "version": "v1",
    }
    role["contract"] = invented
    execution = cast(JSONObject, role["execution"])
    execution["capability_contract"] = invented
    plan = MissionPlan.from_json(value)

    with pytest.raises(CapabilityCatalogError, match="delivery.magic_move@v1"):
        CanonicalCapabilityCatalog.load(CATALOG).validate_plan(plan)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "missing=\\['plan'\\]"),
        ("unknown", "unknown=\\['invented'\\]"),
        ("wrong-type", "parameters.plan must be string"),
    ],
)
def test_catalog_rejects_invalid_intent_parameters(mutation: str, message: str) -> None:
    """Closed parameter definitions reject missing, invented, and mistyped values."""
    value = _plan_json()
    role = _first_role(value)
    execution = cast(JSONObject, role["execution"])
    parameters = cast(JSONObject, execution["parameters"])
    if mutation == "missing":
        del parameters["plan"]
    elif mutation == "unknown":
        parameters["invented"] = "value"
    else:
        parameters["plan"] = True
    plan = MissionPlan.from_json(value)

    with pytest.raises(CapabilityCatalogError, match=message):
        CanonicalCapabilityCatalog.load(CATALOG).validate_plan(plan)
