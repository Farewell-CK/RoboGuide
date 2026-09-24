"""Deterministic tests for pre-reset Habitat planning-world evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.planning_world_evidence import (  # noqa: E402
    PlanningWorldEvidenceBuildError,
    build_authoritative_planning_world_evidence,
)


class _Vector:
    """Expose Habitat-like vector attributes without importing Habitat."""

    def __init__(self, x: float, y: float, z: float) -> None:
        """Retain one deterministic three-dimensional value."""
        self.x, self.y, self.z = x, y, z


class _Sim:
    """Provide static scene and target data while tracking forbidden reset calls."""

    def __init__(self) -> None:
        """Create one room/floor, one object, and one goal transform."""
        region = SimpleNamespace(
            id="room-1",
            level=SimpleNamespace(id="floor-1"),
            aabb=SimpleNamespace(center=_Vector(1.0, 2.0, 3.0), sizes=_Vector(4.0, 4.0, 4.0)),
        )
        self.semantic_scene = SimpleNamespace(regions=[region])
        self.reset_calls = 0

    def get_rigid_object_manager(self) -> object:
        """Reject a pre-reset object-manager read that could return stale instances."""
        raise AssertionError("object manager must not be read before reset")

    def _get_target_trans(self) -> list[tuple[int, object]]:
        """Reject target-index reads before PDDL binds this episode on reset."""
        raise AssertionError("sim target indices must not be read before reset")


def _environment(dataset_path: Path) -> SimpleNamespace:
    """Build an environment facade with dataset and objective identities."""
    object_transform = [
        [1.0, 0.0, 0.0, 1.0],
        [0.0, 1.0, 0.0, 2.0],
        [0.0, 0.0, 1.0, 3.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    target_transform = [
        [1.0, 0.0, 0.0, 1.5],
        [0.0, 1.0, 0.0, 2.0],
        [0.0, 0.0, 1.0, 3.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    goal = SimpleNamespace(
        sub_exprs=[
            SimpleNamespace(
                name="any_at",
                _arg_values=[SimpleNamespace(name="any_targets|0")],
                sub_exprs=None,
            ),
            SimpleNamespace(
                name="any_at",
                _arg_values=[SimpleNamespace(name="TARGET_any_targets|0")],
                sub_exprs=None,
            ),
        ],
        expr_type=SimpleNamespace(value="and"),
        quantifier=None,
        inputs=(),
    )
    return SimpleNamespace(
        current_episode=SimpleNamespace(
            episode_id="51",
            scene_id="scene-51",
            info={"object_labels": {"object-handle": "any_targets|0"}, "same_floor": False},
            rigid_objs=[("object-handle", object_transform)],
            targets={"object-handle": target_transform},
        ),
        _dataset=SimpleNamespace(
            config=SimpleNamespace(data_path=str(dataset_path), split="train"),
            content_scenes_path=str(dataset_path.parent / "content/{scene}"),
        ),
        sim=_Sim(),
        task=SimpleNamespace(pddl_problem=SimpleNamespace(goal=goal)),
    )


def test_builder_reports_static_object_and_goal_floor_facts_without_reset(tmp_path: Path) -> None:
    """Only static scene reads enter planning evidence; reset state stays unknown."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-51",
        episode_id="51",
    )
    assert [fact["entity_id"] for fact in document["facts"]] == [
        "TARGET_any_targets|0",
        "any_targets|0",
    ]
    assert any(gap["code"] == "agent_start_state_pending_reset" for gap in document["gaps"])
    assert document["relations"] == [
        {
            "subject_entity_id": "any_targets|0",
            "relation": "different_floor",
            "object_entity_id": "TARGET_any_targets|0",
        }
    ]
    assert environment.sim.reset_calls == 0


def test_builder_keeps_unresolved_regions_as_explicit_gaps(tmp_path: Path) -> None:
    """A missing floor mapping never becomes a guessed planning constraint."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    environment.sim.semantic_scene.regions[0].level = None
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-51",
        episode_id="51",
    )
    assert document["facts"] == []
    assert any(gap["code"] == "object_region_unresolved" for gap in document["gaps"])


def test_builder_does_not_guess_runtime_object_instance_from_template_name(tmp_path: Path) -> None:
    """A template path cannot stand in for an episode instance-handle location."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    environment.current_episode.rigid_objs[0] = (
        "object-template.object_config.json",
        environment.current_episode.rigid_objs[0][1],
    )
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-51",
        episode_id="51",
    )
    assert [fact["entity_id"] for fact in document["facts"]] == ["TARGET_any_targets|0"]
    assert any(gap["code"] == "object_spatial_fact_unavailable" for gap in document["gaps"])


def test_builder_rejects_episode_identity_mismatch(tmp_path: Path) -> None:
    """Evidence cannot be reused for a different requested episode."""
    with pytest.raises(PlanningWorldEvidenceBuildError, match="episode identity"):
        build_authoritative_planning_world_evidence(
            _environment(tmp_path / "dataset.json.gz"),
            run_id="run-51",
            episode_id="52",
        )


def test_builder_rejects_ambiguous_region_and_unmatched_target_identity(tmp_path: Path) -> None:
    """Overlapping floors and target handles never become asserted goal facts."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    overlapping = environment.sim.semantic_scene.regions[0]
    environment.sim.semantic_scene.regions.append(
        SimpleNamespace(id="room-2", level=SimpleNamespace(id="floor-2"), aabb=overlapping.aabb)
    )
    document = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51"
    )
    assert document["facts"] == []
    assert {gap["code"] for gap in document["gaps"]} >= {
        "object_region_unresolved",
        "goal_region_unresolved",
    }

    environment.sim.semantic_scene.regions.pop()
    environment.current_episode.targets = {
        "other-instance": next(iter(environment.current_episode.targets.values()))
    }
    document = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51"
    )
    assert [fact["entity_id"] for fact in document["facts"]] == ["any_targets|0"]
    assert any(gap["code"] == "goal_entity_mapping_incomplete" for gap in document["gaps"])


def test_builder_keeps_non_affine_episode_target_as_gap(tmp_path: Path) -> None:
    """Malformed static transforms cannot be promoted into floor facts."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    target = next(iter(environment.current_episode.targets.values()))
    target[3][3] = 0.0
    document = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51"
    )
    assert [fact["entity_id"] for fact in document["facts"]] == ["any_targets|0"]
    assert any(gap["code"] == "goal_region_unresolved" for gap in document["gaps"])
