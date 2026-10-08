"""Versioned initial-location references remain neutral and immutable in MI inputs."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from mission.grounding_context import GroundingContextError, GroundingContextSnapshot
from mission.planning_world_evidence import (
    OBJECT_SOURCE_PLANNING_WORLD_EVIDENCE_SCHEMA,
    RESET_PLANNING_WORLD_EVIDENCE_SCHEMA,
    AuthoritativePlanningWorldEvidence,
    PlanningObjectSource,
    PlanningWorldEvidenceError,
    planning_world_review_payload,
)
from mission.responses import _with_planning_world_evidence
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression


def evidence() -> AuthoritativePlanningWorldEvidence:
    """Build a neutral source snapshot without physical executor or live position fields."""
    return AuthoritativePlanningWorldEvidence.create(
        run_id="run",
        episode_id="episode",
        scene_id="scene",
        dataset_revision="dataset",
        dataset_sha256="a" * 64,
        source_revision="reset-locations-v1",
        schema_version=OBJECT_SOURCE_PLANNING_WORLD_EVIDENCE_SCHEMA,
        object_sources=(
            PlanningObjectSource("object", "initial-location:" + "c" * 64, "sha256:" + "b" * 64),
        ),
    )


def semantic() -> AuthoritativeSemanticEvidence:
    """Keep official relocation semantics separate from initial location evidence."""
    return AuthoritativeSemanticEvidence.create(
        run_id="run",
        episode_id="episode",
        revision="goal",
        dataset_revision="dataset",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.predicate("at", ("object", "destination")),
        world_context={"scene_id": "scene", "entity_catalog": ["object", "destination"]},
    )


def test_object_sources_round_trip_and_reach_all_adapter_payloads() -> None:
    """The shared payload builder preserves exact sources and explains their limited basis."""
    original = evidence()
    assert AuthoritativePlanningWorldEvidence.from_json(original.to_json()) == original
    snapshot = GroundingContextSnapshot.create(
        "request",
        "sha256:" + "c" * 64,
        1,
        semantic_evidence=semantic(),
        planning_world_evidence=original,
    )
    assert GroundingContextSnapshot.from_json(snapshot.to_json()) == snapshot
    payload = cast(dict[str, Any], _with_planning_world_evidence({}, snapshot))
    assert payload["authoritative_planning_world_evidence"]["object_sources"] == [
        original.object_sources[0].to_json()
    ]
    guidance = payload["authoritative_planning_world_evidence"]["guidance"]
    assert guidance["initial_location_is_not_containment_or_current_placement"] is True
    assert guidance["relocation_uses_exact_supplied_source_reference"] is True
    assert "node_id" not in str(payload) and "physical_entity_id" not in str(payload)


@pytest.mark.parametrize("source", ["object", "destination"])
def test_source_cannot_alias_catalog_object_or_destination(source: str) -> None:
    """Declared source references cannot turn an existing task entity into a fabricated location."""
    with pytest.raises((PlanningWorldEvidenceError, GroundingContextError), match="source"):
        value = AuthoritativePlanningWorldEvidence.create(
            run_id="run",
            episode_id="episode",
            scene_id="scene",
            dataset_revision="dataset",
            dataset_sha256="a" * 64,
            source_revision="reset-locations-v1",
            schema_version=OBJECT_SOURCE_PLANNING_WORLD_EVIDENCE_SCHEMA,
            object_sources=(PlanningObjectSource("object", source, "sha256:" + "b" * 64),),
        )
        GroundingContextSnapshot.create(
            "request",
            "sha256:" + "c" * 64,
            1,
            semantic_evidence=semantic(),
            planning_world_evidence=value,
        )


def test_object_sources_require_semantic_catalog_and_same_snapshot() -> None:
    """Wrong objects or mixed source captures fail even when each entry has a legal digest shape."""
    with pytest.raises(GroundingContextError, match="source"):
        GroundingContextSnapshot.create(
            "request",
            "sha256:" + "c" * 64,
            1,
            semantic_evidence=AuthoritativeSemanticEvidence.create(
                run_id="run",
                episode_id="episode",
                revision="goal",
                dataset_revision="dataset",
                dataset_sha256="a" * 64,
                goal=SemanticExpression.predicate("at", ("object", "destination")),
                world_context={"scene_id": "scene", "entity_catalog": ["other"]},
            ),
            planning_world_evidence=evidence(),
        )
    with pytest.raises(PlanningWorldEvidenceError, match="one snapshot"):
        replace(
            evidence(),
            object_sources=(
                *evidence().object_sources,
                PlanningObjectSource("other", "initial-location:" + "d" * 64, "sha256:" + "d" * 64),
            ),
        )


def test_old_world_schema_does_not_accept_source_fields() -> None:
    """Historical v0.1/v0.2 inputs keep their exact schema, without implicit source upgrades."""
    with pytest.raises(PlanningWorldEvidenceError, match="schema"):
        replace(evidence(), schema_version=RESET_PLANNING_WORLD_EVIDENCE_SCHEMA)
    assert planning_world_review_payload(None) is None
    old = AuthoritativePlanningWorldEvidence.create(
        run_id="run",
        episode_id="episode",
        scene_id="scene",
        dataset_revision="dataset",
        dataset_sha256="a" * 64,
        source_revision="old-source",
    )
    assert "object_sources" not in old.to_json()
    assert AuthoritativePlanningWorldEvidence.from_json(old.to_json()) == old
