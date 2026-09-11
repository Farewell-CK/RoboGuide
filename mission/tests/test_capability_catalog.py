"""Deterministic tests for the deployment-independent canonical capability vocabulary."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog, CapabilityCatalogError
from mission.models import JSONObject, MissionPlan

CATALOG = Path("contracts/capability/v0.1/catalog.json")
CURRENT_CATALOG = Path("contracts/capability/v0.3/catalog.json")
FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
NORMALIZED_FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")


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
    catalog = CanonicalCapabilityCatalog.load(CURRENT_CATALOG)
    validated: list[Path] = []
    supported_versions = {
        "roboguide.mission-plan/v0.3",
        "roboguide.mission-plan/v0.4",
        "roboguide.mission-plan/v0.5",
        "roboguide.mission-plan/v0.6",
        "roboguide.mission-plan/v0.7",
    }
    for path in sorted(Path("scenarios").rglob("*.json")):
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict) or decoded.get("schema_version") not in supported_versions:
            continue
        catalog.validate_plan(MissionPlan.from_json(cast(JSONObject, decoded)))
        validated.append(path)

    assert validated


def test_v03_integrated_operation_requires_only_provider_level_capability() -> None:
    """An integrated semantic operation does not expose its Local How as requirements."""
    catalog = CanonicalCapabilityCatalog.load(CURRENT_CATALOG)
    plan = MissionPlan.from_json(
        cast(JSONObject, json.loads(NORMALIZED_FIXTURE.read_text(encoding="utf-8")))
    )

    catalog.validate_plan(plan)
    role = plan.tasks[0].roles[0]
    assert len(role.capabilities) == 1
    assert role.capabilities[0].contract.name == "relocate"
    assert role.capabilities[0].constraints[0].attribute == "max-payload-grams"
    assert role.execution.operation.name == "relocate"
    assert role.execution.objective.startswith("将指定急救包")


def test_v02_catalog_rejects_unknown_requirement_before_live_matching() -> None:
    """Unknown language is invalid even though current provider availability is never consulted."""
    raw = cast(JSONObject, json.loads(NORMALIZED_FIXTURE.read_text(encoding="utf-8")))
    tasks = cast(list[JSONObject], raw["tasks"])
    roles = cast(list[JSONObject], tasks[0]["roles"])
    requirements = cast(JSONObject, roles[0]["requirements"])
    capabilities = cast(list[JSONObject], requirements["capabilities"])
    capability_contract = cast(JSONObject, capabilities[0]["contract"])
    capability_contract["name"] = "magic-grasp"
    plan = MissionPlan.from_json(raw)

    with pytest.raises(CapabilityCatalogError, match="magic-grasp"):
        CanonicalCapabilityCatalog.load(CURRENT_CATALOG).validate_plan(plan)


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
