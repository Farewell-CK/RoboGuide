"""Deterministic checks for deployment-published MI execution evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from mission.execution_planning_evidence import (
    EXECUTION_PLANNING_SCHEMA,
    LEGACY_EXECUTION_PLANNING_SCHEMA,
    ExecutionPlanningEvidence,
)
from mission.models import JSONObject, MissionPlan, MissionPlanError
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression

FIXTURE = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
_PREDICATE = "object first-aid-kit-2f is in reception-1f"


def _digest(value: object) -> str:
    """Return the repository's canonical digest for a test artifact body."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _semantic() -> AuthoritativeSemanticEvidence:
    """Build one frozen world identity shared by the artifact and plan tests."""
    return AuthoritativeSemanticEvidence.create(
        run_id="run-evidence",
        episode_id="episode-evidence",
        revision="semantic-revision",
        dataset_revision="dataset-revision",
        dataset_sha256="a" * 64,
        goal=SemanticExpression.predicate("at", ("target",)),
        world_context={
            "scene_id": "scene-evidence",
            "agent_ids": ["agent-0"],
            "entity_catalog": [],
        },
    )


def _evidence(
    *, endpoint_count: int = 2
) -> tuple[ExecutionPlanningEvidence, AuthoritativeSemanticEvidence]:
    """Build a digest-bound terminal verifier and capacity declaration."""
    semantic = _semantic()
    source_body: JSONObject = {
        "schema_version": "roboguide.task-verifier-source/v0.1",
        "source_id": "habitat-official-pddl",
        "source_revision": semantic.evidence_digest,
        "identity": {
            "run_id": semantic.run_id,
            "episode_id": semantic.episode_id,
            "scene_id": "scene-evidence",
            "dataset_revision": semantic.dataset_revision,
            "dataset_sha256": semantic.dataset_sha256,
        },
        "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
        "supported_predicates": [_PREDICATE],
        "verdict_finality": "terminal",
    }
    source = {**source_body, "digest": _digest(source_body)}
    body: JSONObject = {
        "schema_version": EXECUTION_PLANNING_SCHEMA,
        "authority": "deployment-published",
        "identity": {
            "run_id": semantic.run_id,
            "episode_id": semantic.episode_id,
            "scene_id": "scene-evidence",
            "dataset_revision": semantic.dataset_revision,
            "dataset_sha256": semantic.dataset_sha256,
            "semantic_evidence_digest": semantic.evidence_digest,
        },
        "verifier_source": source,
        "execution_lifetime": {
            "declaration_digest": "sha256:" + "b" * 64,
            "configured_endpoint_count": endpoint_count,
            "endpoint_capacity": 1,
            "exclusive_resource_kind": "space",
            "max_slots": 32,
            "resource_release": "after-task-satisfaction",
            "verifier_publication": "after-all-session-slots",
        },
        "execution_topology": {
            "supported_coupling_modes": ["independent"],
            "supports_task_dependencies": True,
            "supports_context_relations": False,
            "actor_binding": "session-stable",
        },
    }
    return ExecutionPlanningEvidence.from_json({**body, "digest": _digest(body)}), semantic


def _plan(task_count: int) -> MissionPlan:
    """Clone the checked-in verifier plan while retaining all logical task slots."""
    raw = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    mission = cast(dict[str, Any], raw["mission"])
    context = cast(list[dict[str, Any]], raw["contexts"])[0]
    task = cast(list[dict[str, Any]], raw["tasks"])[0]
    mission["actors"] = []
    raw["tasks"] = []
    for index in range(task_count):
        actor_id = f"courier-{index}"
        role_context = f"carrier-{index}"
        mission["actors"].append({"id": actor_id})
        copied_context = copy.deepcopy(context)
        copied_context["id"] = f"delivery-context-{index}"
        copied_context["roles"][0] = {"id": role_context, "actor": actor_id}
        cast(list[dict[str, Any]], raw.setdefault("contexts", [])).append(copied_context)
        copied_task = copy.deepcopy(task)
        copied_task["id"] = f"relocate-aid-kit-{index}"
        copied_task["context_id"] = copied_context["id"]
        copied_task["roles"][0]["context_role"] = role_context
        copied_task["satisfaction"]["verifier"]["predicate"] = _PREDICATE
        cast(list[dict[str, Any]], raw["tasks"]).append(copied_task)
    # The fixture context is replaced by the generated contexts above.
    raw["contexts"] = raw["contexts"][1:]
    return MissionPlan.from_json(raw)


def test_published_verifier_and_world_identity_are_bound() -> None:
    """A valid source is accepted only for its exact frozen semantic snapshot."""
    evidence, semantic = _evidence()
    evidence.validate_world(semantic)
    evidence.validate_plan(_plan(1))


def test_legacy_evidence_remains_readable_without_inventing_topology_support() -> None:
    """Historical artifacts retain their original bytes and do not acquire v0.2 declarations."""
    document = _evidence()[0].to_json()
    document["schema_version"] = LEGACY_EXECUTION_PLANNING_SCHEMA
    document.pop("execution_topology")
    document["digest"] = _digest({key: value for key, value in document.items() if key != "digest"})
    restored = ExecutionPlanningEvidence.from_json(document)
    assert restored.to_json() == document
    assert "execution_topology" not in restored.to_json()
    restored.validate_plan(_plan(2))


@pytest.mark.parametrize(
    "fault", ["missing", "unknown_mode", "duplicate_mode", "wrong_type", "inventory", "schema"]
)
def test_topology_declarations_fail_closed_even_with_recomputed_digest(fault: str) -> None:
    """Digest validity cannot legitimize missing, invented or inventory-shaped support."""
    document = _evidence()[0].to_json()
    topology = cast(JSONObject, document["execution_topology"])
    if fault == "missing":
        document.pop("execution_topology")
    elif fault == "unknown_mode":
        topology["supported_coupling_modes"] = ["automatically-repaired"]
    elif fault == "duplicate_mode":
        topology["supported_coupling_modes"] = ["independent", "independent"]
    elif fault == "wrong_type":
        topology["supports_task_dependencies"] = 1
    elif fault == "inventory":
        topology["node_id"] = "untrusted-selector"
    else:
        document["schema_version"] = "roboguide.execution-planning-evidence/v9"
    document["digest"] = _digest({key: value for key, value in document.items() if key != "digest"})
    with pytest.raises(ValueError):
        ExecutionPlanningEvidence.from_json(document)


def test_wrong_world_identity_fails_closed() -> None:
    """An artifact from another reset cannot silently become MI input."""
    evidence, semantic = _evidence()
    other = AuthoritativeSemanticEvidence.create(
        run_id=semantic.run_id,
        episode_id=semantic.episode_id,
        revision=semantic.revision,
        dataset_revision=semantic.dataset_revision,
        dataset_sha256=semantic.dataset_sha256,
        goal=semantic.goal,
        world_context={"scene_id": "different-scene", "agent_ids": [], "entity_catalog": []},
    )
    with pytest.raises(ValueError, match="differs from the frozen semantic world"):
        evidence.validate_world(other)


def test_non_authoritative_verifier_source_fails_closed() -> None:
    """A digest-valid imitation cannot replace the deployment's official verifier source."""
    evidence, _ = _evidence()
    document = evidence.to_json()
    source = cast(JSONObject, document["verifier_source"])
    source["source_id"] = "model-inferred-pddl"
    source["digest"] = _digest({key: value for key, value in source.items() if key != "digest"})
    document["digest"] = _digest({key: value for key, value in document.items() if key != "digest"})
    with pytest.raises(ValueError, match="not Habitat official PDDL"):
        ExecutionPlanningEvidence.from_json(document)


def test_terminal_verifier_capacity_rejects_too_many_roles_in_one_task() -> None:
    """A two-endpoint profile rejects a Task requiring three simultaneous slots."""
    evidence, _ = _evidence(endpoint_count=2)
    raw = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    context = cast(list[dict[str, Any]], raw["contexts"])[0]
    task = cast(list[dict[str, Any]], raw["tasks"])[0]
    for index in range(1, 3):
        actor_id = f"courier-{index}"
        context["roles"].append({"id": f"carrier-{index}", "actor": actor_id})
        role = copy.deepcopy(task["roles"][0])
        role["id"] = f"delivery-role-{index}"
        role["context_role"] = f"carrier-{index}"
        task["roles"].append(role)
        cast(list[dict[str, Any]], cast(dict[str, Any], raw["mission"])["actors"]).append(
            {"id": actor_id}
        )
    with pytest.raises(MissionPlanError, match="requires 3 units"):
        evidence.validate_plan(MissionPlan.from_json(raw))


def test_context_scoped_resource_is_counted_once_for_one_task() -> None:
    """A shared Context resource is not mistaken for one endpoint per Role."""
    evidence, _ = _evidence(endpoint_count=2)
    raw = cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))
    task = cast(list[dict[str, Any]], raw["tasks"])[0]
    for index in range(1, 3):
        role = copy.deepcopy(task["roles"][0])
        role["id"] = f"delivery-role-{index}"
        role["context_role"] = "carrier"
        role["resource_scope"] = "context"
        task["roles"].append(role)
    cast(dict[str, Any], task["roles"][0])["resource_scope"] = "context"
    task["satisfaction"]["basis"] = "execution-report"
    task["satisfaction"]["verifier"] = None
    evidence.validate_plan(MissionPlan.from_json(raw))


def test_terminal_verifier_capacity_allows_independent_task_reuse() -> None:
    """Final-world Tasks retain endpoints even when they are in different Tasks."""
    evidence, _ = _evidence(endpoint_count=2)
    evidence.validate_plan(_plan(1))
    evidence.validate_plan(_plan(2))
    with pytest.raises(MissionPlanError, match="terminal verifier retains 3 slots"):
        evidence.validate_plan(_plan(3))


def test_ordinary_tasks_can_reuse_a_single_endpoint() -> None:
    """Ordinary completion retains the existing serial resource release contract."""
    evidence, _ = _evidence(endpoint_count=1)
    raw = _plan(3).to_json()
    tasks = cast(list[JSONObject], raw["tasks"])
    for task in tasks:
        satisfaction = cast(JSONObject, task["satisfaction"])
        satisfaction["basis"] = "execution-report"
        satisfaction["verifier"] = None
    evidence.validate_plan(MissionPlan.from_json(raw))


def test_adding_dependencies_cannot_unlock_a_final_world_verifier() -> None:
    """A predecessor awaiting world closure cannot satisfy a dependent Task's DAG edge."""
    evidence, _ = _evidence()
    raw = _plan(2).to_json()
    tasks = cast(list[JSONObject], raw["tasks"])
    tasks[1]["depends_on"] = [tasks[0]["id"]]
    with pytest.raises(MissionPlanError, match="before a dependent Task"):
        evidence.validate_plan(MissionPlan.from_json(raw))
