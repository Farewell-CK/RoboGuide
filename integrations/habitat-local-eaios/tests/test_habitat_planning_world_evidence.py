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


def test_reset_geometry_reads_exact_entities_without_reset_render_or_rng(tmp_path: Path) -> None:
    """Geometry is actual position data and preserves missing entities as explicit gaps."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: SimpleNamespace(name=name)
    problem.sim_info = SimpleNamespace(
        robot_at_thresh=1.0,
        get_entity_pos=lambda entity: [1.125, 7.5 if entity.name == "any_targets|0" else 2.0, -3.0],
    )
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-51",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert document["schema_version"] == "roboguide.authoritative-planning-world-evidence/v0.4"
    geometry = {fact["entity_id"]: fact for fact in document["entity_geometry"]}
    assert geometry["any_targets|0"]["position_mm"] == [1125, 7500, -3000]
    assert geometry["TARGET_any_targets|0"]["position_mm"][1] == 2000
    assert environment.sim.reset_calls == 0
    problem.get_entity = lambda name: None
    unavailable = build_authoritative_planning_world_evidence(
        environment,
        run_id="run-51",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert unavailable["entity_geometry"] == []
    assert any(gap["code"] == "entity_geometry_unavailable" for gap in unavailable["gaps"])


@pytest.mark.parametrize("has_exact_static_handle", [False, True])
def test_reset_floor_facts_use_the_same_actual_positions_as_entity_geometry(
    tmp_path: Path, has_exact_static_handle: bool
) -> None:
    """A reset object cannot inherit a missing or stale pre-reset floor assignment."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    if not has_exact_static_handle:
        environment.current_episode.rigid_objs = [("template-handle", [[1.0]])]
    environment.sim.semantic_scene.regions.append(
        SimpleNamespace(
            id="room-2",
            level=SimpleNamespace(id="floor-2"),
            aabb=SimpleNamespace(center=_Vector(1, 7.5, 3), sizes=_Vector(4, 2, 4)),
        )
    )
    problem = environment.task.pddl_problem
    reads: list[str] = []

    def position(entity: SimpleNamespace) -> list[float]:
        """Count existing position reads without rendering, moving or querying an object manager."""
        reads.append(entity.name)
        return [1, 7.5, 3] if entity.name == "any_targets|0" else [1.5, 2, 3]

    problem.get_entity = lambda name: SimpleNamespace(name=name)
    problem.sim_info = SimpleNamespace(robot_at_thresh=1.0, get_entity_pos=position)
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="reset-facts",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    facts = {fact["entity_id"]: fact for fact in document["facts"]}
    assert facts["any_targets|0"] == {
        "entity_id": "any_targets|0",
        "region_id": "room-2",
        "floor_id": "floor-2",
    }
    assert facts["TARGET_any_targets|0"]["floor_id"] == "floor-1"
    assert document["relations"] == [
        {
            "subject_entity_id": "any_targets|0",
            "relation": "different_floor",
            "object_entity_id": "TARGET_any_targets|0",
        }
    ]
    assert document["identity"]["source_revision"] == (
        "habitat-local-eaios-reset-entity-geometry/v0.4.1"
    )
    assert not any(gap["code"] == "object_spatial_fact_unavailable" for gap in document["gaps"])
    assert len(reads) == 4  # Two witness reads and two geometry/fact reads; no extra floor reads.
    assert environment.sim.reset_calls == 0


@pytest.mark.parametrize("fault", ["outside", "overlap", "missing-entity"])
def test_reset_floor_unknown_never_falls_back_to_an_old_static_location(
    tmp_path: Path, fault: str
) -> None:
    """Unresolved current spatial evidence stays unknown even when old episode data is available."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: (
        None if fault == "missing-entity" else SimpleNamespace(name=name)
    )
    problem.sim_info = SimpleNamespace(
        robot_at_thresh=1.0,
        get_entity_pos=lambda entity: [1, 10 if fault == "outside" else 2, 3],
    )
    if fault == "overlap":
        environment.sim.semantic_scene.regions.append(environment.sim.semantic_scene.regions[0])
        environment.sim.semantic_scene.regions[-1] = SimpleNamespace(
            id="another-room",
            level=SimpleNamespace(id="another-floor"),
            aabb=environment.sim.semantic_scene.regions[0].aabb,
        )
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="unknown-reset",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert document["facts"] == []
    assert document["relations"] == []
    assert any(
        gap["code"] in {"entity_geometry_unavailable", "reset_entity_region_unresolved"}
        for gap in document["gaps"]
    )


def test_reset_floor_relation_cannot_contradict_current_exact_entity_locations(
    tmp_path: Path,
) -> None:
    """An authored relation is not projected as current truth when reset evidence contradicts it."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: SimpleNamespace(name=name)
    problem.sim_info = SimpleNamespace(robot_at_thresh=1.0, get_entity_pos=lambda entity: [1, 2, 3])
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="relation-conflict",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert len(document["facts"]) == 2
    assert document["relations"] == []
    assert any(gap["code"] == "reset_floor_relation_conflict" for gap in document["gaps"])


def test_floor_containment_uses_the_unrounded_sample_and_retains_geometry(tmp_path: Path) -> None:
    """Millimetre serialization cannot move an outside point onto an apparent floor boundary."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: SimpleNamespace(name=name)
    problem.sim_info = SimpleNamespace(
        robot_at_thresh=1.0,
        get_entity_pos=lambda entity: [1, 4.00049 if entity.name == "any_targets|0" else 2, 3],
    )
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="unrounded",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert [fact["entity_id"] for fact in document["facts"]] == ["TARGET_any_targets|0"]
    geometry = {entry["entity_id"]: entry for entry in document["entity_geometry"]}
    assert geometry["any_targets|0"]["position_mm"][1] == 4000
    assert geometry["TARGET_any_targets|0"]["position_mm"][1] == 2000


def test_broken_optional_region_reader_preserves_independent_position_evidence(
    tmp_path: Path,
) -> None:
    """An unreadable possible containing region prevents a floor claim, not the geometry sample."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)

    class BrokenRegion:
        """Represent a loaded semantic region whose optional geometry cannot be read."""

        @property
        def aabb(self) -> None:
            """Fail without mutating the world or supplying a guessed containing region."""
            raise RuntimeError("region unavailable")

    environment.sim.semantic_scene.regions.append(BrokenRegion())
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: SimpleNamespace(name=name)
    problem.sim_info = SimpleNamespace(robot_at_thresh=1.0, get_entity_pos=lambda entity: [1, 2, 3])
    document = build_authoritative_planning_world_evidence(
        environment,
        run_id="region-read-fault",
        episode_id="51",
        reset_goal_geometry=True,
        entity_geometry=True,
    )
    assert len(document["entity_geometry"]) == 2
    assert document["facts"] == []
    assert not any(gap["code"] == "entity_geometry_unavailable" for gap in document["gaps"])


@pytest.mark.parametrize("field", ["episode_id", "scene_id"])
def test_reset_geometry_cannot_borrow_another_current_episode(
    tmp_path: Path,
    field: str,
) -> None:
    """The selected metadata cannot relabel live PDDL geometry from a different reset world."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    selected = SimpleNamespace(**vars(environment.current_episode))
    setattr(environment.current_episode, field, "other-world")
    with pytest.raises(PlanningWorldEvidenceBuildError, match="reset world"):
        build_authoritative_planning_world_evidence(
            environment,
            run_id="mismatched-reset",
            episode_id="51",
            episode=selected,
            reset_goal_geometry=True,
            entity_geometry=True,
        )


def test_reset_goal_witness_uses_official_tolerance_and_exact_entity_positions(
    tmp_path: Path,
) -> None:
    """A reset geometric overlap permits one witness without a simulator step."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    positions = {
        "any_targets|0": (0.0, 0.0, 0.0),
        "TARGET_any_targets|0": (1.675, 0.0, 0.0),
    }
    problem = environment.task.pddl_problem
    problem.get_entity = lambda name: name
    problem.sim_info = SimpleNamespace(
        robot_at_thresh=2.0, get_entity_pos=lambda entity: positions[entity]
    )
    document = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51", reset_goal_geometry=True
    )
    assert document["schema_version"] == "roboguide.authoritative-planning-world-evidence/v0.2"
    assert [item["destination_entity_id"] for item in document["goal_witnesses"]] == [
        "TARGET_any_targets|0",
        "any_targets|0",
    ]
    assert all(item["max_distance_mm"] == 1675 for item in document["goal_witnesses"])
    assert all(item["tolerance_mm"] == 2000 for item in document["goal_witnesses"])
    assert environment.sim.reset_calls == 0

    positions["TARGET_any_targets|0"] = (2.001, 0.0, 0.0)
    separated = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51", reset_goal_geometry=True
    )
    assert separated["goal_witnesses"] == []


def test_reset_goal_witness_unavailable_is_explicit_and_does_not_guess(tmp_path: Path) -> None:
    """An unbound PDDL simulator yields a gap instead of a positive claim."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    document = build_authoritative_planning_world_evidence(
        _environment(dataset), run_id="run-51", episode_id="51", reset_goal_geometry=True
    )
    assert document["goal_witnesses"] == []
    assert any(gap["code"] == "reset_goal_geometry_unavailable" for gap in document["gaps"])


def test_reset_goal_witness_does_not_simplify_quantified_goals(tmp_path: Path) -> None:
    """A quantified conjunction cannot be treated as two direct terminal predicates."""
    dataset = tmp_path / "dataset.json.gz"
    dataset.write_bytes(b"dataset")
    environment = _environment(dataset)
    problem = environment.task.pddl_problem
    problem.goal.quantifier = SimpleNamespace(value="forall")
    problem.get_entity = lambda name: name
    problem.sim_info = SimpleNamespace(
        robot_at_thresh=2.0,
        get_entity_pos=lambda entity: (0.0, 0.0, 0.0),
    )
    document = build_authoritative_planning_world_evidence(
        environment, run_id="run-51", episode_id="51", reset_goal_geometry=True
    )
    assert document["goal_witnesses"] == []


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
