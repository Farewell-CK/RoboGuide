"""Validate integrated relocation against the unchanged MI contract and deployment profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import load_settings
from mission.contract_values import MissionPlanError
from mission.execution_profile import DeploymentExecutionProfile
from mission.grounding_context import GroundingContextSnapshot
from mission.models import MissionPlan
from mission.planning_world_evidence import (
    OBJECT_SOURCE_PLANNING_WORLD_EVIDENCE_SCHEMA,
    AuthoritativePlanningWorldEvidence,
    PlanningObjectSource,
)
from mission.satisfaction_policy import validate_satisfaction_policy
from mission.semantic_evidence import (
    AuthoritativeSemanticEvidence,
    SemanticExpression,
    exact_goal_verifier_predicate,
)

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "scenarios/e1-shared-world-relocation"


@pytest.mark.parametrize("basis", ["verifier-evidence", "execution-report"])
def test_relocation_plan_keeps_sources_resources_and_official_goal(basis: str) -> None:
    """Keep exact sources; local place completion cannot satisfy the official-goal policy."""
    semantic = AuthoritativeSemanticEvidence.create(
        run_id="run-generic",
        episode_id="episode-generic",
        revision="goal-v1",
        dataset_revision="dataset-generic",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.predicate("at", ("parcel", "drop-zone")),
        world_context={"scene_id": "scene", "entity_catalog": ["parcel", "drop-zone"]},
    )
    source = "initial-location:" + "c" * 64
    planning = AuthoritativePlanningWorldEvidence.create(
        run_id="run-generic",
        episode_id="episode-generic",
        scene_id="scene",
        dataset_revision="dataset-generic",
        dataset_sha256="a" * 64,
        source_revision="reset-object-locations/v0.3",
        schema_version=OBJECT_SOURCE_PLANNING_WORLD_EVIDENCE_SCHEMA,
        object_sources=(PlanningObjectSource("parcel", source, "sha256:" + "b" * 64),),
    )
    snapshot = GroundingContextSnapshot.create(
        "request",
        "sha256:" + "d" * 64,
        1,
        semantic_evidence=semantic,
        planning_world_evidence=planning,
    )
    # This authored shape is a deterministic contract fixture, never a submitted B1 plan.
    raw: dict[str, Any] = json.loads(
        (ROOT / "scenarios/mission-front-half-v0.7/mission-plan.json").read_bytes()
    )
    raw["schema_version"] = "roboguide.mission-plan/v0.8"
    raw["contexts"][0]["executor_constraints"] = []
    raw["mission"]["objective"] = "Put the parcel at the drop zone."
    task = raw["tasks"][0]
    task["description"] = raw["mission"]["objective"]
    role = task["roles"][0]
    role["requirements"]["capabilities"][0]["constraints"] = []
    role["requirements"]["resources"] = []
    role["execution_intent"]["objective"] = task["description"]
    role["execution_intent"]["parameters"] = {
        "object": "parcel",
        "source": source,
        "destination": "drop-zone",
    }
    task["satisfaction"] = {
        "expected_effect": "The parcel is at the drop zone.",
        "basis": basis,
        "verifier": {
            "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicate": exact_goal_verifier_predicate(semantic.goal),
            "max_evidence_age_ms": 5000,
        }
        if basis == "verifier-evidence"
        else None,
    }
    plan = DeploymentExecutionProfile.load(DEPLOYMENT / "execution-profile.json").apply(
        MissionPlan.from_json(raw)
    )
    plan.validate_implementation_support()
    CanonicalCapabilityCatalog.load(ROOT / "contracts/capability/v0.3/catalog.json").validate_plan(
        plan
    )
    final_role = plan.tasks[0].roles[0]
    assert snapshot.planning_world_evidence is not None
    assert dict(final_role.execution.parameters) == {
        "object": "parcel",
        "source": snapshot.planning_world_evidence.object_sources[0].source_entity_id,
        "destination": "drop-zone",
    }
    assert [(resource.kind, resource.units) for resource in final_role.resources] == [("space", 1)]
    assert plan.contexts[0].coupling_mode == "independent"
    assert plan.contexts[0].relations == ()
    policy = load_settings(
        DEPLOYMENT / "mission-config-b1.toml", repository_root=ROOT
    ).satisfaction_policy
    if basis == "execution-report":
        with pytest.raises(MissionPlanError, match="exact authoritative joint goal"):
            validate_satisfaction_policy(plan, policy, semantic)
    else:
        validate_satisfaction_policy(plan, policy, semantic)
