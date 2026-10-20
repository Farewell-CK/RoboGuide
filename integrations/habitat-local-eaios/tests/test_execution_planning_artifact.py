"""Deterministic checks for the deployment-published MI planning artifact."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from habitat_local_eaios.endpoint_registry import build_endpoint_registry
from habitat_local_eaios.execution_planning_artifact import build
from habitat_local_eaios.model import IntegrationError
from habitat_local_eaios.task_verifier import build_task_verifier_source

ROOT = Path(__file__).resolve().parents[3]
NODE = ROOT / "scenarios/e1-shared-world-perception/node-a.toml"


def _digest(value: object) -> str:
    """Hash one strict semantic fixture using the cross-language encoding."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _semantic() -> dict[str, object]:
    """Create a small authoritative reset artifact accepted by the verifier builder."""
    body: dict[str, object] = {
        "schema_version": "roboguide.authoritative-semantic-evidence/v0.2",
        "authority": "environment-authoritative",
        "identity": {
            "run_id": "run-artifact",
            "episode_id": "64",
            "revision": "reset-1",
            "dataset_revision": "dataset-1",
            "dataset_sha256": "a" * 64,
        },
        "objective_scope": "joint_terminal_state",
        "goal": {
            "kind": "predicate",
            "name": "any_at",
            "arguments": ["target|0"],
        },
        "world_context": {"scene_id": "scene.glb", "agent_ids": ["agent-0"]},
    }
    return {**body, "digest": _digest(body)}


def test_artifact_binds_verifier_and_registry_sources(tmp_path: Path) -> None:
    """The artifact can be built only from the exact semantic and endpoint sources."""
    semantic = _semantic()
    verifier = build_task_verifier_source(semantic)
    registry = build_endpoint_registry(((0, NODE),), tmp_path)
    semantic_path = tmp_path / "semantic.json"
    verifier_path = tmp_path / "verifier.json"
    registry_path = tmp_path / "registry.json"
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    verifier_path.write_text(json.dumps(verifier), encoding="utf-8")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    artifact = build(semantic_path, verifier_path, registry_path)

    assert artifact["identity"]["semantic_evidence_digest"] == semantic["digest"]
    assert artifact["execution_lifetime"]["configured_endpoint_count"] == 1
    assert artifact["schema_version"] == "roboguide.execution-planning-evidence/v0.2"
    assert artifact["execution_topology"] == {
        "supported_coupling_modes": ["independent"],
        "supports_task_dependencies": True,
        "supports_context_relations": False,
        "actor_binding": "session-stable",
    }
    assert "node_id" not in json.dumps(artifact)
    assert artifact["digest"] == _digest(
        {key: value for key, value in artifact.items() if key != "digest"}
    )


def test_artifact_rejects_verifier_from_another_world(tmp_path: Path) -> None:
    """A rehashed or copied verifier source cannot be paired with another reset."""
    semantic = _semantic()
    verifier = build_task_verifier_source(semantic)
    other = copy.deepcopy(semantic)
    other_identity = cast(dict[str, Any], semantic["identity"])
    other["identity"] = {**other_identity, "scene_id": "other-scene"}
    # The changed semantic is intentionally digest-bound to its own world.
    other["digest"] = _digest({key: value for key, value in other.items() if key != "digest"})
    registry = build_endpoint_registry(((0, NODE),), tmp_path)
    semantic_path = tmp_path / "semantic.json"
    verifier_path = tmp_path / "verifier.json"
    registry_path = tmp_path / "registry.json"
    semantic_path.write_text(json.dumps(other), encoding="utf-8")
    verifier_path.write_text(json.dumps(verifier), encoding="utf-8")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(IntegrationError, match="verifier source is unavailable"):
        build(semantic_path, verifier_path, registry_path)
