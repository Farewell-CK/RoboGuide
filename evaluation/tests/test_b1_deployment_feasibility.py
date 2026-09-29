"""Frozen B1 workload binding for the one-reset deployment eligibility source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from b1_helpers import make_run, write_json
from roboguide_eval.b1_deployment_feasibility import (
    _content_digest,
    preflight_deployment_feasibility,
)
from roboguide_eval.b1_workload import extract_b1_workload


def test_cross_language_float_digest_golden_is_stable() -> None:
    """Tiny values, negative zero, and Unicode have one portable evidence digest."""
    assert _content_digest({"a": [1e-7, -0.0, 1.0], "b": "目标"}) == (
        "sha256:1ddb467cd0b0f8a661f022b93bae56e7dd457c530d75f55e03fbc3d84724fd8c"
    )
    assert _content_digest({"a": [-1.9126900434494019]}) == (
        "sha256:d7c35e78037b280f3263068c3ad0c72bcf2f2e515160495244441c6fbe5e135f"
    )


def _source(run: Path) -> dict[str, Any]:
    """Create one run-local artifact with exact frozen identity and a content digest."""
    workload = extract_b1_workload(json.loads((run / "b1-input-used.json").read_text()))
    semantic = json.loads((run / "evidence/authoritative-semantic-evidence.json").read_text())
    profile = {"digest": "sha256:" + "a" * 64}
    write_json(run / "spatial-profile.json", profile)
    body: dict[str, Any] = {
        "schema_version": "roboguide.deployment-intent-feasibility/v0.3",
        "authority": "deployment-observed-reset-state",
        "identity": {
            "run_id": run.name,
            "episode_id": workload.episode_id,
            "scene_id": workload.scene_id,
            "dataset_revision": workload.dataset_revision,
            "dataset_sha256": workload.dataset_sha256,
            "semantic_evidence_digest": semantic["digest"],
            "spatial_profile_digest": profile["digest"],
            "habitat_seed": workload.seed,
            "episode_reset_count": 1,
        },
        "initial_agent_positions": {"0": [0.0, 0.0, 0.0]},
        "records": [
            {
                "node_id": "node-a",
                "destination": "any_targets|0",
                "goal_occupancy": "any_at",
                "goal_tolerance_m": 2.0,
                "status": "unknown",
            }
        ],
    }
    return {**body, "digest": _content_digest(body)}


def test_preassignment_source_binds_frozen_b1_identity(tmp_path: Path) -> None:
    """Run, scene, dataset, seed, semantics, and profile must match exactly."""
    run = make_run(tmp_path)
    document = _source(run)
    write_json(run / "evidence/preassignment-feasibility.json", document)
    assert preflight_deployment_feasibility(run)["identity"]["run_id"] == run.name
    document["identity"]["habitat_seed"] = 41
    body = {key: value for key, value in document.items() if key != "digest"}
    document["digest"] = _content_digest(body)
    write_json(run / "evidence/preassignment-feasibility.json", document)
    with pytest.raises(ValueError, match="frozen B1 identity"):
        preflight_deployment_feasibility(run)


def test_preassignment_source_rejects_tampering_and_missing_source(tmp_path: Path) -> None:
    """An invalid or absent startup artifact cannot silently disable the filter."""
    run = make_run(tmp_path)
    document = _source(run)
    with pytest.raises(ValueError, match="missing"):
        preflight_deployment_feasibility(run)
    document["records"][0]["status"] = "compatible"
    write_json(run / "evidence/preassignment-feasibility.json", document)
    with pytest.raises(ValueError, match="digest"):
        preflight_deployment_feasibility(run)


def test_resealed_goal_claim_cannot_override_frozen_semantics(tmp_path: Path) -> None:
    """A new artifact digest cannot turn an official occupancy goal into a floor exclusion."""
    run = make_run(tmp_path)
    document = _source(run)
    document["records"][0]["goal_occupancy"] = "none"
    document["records"][0]["goal_tolerance_m"] = None
    document["records"][0]["status"] = "incompatible"
    body = {key: value for key, value in document.items() if key != "digest"}
    document["digest"] = _content_digest(body)
    write_json(run / "evidence/preassignment-feasibility.json", document)
    with pytest.raises(ValueError, match="differs from semantic evidence"):
        preflight_deployment_feasibility(run)


def test_prior_matrix_version_cannot_use_new_decision_rule(tmp_path: Path) -> None:
    """The B1 launcher rejects a resealed artifact under the old matrix schema."""
    run = make_run(tmp_path)
    document = _source(run)
    document["schema_version"] = "roboguide.deployment-intent-feasibility/v0.2"
    body = {key: value for key, value in document.items() if key != "digest"}
    document["digest"] = _content_digest(body)
    write_json(run / "evidence/preassignment-feasibility.json", document)
    with pytest.raises(ValueError, match="schema"):
        preflight_deployment_feasibility(run)
