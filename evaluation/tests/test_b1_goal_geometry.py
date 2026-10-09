"""Deterministic comparisons of official 3D and diagnostic horizontal goals."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.b1_goal_geometry import (
    GEOMETRY_SCHEMA,
    assess_goal_geometry,
    write_goal_geometry_diagnostic,
)
from roboguide_eval.b1_provenance import plan_digest


def _evidence(agent_count: int = 2) -> tuple[dict[str, Any], ...]:
    """Build one joint goal with a vertically separated target and exact source links."""
    semantic: dict[str, Any] = {
        "schema_version": "roboguide.authoritative-semantic-evidence/v0.2",
        "goal": {
            "kind": "logical",
            "operator": "and",
            "quantifier": None,
            "variables": [],
            "operands": [
                {"kind": "predicate", "name": "any_at", "arguments": ["object"]},
                {"kind": "predicate", "name": "any_at", "arguments": ["target"]},
            ],
        },
    }
    semantic["digest"] = plan_digest(semantic)
    agents = {
        str(index): {"pddl_reference_position": [100.0 * index, 0.0, 0.0]}
        for index in range(agent_count)
    }
    terminal = {
        "schema_version": "roboguide.e1.physical-diagnostics/v0.4",
        "phase": "terminal_world_state",
        "episode_id": "51",
        "scene_id": "scene-51",
        "simulator_steps": 76,
        "robot_at_threshold_m": 2.0,
        "agents": agents,
        "goal_entity_positions": {"object": [0.0, 2.3, 0.0], "target": [0.8, 0.0, 0.0]},
        "official_metrics": {"pddl_success": False},
    }
    summary = {
        "authoritative_semantic_evidence_digest": semantic["digest"],
        "official_pddl_success": False,
        "identity": {
            "episode_id": "51",
            "scene_id": "scene-51",
            "simulator_steps": 76,
            "initial_agent_positions": {agent: [0.0, 0.0, 0.0] for agent in agents},
        },
    }
    verdict = {"admission": {"provenance_valid": True}}
    return semantic, terminal, summary, verdict


@pytest.mark.parametrize("agent_count", [1, 2, 4])
@pytest.mark.parametrize("terminal_schema", ["v0.4", "v0.5", "v0.6"])
def test_horizontal_counterfactual_never_replaces_official_three_d_result(
    agent_count: int,
    terminal_schema: str,
) -> None:
    """A lower-floor robot may satisfy both X/Z projections but not the 3D goal."""
    semantic, terminal, summary, verdict = _evidence(agent_count)
    terminal["schema_version"] = f"roboguide.e1.physical-diagnostics/{terminal_schema}"
    result = assess_goal_geometry(semantic, terminal, summary, verdict)
    assert result["schema_version"] == GEOMETRY_SCHEMA
    assert result["terminal_diagnostics_schema"] == terminal["schema_version"]
    assert result["status"] == "available"
    assert result["official_pddl_success"] is False
    assert result["reconstructed_three_d_goal"] is False
    assert result["horizontal_goal_counterfactual"] is True
    assert result["predicate_distances"][0]["agent_distances"]["0"]["vertical_m"] == 2.3


def test_two_agents_can_jointly_satisfy_distinct_goal_locations() -> None:
    """Any-at quantifies over the agent set independently for each predicate."""
    semantic, terminal, summary, verdict = _evidence()
    terminal["agents"]["1"]["pddl_reference_position"] = [10.0, 0.0, 0.0]
    terminal["goal_entity_positions"] = {"object": [0.0, 0.0, 0.0], "target": [10.0, 0.0, 0.0]}
    terminal["official_metrics"]["pddl_success"] = True
    summary["official_pddl_success"] = True
    result = assess_goal_geometry(semantic, terminal, summary, verdict)
    assert result["status"] == "available"
    assert result["reconstructed_three_d_goal"] is True
    assert result["horizontal_goal_counterfactual"] is True


@pytest.mark.parametrize("version", ["v0.6", "v0.7"])
def test_motion_observation_never_replaces_official_goal_truth(version: str) -> None:
    """New or unavailable motion data cannot turn an official failure into success."""
    semantic, terminal, summary, verdict = _evidence()
    terminal["schema_version"] = f"roboguide.e1.physical-diagnostics/{version}"
    terminal["agents"]["0"]["local_motion"] = {
        "last_post_step": {"success": True, "returned_end": [0.0, 2.3, 0.0]},
        "pending_since_last_post_step": {"_status": "unavailable"},
    }
    terminal["agents"]["0"]["local_manipulation"] = {"is_grasped": True}
    result = assess_goal_geometry(semantic, terminal, summary, verdict)
    assert result["status"] == "available"
    assert result["official_pddl_success"] is False
    assert result["reconstructed_three_d_goal"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing_threshold", "robot_at_threshold_unavailable"),
        ("legacy_schema", "terminal_diagnostics_schema_unsupported"),
        ("missing_pose", "pddl_reference_position_unavailable"),
        ("source_mismatch", "semantic_summary_mismatch"),
        ("provenance_invalid", "b1_provenance_not_valid"),
        ("unsupported_predicate", "goal_not_any_at_only"),
        ("official_disagreement", "three_d_reconstruction_disagrees_with_official"),
        ("agent_missing", "terminal_agent_set_mismatch"),
        ("step_mismatch", "terminal_step_identity_mismatch"),
        ("scene_mismatch", "terminal_episode_scene_mismatch"),
        ("episode_mismatch", "terminal_episode_scene_mismatch"),
    ],
)
def test_missing_or_inconsistent_inputs_cannot_claim_a_counterfactual(
    mutation: str, reason: str
) -> None:
    """Only a complete, same-run source can produce an available comparison."""
    semantic, terminal, summary, verdict = deepcopy(_evidence())
    if mutation == "missing_threshold":
        terminal.pop("robot_at_threshold_m")
    elif mutation == "legacy_schema":
        terminal["schema_version"] = "roboguide.e1.physical-diagnostics/v0.3"
    elif mutation == "missing_pose":
        terminal["agents"]["1"]["pddl_reference_position"] = {"_status": "unavailable"}
    elif mutation == "source_mismatch":
        summary["authoritative_semantic_evidence_digest"] = "sha256:" + "0" * 64
    elif mutation == "provenance_invalid":
        verdict["admission"]["provenance_valid"] = False
    elif mutation == "unsupported_predicate":
        semantic["goal"]["operands"][0]["name"] = "holding"
        semantic["digest"] = plan_digest({k: v for k, v in semantic.items() if k != "digest"})
        summary["authoritative_semantic_evidence_digest"] = semantic["digest"]
    elif mutation == "official_disagreement":
        terminal["official_metrics"]["pddl_success"] = True
        summary["official_pddl_success"] = True
    elif mutation == "agent_missing":
        terminal["agents"].pop("1")
    elif mutation == "step_mismatch":
        terminal["simulator_steps"] = 75
    elif mutation == "scene_mismatch":
        terminal["scene_id"] = "other-scene"
    elif mutation == "episode_mismatch":
        terminal["episode_id"] = "other-episode"
    result = assess_goal_geometry(semantic, terminal, summary, verdict)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert "horizontal_goal_counterfactual" not in result


def test_early_failure_archives_explicit_unavailable_geometry(tmp_path: Path) -> None:
    """An MI or Controller early failure keeps the optional result unavailable."""
    result = write_goal_geometry_diagnostic(tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "b1_verdict_invalid"
    assert (tmp_path / "evidence/goal-geometry-diagnostic.json").exists()


def test_offline_sidecar_uses_only_archived_inputs_and_preserves_verdict(tmp_path: Path) -> None:
    """A complete archived run produces a separate sidecar without rewriting authority."""
    semantic, terminal, summary, verdict = _evidence()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    documents = {
        "evidence/authoritative-semantic-evidence.json": semantic,
        "evidence/diagnostics-terminal.json": terminal,
        "evidence/shared-world-summary.json": summary,
        "b1-verdict.json": verdict,
    }
    for name, document in documents.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    original_verdict = (tmp_path / "b1-verdict.json").read_bytes()
    output = tmp_path / "separate-analysis" / "geometry.json"
    result = write_goal_geometry_diagnostic(tmp_path, output)
    assert result["status"] == "available"
    assert json.loads(output.read_text())["horizontal_goal_counterfactual"] is True
    assert (tmp_path / "b1-verdict.json").read_bytes() == original_verdict
    assert not (evidence / "goal-geometry-diagnostic.json").exists()
