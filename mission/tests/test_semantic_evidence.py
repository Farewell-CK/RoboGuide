"""Deterministic tests for the frozen benchmark-neutral semantic evidence boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONObject, JSONValue
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.semantic_evidence import (
    AuthoritativeSemanticEvidence,
    SemanticEvidenceError,
    SemanticExpression,
)


class _Transport:
    """Return empty Control projections for fixed grounding-reader tests."""

    def get_json(self, url: str, timeout_seconds: float) -> dict[str, Any]:
        """Return valid empty State or Memory catalog responses."""
        if "/state/records" in url:
            return {"schema": "roboguide.state-query/v0.1", "records": []}
        return {"schema": "roboguide.memory-catalog/v0.1", "memories": []}


def _evidence() -> AuthoritativeSemanticEvidence:
    """Build one joint objective with two predicates and immutable world context."""
    return AuthoritativeSemanticEvidence.create(
        run_id="run-51",
        episode_id="51",
        revision="goal-revision-1",
        goal=SemanticExpression.logical(
            "and",
            (
                SemanticExpression.predicate("at", ("target-a",)),
                SemanticExpression.predicate("at", ("target-b",)),
            ),
        ),
        world_context={"scene_id": "scene-51", "agent_ids": [0, 1]},
    )


def test_joint_terminal_goal_round_trips_without_flattening() -> None:
    """The two predicates remain operands of one authoritative conjunction."""
    restored = AuthoritativeSemanticEvidence.from_json(_evidence().to_json())
    assert restored.objective_scope == "joint_terminal_state"
    assert restored.goal.kind == "logical"
    assert restored.goal.operator == "and"
    assert len(restored.goal.operands) == 2


def test_semantic_digest_rejects_goal_tampering() -> None:
    """Changing one predicate without recomputing the evidence identity fails closed."""
    document = _evidence().to_json()
    tampered = copy.deepcopy(document)
    goal = cast(JSONObject, tampered["goal"])
    operands = cast(list[JSONValue], goal["operands"])
    first_operand = cast(JSONObject, operands[0])
    first_operand["arguments"] = ["other-target"]
    with pytest.raises(SemanticEvidenceError, match="digest"):
        AuthoritativeSemanticEvidence.from_json(tampered)


def test_semantic_contract_rejects_goal_weakening_shape() -> None:
    """A flat predicate cannot masquerade as the required joint terminal objective."""
    document = _evidence().to_json()
    weakened = copy.deepcopy(document)
    goal = cast(JSONObject, weakened["goal"])
    operands = cast(list[JSONValue], goal["operands"])
    weakened["goal"] = operands[0]
    with pytest.raises(SemanticEvidenceError):
        AuthoritativeSemanticEvidence.from_json(weakened)


def test_semantic_identity_cannot_be_reused_for_another_episode() -> None:
    """Episode identity remains part of the signed semantic snapshot."""
    document = _evidence().to_json()
    identity = cast(JSONObject, document["identity"])
    identity["episode_id"] = "52"
    with pytest.raises(SemanticEvidenceError, match="digest"):
        AuthoritativeSemanticEvidence.from_json(document)


def test_reader_freezes_adapter_evidence_and_records_missing_source(tmp_path: Path) -> None:
    """The fixed path enters the snapshot; deleting it becomes an explicit source gap."""
    evidence = _evidence()
    path = tmp_path / "authoritative-semantic-evidence.json"
    path.write_text(json.dumps(evidence.to_json()), encoding="utf-8")
    reader = HttpMissionGroundingReader(
        "http://controller.test",
        "http://artifact.test",
        1.0,
        4,
        4,
        _Transport(),
        semantic_evidence_path=path,
    )
    dialogue = (
        DialogueTurn("turn-1", DialogueSpeaker.USER, DialogueTurnKind.INSTRUCTION, "goal", 1),
    )
    snapshot = reader.capture("request-1", dialogue, 2)
    assert snapshot.semantic_evidence == evidence
    path.unlink()
    missing = reader.capture("request-2", dialogue, 3)
    assert missing.semantic_evidence is None
    assert any(gap.code == "semantic_evidence_unavailable" for gap in missing.gaps)
