"""Deterministic validation and provider-input tests for deployment planning profiles."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from mission.intent import GroundedIntent
from mission.models import JSONObject
from mission.planning_profile import (
    DeploymentPlanningProfile,
    PlanningProfileError,
)
from mission.planning_world_evidence import (
    AuthoritativePlanningWorldEvidence,
    PlanningSpatialFact,
    PlanningWorldRelation,
)
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _review_output,
    _v0_8_plan,
)

_PROFILE = Path("scenarios/e1-shared-world-episode-51/planning-profile.json")


def test_checked_in_profile_is_versioned_typed_and_catalog_bound() -> None:
    """The deployment profile exposes abstract facts without live identity fields."""
    profile = DeploymentPlanningProfile.load(_PROFILE)
    profile.validate_catalog(_current_catalog())
    payload = profile.to_json()
    assert payload["schema_version"] == "roboguide.deployment-planning-profile/v0.1"
    assert payload["authority"] == "deployment-owned"
    assert "node_id" not in json.dumps(payload)
    assert "resource_id" not in json.dumps(payload)
    assert "physical_entity" not in json.dumps(payload)
    assert {item.class_id for item in profile.capability_classes} == {
        "legged-floor-capable",
        "wheeled-single-floor",
    }


def test_profile_digest_and_catalog_validation_fail_closed(tmp_path: Path) -> None:
    """Tampering, unknown attributes, and untyped facts cannot enter MI input."""
    raw = json.loads(_PROFILE.read_text(encoding="utf-8"))
    tampered = deepcopy(raw)
    tampered["capability_classes"][0]["capabilities"][0]["attributes"][
        "supports-floor-transition"
    ] = False
    with pytest.raises(PlanningProfileError, match="digest"):
        DeploymentPlanningProfile.from_json(tampered)

    unknown_attribute = deepcopy(raw)
    unknown_attribute["capability_classes"][0]["capabilities"][0]["attributes"]["unknown"] = True
    unknown_attribute["digest"] = _digest_without_digest(unknown_attribute)
    with pytest.raises(PlanningProfileError, match="unknown attribute"):
        profile = DeploymentPlanningProfile.from_json(unknown_attribute)
        profile.validate_catalog(_current_catalog())

    invalid_contract = deepcopy(raw)
    invalid_contract["capability_classes"][0]["capabilities"][0]["contract"]["name"] = "unknown"
    invalid_contract["digest"] = _digest_without_digest(invalid_contract)
    path = tmp_path / "invalid-profile.json"
    path.write_text(json.dumps(invalid_contract), encoding="utf-8")
    with pytest.raises(PlanningProfileError, match="unknown capability"):
        profile = DeploymentPlanningProfile.load(path)
        profile.validate_catalog(_current_catalog())


def test_planner_reviewer_and_repairer_share_frozen_profile_input() -> None:
    """All model-facing Mission stages receive the same immutable profile payload."""
    profile = DeploymentPlanningProfile.load(_PROFILE)
    profile.validate_catalog(_current_catalog())
    plan_json = _v0_8_plan()
    transport = FakeTransport(
        [
            _response(_provider_plan(plan_json)),
            _response(_review_output()),
            _response(_provider_plan(plan_json)),
        ]
    )
    settings = _local_settings()
    environment = {"OPENAI_API_KEY": "test-only-key"}
    planner = ResponsesMissionPlanner(settings, environment, transport, planning_profile=profile)
    reviewer = ResponsesMissionReviewer(settings, environment, transport, planning_profile=profile)
    repairer = ResponsesMissionRepairer(settings, environment, transport, planning_profile=profile)
    mission = cast(JSONObject, plan_json["mission"])
    mission_id = cast(str, mission["id"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    grounding = _grounding()
    world_evidence = AuthoritativePlanningWorldEvidence.create(
        run_id="run-51",
        episode_id="51",
        scene_id="scene-51",
        dataset_revision="dataset-1",
        dataset_sha256="a" * 64,
        source_revision="scene-source-1",
        facts=(PlanningSpatialFact("target", "room-2", "floor-2"),),
        relations=(PlanningWorldRelation("target", "different_floor", "TARGET_target"),),
    )
    grounding = grounding.create(
        request_id=grounding.request_id,
        dialogue_digest=grounding.dialogue_digest,
        captured_at_ms=grounding.captured_at_ms,
        state_evidence=grounding.state_evidence,
        memory_evidence=grounding.memory_evidence,
        gaps=grounding.gaps,
        selection_policy_ref=grounding.selection_policy_ref,
        semantic_evidence=grounding.semantic_evidence,
        planning_world_evidence=world_evidence,
    )
    catalog = _current_catalog()

    plan = planner.plan(mission_id, intent, catalog, grounding)
    review = reviewer.review(intent, plan, catalog, grounding)
    repairer.repair(
        mission_id,
        intent,
        plan,
        MissionPlanReview.from_json(review.to_json()),
        catalog,
        grounding,
    )

    assert len(transport.requests) == 3
    for request in transport.requests:
        payload = json.loads(cast(str, request[2]["input"]))
        assert payload["deployment_planning_profile"] == profile.to_json()
        assert payload["authoritative_planning_world_evidence"] == {
            "schema_version": "roboguide.authoritative-planning-world-evidence/v0.1",
            "evidence_digest": world_evidence.evidence_digest,
            "identity": {
                "episode_id": "51",
                "scene_id": "scene-51",
                "dataset_revision": "dataset-1",
                "dataset_sha256": "a" * 64,
                "source_revision": "scene-source-1",
            },
            "facts": [
                {
                    "entity_id": "target",
                    "region_id": "room-2",
                    "floor_id": "floor-2",
                }
            ],
            "relations": [
                {
                    "subject_entity_id": "target",
                    "relation": "different_floor",
                    "object_entity_id": "TARGET_target",
                }
            ],
            "gaps": [],
            "guidance": {
                "facts_describe_world_only": True,
                "facts_do_not_select_nodes_or_physical_entities": True,
                "unknown_world_facts_must_not_be_guessed": True,
            },
        }


def _digest_without_digest(value: JSONObject) -> str:
    """Compute the profile digest for a test mutation without duplicating production code."""
    import hashlib

    body = {key: item for key, item in value.items() if key != "digest"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
