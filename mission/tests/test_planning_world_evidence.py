"""Deterministic tests for the environment-owned planning-world evidence contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from mission.grounding_context import GroundingContextSnapshot
from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONValue
from mission.planning_world_evidence import (
    AuthoritativePlanningWorldEvidence,
    PlanningSpatialFact,
    PlanningWorldEvidenceError,
    PlanningWorldGap,
    PlanningWorldRelation,
)
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression


def _evidence() -> AuthoritativePlanningWorldEvidence:
    """Create one two-entity spatial snapshot for round-trip tests."""
    return AuthoritativePlanningWorldEvidence.create(
        run_id="run-51",
        episode_id="51",
        scene_id="scene-51",
        dataset_revision="dataset-1",
        dataset_sha256="a" * 64,
        source_revision="scene-source-1",
        facts=(
            PlanningSpatialFact("TARGET_any_targets|0", "room-2", "floor-2"),
            PlanningSpatialFact("any_targets|0", "room-1", "floor-1"),
        ),
        relations=(
            PlanningWorldRelation("any_targets|0", "different_floor", "TARGET_any_targets|0"),
        ),
        gaps=(PlanningWorldGap("agent_start_state_pending_reset", "reset has not run"),),
    )


def test_planning_world_evidence_round_trips_and_orders_facts() -> None:
    """Facts are canonicalized and retain explicit pre-reset unknowns."""
    evidence = _evidence()
    restored = AuthoritativePlanningWorldEvidence.from_json(evidence.to_json())
    assert restored == evidence
    assert [fact.entity_id for fact in restored.facts] == [
        "TARGET_any_targets|0",
        "any_targets|0",
    ]
    assert restored.gaps[0].code == "agent_start_state_pending_reset"
    assert restored.relations[0].relation == "different_floor"


def test_planning_world_evidence_digest_rejects_tampering() -> None:
    """Changing a floor without changing the signed digest fails closed."""
    document = copy.deepcopy(_evidence().to_json())
    facts = cast(list[dict[str, object]], document["facts"])
    facts[0]["floor_id"] = "floor-9"
    with pytest.raises(PlanningWorldEvidenceError, match="digest"):
        AuthoritativePlanningWorldEvidence.from_json(document)


def test_planning_world_evidence_requires_exact_shape_even_with_recomputed_digest() -> None:
    """Unknown fields cannot be smuggled into the versioned evidence DTO."""
    document = copy.deepcopy(_evidence().to_json())
    cast(dict[str, object], document["identity"])["node_id"] = "node-a"
    body = {key: value for key, value in document.items() if key != "digest"}
    document["digest"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        .hexdigest()
    )
    with pytest.raises(PlanningWorldEvidenceError, match="keys mismatch"):
        AuthoritativePlanningWorldEvidence.from_json(document)


def test_planning_world_evidence_rejects_unknown_or_duplicate_relations() -> None:
    """Relation semantics are versioned and cannot be widened by provider output."""
    with pytest.raises(PlanningWorldEvidenceError, match="unsupported"):
        PlanningWorldRelation("a", "near", "b")
    with pytest.raises(PlanningWorldEvidenceError, match="distinct"):
        PlanningWorldRelation("a", "same_floor", "a")
    evidence = _evidence()
    with pytest.raises(PlanningWorldEvidenceError, match="unique"):
        AuthoritativePlanningWorldEvidence.create(
            run_id=evidence.run_id,
            episode_id=evidence.episode_id,
            scene_id=evidence.scene_id,
            dataset_revision=evidence.dataset_revision,
            dataset_sha256=evidence.dataset_sha256,
            source_revision=evidence.source_revision,
            facts=evidence.facts,
            relations=evidence.relations + evidence.relations,
            gaps=evidence.gaps,
        )


def test_grounding_context_v02_remains_compatible_and_v03_binds_world_evidence() -> None:
    """Adding planning evidence selects the explicit v0.3 context contract."""
    base = GroundingContextSnapshot.create("request", "sha256:" + "b" * 64, 1)
    assert base.schema_version == "roboguide.grounding-context/v0.2"
    assert GroundingContextSnapshot.from_json(base.to_json()) == base
    enriched = GroundingContextSnapshot.create(
        "request",
        "sha256:" + "b" * 64,
        1,
        planning_world_evidence=_evidence(),
    )
    assert enriched.schema_version == "roboguide.grounding-context/v0.3"
    assert GroundingContextSnapshot.from_json(enriched.to_json()) == enriched


def test_grounding_context_rejects_spatial_facts_from_another_world_snapshot() -> None:
    """Scene and dataset facts cannot be mixed across semantic evidence revisions."""
    semantic = AuthoritativeSemanticEvidence.create(
        run_id="run-51",
        episode_id="51",
        revision="semantic-1",
        dataset_revision="dataset-1",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.predicate("any_at", ("any_targets|0",)),
        world_context={"scene_id": "different-scene"},
    )
    with pytest.raises(
        ValueError, match="planning world scene identity does not match semantic evidence"
    ):
        GroundingContextSnapshot.create(
            "request",
            "sha256:" + "b" * 64,
            1,
            semantic_evidence=semantic,
            planning_world_evidence=_evidence(),
        )


def test_grounding_context_rejects_spatial_facts_from_another_run() -> None:
    """An otherwise identical episode artifact cannot cross a run boundary."""
    semantic = AuthoritativeSemanticEvidence.create(
        run_id="other-run",
        episode_id="51",
        revision="semantic-1",
        dataset_revision="dataset-1",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.predicate("any_at", ("any_targets|0",)),
        world_context={"scene_id": "scene-51"},
    )
    with pytest.raises(ValueError, match="planning world run identity"):
        GroundingContextSnapshot.create(
            "request",
            "sha256:" + "b" * 64,
            1,
            semantic_evidence=semantic,
            planning_world_evidence=_evidence(),
        )


class _Transport:
    """Return empty HTTP projections for fixed planning-world reader tests."""

    def get_json(self, url: str, timeout_seconds: float) -> dict[str, JSONValue]:
        """Return one valid empty State or Memory response."""
        del timeout_seconds
        if "/state/records" in url:
            return {"schema": "roboguide.state-query/v0.1", "records": []}
        return {"schema": "roboguide.memory-catalog/v0.1", "memories": []}


def test_reader_freezes_planning_world_evidence_and_records_missing_source(tmp_path: Path) -> None:
    """The fixed path is included in context identity and missing data stays explicit."""
    path = tmp_path / "planning-world.json"
    path.write_text(json.dumps(_evidence().to_json()), encoding="utf-8")
    reader = HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        1.0,
        4,
        4,
        _Transport(),
        planning_world_evidence_path=path,
    )
    dialogue = (
        DialogueTurn("turn-1", DialogueSpeaker.USER, DialogueTurnKind.INSTRUCTION, "goal", 1),
    )
    snapshot = reader.capture("request-1", dialogue, 2)
    assert snapshot.planning_world_evidence == _evidence()
    assert snapshot.schema_version == "roboguide.grounding-context/v0.3"
    path.unlink()
    missing = reader.capture("request-2", dialogue, 3)
    assert missing.planning_world_evidence is None
    assert any(gap.code == "planning_world_evidence_unavailable" for gap in missing.gaps)
