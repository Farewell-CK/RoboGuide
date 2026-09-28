"""Offline contract tests for the official shared-world verifier producer."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.shared_world import SharedWorldCoordinator  # noqa: E402
from habitat_local_eaios.task_verifier import (  # noqa: E402
    build_task_verifier_source,
    build_task_verifier_verdict,
    canonical_goal_predicate,
)


def _digest(value: object) -> str:
    """Compute the artifact digest used by the real producer."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _semantic() -> dict[str, Any]:
    """Provide one complete environment-owned conjunction without simulator I/O."""
    body: dict[str, Any] = {
        "schema_version": "roboguide.authoritative-semantic-evidence/v0.2",
        "authority": "environment-authoritative",
        "identity": {
            "run_id": "run-1",
            "episode_id": "51",
            "revision": "goal-1",
            "dataset_revision": "dataset-1",
            "dataset_sha256": "a" * 64,
        },
        "objective_scope": "joint_terminal_state",
        "goal": {
            "kind": "logical",
            "operator": "and",
            "operands": [
                {"kind": "predicate", "name": "any_at", "arguments": ["any_targets|0"]},
                {
                    "kind": "predicate",
                    "name": "any_at",
                    "arguments": ["TARGET_any_targets|0"],
                },
            ],
            "quantifier": None,
            "variables": [],
        },
        "world_context": {"scene_id": "scene.glb", "agent_ids": [0, 1], "entity_catalog": []},
    }
    return {**body, "digest": _digest(body)}


def _invocation(*, attempt_id: str | None = "attempt-a") -> CanonicalMobilityInvocation:
    """Build one exact local execution with an optional physical attempt identity."""
    return CanonicalMobilityInvocation(
        mission_id="mission-1",
        task_id="task-1",
        group_id="group-1",
        role_id="role-1",
        operation="mobility.move@v1",
        objective="reach the joint terminal goal",
        parameters={"destination": "TARGET_any_targets|0"},
        resource_ids=("space-a",),
        attempt_id=attempt_id,
    )


def test_exact_authoritative_goal_is_the_only_advertised_predicate() -> None:
    """Publish the goal string actually used by the accepted Episode51 plan."""
    source = build_task_verifier_source(_semantic())
    assert source["supported_predicates"] == [
        "any_at(any_targets|0) AND any_at(TARGET_any_targets|0)"
    ]
    assert source["identity"]["scene_id"] == "scene.glb"
    assert source["source_revision"] == _semantic()["digest"]
    fixture = json.loads(
        (INTEGRATION_ROOT / "tests/fixtures/task-verifier-source.json").read_text(encoding="utf-8")
    )
    assert source == fixture


def test_unknown_goal_syntax_advertises_no_verifier_capability() -> None:
    """An unrenderable PDDL expression cannot be guessed or silently simplified."""
    semantic = _semantic()
    semantic["goal"]["operator"] = "forall"
    semantic["digest"] = _digest({key: item for key, item in semantic.items() if key != "digest"})
    assert build_task_verifier_source(semantic)["supported_predicates"] == []
    assert canonical_goal_predicate(semantic["goal"]) is None


def test_source_rejects_tampered_or_missing_dataset_identity() -> None:
    """Source enrollment fails before any verdict when world identity is inconsistent."""
    semantic = _semantic()
    semantic["world_context"]["scene_id"] = "other.glb"
    with pytest.raises(IntegrationError, match="digest mismatch"):
        build_task_verifier_source(semantic)
    semantic = _semantic()
    semantic["identity"]["dataset_revision"] = ""
    semantic["digest"] = _digest({key: item for key, item in semantic.items() if key != "digest"})
    with pytest.raises(IntegrationError, match="incomplete"):
        build_task_verifier_source(semantic)


@pytest.mark.parametrize("metric", [True, False])
def test_strict_official_result_is_bound_to_actual_attempt(metric: bool) -> None:
    """Only a strict official bool produces a positive or final negative verdict."""
    source = build_task_verifier_source(_semantic())
    verdict = build_task_verifier_verdict(
        source, {"pddl_success": metric}, [_invocation()], source_observed_at_ms=123
    )
    assert verdict is not None
    assert verdict["satisfied"] is metric
    assert verdict["tasks"] == [
        {
            "mission_id": "mission-1",
            "task_id": "task-1",
            "attempts": [{"role_id": "role-1", "attempt_id": "attempt-a"}],
        }
    ]
    assert verdict["source_digest"] == source["digest"]
    if metric:
        fixture = json.loads(
            (INTEGRATION_ROOT / "tests/fixtures/task-verifier-verdict.json").read_text(
                encoding="utf-8"
            )
        )
        expected = build_task_verifier_verdict(
            source, {"pddl_success": True}, [_invocation()], source_observed_at_ms=123
        )
        assert expected == fixture


def test_missing_metric_or_attempt_yields_no_verdict() -> None:
    """Local skill completion and absent official metrics cannot satisfy a Task."""
    source = build_task_verifier_source(_semantic())
    for metrics, invocation in [
        ({"pddl_success": 1}, _invocation()),
        ({}, _invocation()),
        ({"pddl_success": True}, _invocation(attempt_id=None)),
    ]:
        assert build_task_verifier_verdict(source, metrics, [invocation]) is None


def test_attempt_identity_changes_local_invocation_key() -> None:
    """A recovered physical attempt cannot reuse an earlier adapter idempotency key."""
    first = _invocation()
    second = _invocation(attempt_id="attempt-b")
    assert first.request_key() != second.request_key()
    assert CanonicalMobilityInvocation.from_request({"invocation": second.as_dict()}) == second


def test_shared_world_publishes_verdict_before_local_terminal(tmp_path: Path) -> None:
    """The actual coordinator writer retains an exact, immutable final verdict artifact."""
    semantic = _semantic()
    source = build_task_verifier_source(semantic)
    (tmp_path / "authoritative-semantic-evidence.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
    (tmp_path / "task-verifier-source.json").write_text(json.dumps(source), encoding="utf-8")
    coordinator = object.__new__(SharedWorldCoordinator)
    coordinator._evidence_dir = tmp_path
    coordinator._publish_verifier_verdict_best_effort(
        {"official_metrics": {"pddl_success": True}}, [_invocation()]
    )
    artifact = json.loads((tmp_path / "task-verifier-verdict.json").read_text(encoding="utf-8"))
    assert artifact["satisfied"] is True
    assert artifact["source_digest"] == source["digest"]
    assert artifact["tasks"][0]["attempts"][0]["attempt_id"] == "attempt-a"


def test_shared_world_does_not_publish_without_official_metric(tmp_path: Path) -> None:
    """A missing metric cannot be synthesized from local completion or diagnostics."""
    semantic = _semantic()
    (tmp_path / "authoritative-semantic-evidence.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
    (tmp_path / "task-verifier-source.json").write_text(
        json.dumps(build_task_verifier_source(semantic)), encoding="utf-8"
    )
    coordinator = object.__new__(SharedWorldCoordinator)
    coordinator._evidence_dir = tmp_path
    coordinator._publish_verifier_verdict_best_effort({"official_metrics": {}}, [_invocation()])
    assert not (tmp_path / "task-verifier-verdict.json").exists()


def test_verifier_artifact_failure_does_not_change_local_outcome(tmp_path: Path) -> None:
    """An unavailable source is evidence loss, never a simulator or Node terminal rewrite."""
    coordinator = object.__new__(SharedWorldCoordinator)
    coordinator._evidence_dir = tmp_path
    coordinator._publish_verifier_verdict_best_effort(
        {"official_metrics": {"pddl_success": True}}, [_invocation()]
    )
    assert not (tmp_path / "task-verifier-verdict.json").exists()
