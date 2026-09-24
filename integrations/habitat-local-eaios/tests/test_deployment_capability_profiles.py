"""Deterministic checks for known Habitat deployment capability declarations."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import JSONObject, MissionPlan

_INTEGRATION_ROOT = Path(__file__).parents[1]
if str(_INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation  # noqa: E402
from habitat_local_eaios.stage2_contract import (  # noqa: E402
    Stage2ContractViolation,
    Stage2ExecutionContract,
)

_ROOT = Path(__file__).parents[3]
_SPOT_PROFILES = (
    _ROOT / "scenarios/e1-shared-world-episode-51/node-a.toml",
    _ROOT / "scenarios/habitat-local-eaios-c1-s0/node.toml",
    _ROOT / "scenarios/habitat-local-eaios-c1-s0/node-cancel.toml",
    _ROOT / "scenarios/habitat-local-eaios-c1-s0b/node.toml",
)
_FETCH_PROFILES = (_ROOT / "scenarios/e1-shared-world-episode-51/node-b.toml",)


def _capability_attributes(path: Path) -> dict[str, dict[str, Any]]:
    """Read capability profile attributes from one deployment-owned Node config."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    profiles = document["capability_profiles"]
    return {profile["contract"]: profile["attributes"] for profile in profiles}


def test_known_spot_profiles_advertise_legged_floor_transition_support() -> None:
    """Known Spot deployment profiles expose the facts needed for future matching constraints."""
    for path in _SPOT_PROFILES:
        profiles = _capability_attributes(path)
        mobility_profiles = {
            name: attrs for name, attrs in profiles.items() if name.startswith("mobility.")
        }
        assert "mobility.navigate@v1" in mobility_profiles
        for attrs in mobility_profiles.values():
            assert attrs == {
                "base-type": "legged",
                "supports-floor-transition": True,
            }


def test_known_fetch_profile_advertises_single_floor_mobility() -> None:
    """The E1 Fetch profile declares its single-floor deployment limitation."""
    profiles = _capability_attributes(_FETCH_PROFILES[0])
    for contract in ("mobility.navigate@v1", "mobility.move@v1"):
        assert profiles[contract] == {
            "base-type": "wheeled",
            "supports-floor-transition": False,
        }


def test_catalog_plan_and_stage2_guard_keep_distinct_authorities() -> None:
    """A checked-in MissionPlan binds each canonical destination without inferring placement."""
    plan_path = _ROOT / "scenarios/e1-shared-world-episode-51/mission-plan.json"
    plan = MissionPlan.from_json(
        cast(JSONObject, json.loads(plan_path.read_text(encoding="utf-8")))
    )
    CanonicalCapabilityCatalog.load(_ROOT / "contracts/capability/v0.3/catalog.json").validate_plan(
        plan
    )
    assert len(plan.tasks) == 2
    destinations: list[str] = []
    contracts: list[Stage2ExecutionContract] = []
    for task in plan.tasks:
        role = task.roles[0]
        assert len(role.capabilities) == 1
        assert role.capabilities[0].constraints == ()
        assert [(resource.kind, resource.units) for resource in role.resources] == [("space", 1)]
        operation = role.execution.operation
        invocation = CanonicalMobilityInvocation(
            mission_id=plan.mission.mission_id,
            task_id=task.task_id,
            group_id="group-offline-contract-test",
            role_id=role.role_id,
            operation=f"{operation.namespace}.{operation.name}@{operation.version}",
            objective=role.execution.objective,
            parameters=dict(role.execution.parameters),
            resource_ids=("space-offline-test",),
        )
        contract = Stage2ExecutionContract.for_invocation(invocation)
        destination = invocation.destination
        assert contract.expected_destination == destination
        destinations.append(destination)
        contracts.append(contract)
    assert len(set(destinations)) == 2
    for own, other in ((0, 1), (1, 0)):
        contracts[own].validate(
            "agent",
            {"name": "nav_to_obj", "arguments": {"target_obj": destinations[own]}},
            frozenset({"agent"}),
        )
        with pytest.raises(Stage2ContractViolation, match="canonical destination"):
            contracts[own].validate(
                "agent",
                {"name": "nav_to_obj", "arguments": {"target_obj": destinations[other]}},
                frozenset({"agent"}),
            )
    # Node facts are real registration data; the static example does not invent
    # per-task floor requirements, so Control remains the placement authority.
    spot = _capability_attributes(_SPOT_PROFILES[0])
    fetch = _capability_attributes(_FETCH_PROFILES[0])
    for operation_id in ("mobility.move@v1", "mobility.navigate@v1"):
        assert spot[operation_id]["supports-floor-transition"] is True
        assert fetch[operation_id]["supports-floor-transition"] is False
