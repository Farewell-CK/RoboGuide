"""Deterministic checks for the frozen Formal E1 admission contract."""

from pathlib import Path

import yaml


def _workload() -> dict[str, object]:
    """Load the repository-owned Controlled workload declaration."""
    root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load(
        (root / "evaluation/e1/controlled-workload-v0.1.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _object(value: object) -> dict[str, object]:
    """Narrow one workload value to an object for assertions."""
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return value


def _text_list(value: object) -> list[str]:
    """Narrow one workload value to a list of text values."""
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return value


def test_controlled_workload_freezes_episode_and_success_authority() -> None:
    """The candidate fixes episode 51 and Habitat PDDL success explicitly."""
    workload = _workload()
    assert workload["schema"] == "roboguide-e1.controlled-workload/v0.1"
    episode = _object(workload["episode"])
    assert episode["id"] == "51"
    assert episode["seed"] == 40
    assert episode["scene_id"] == ("data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb")
    semantic_task = _object(workload["semantic_task"])
    assert semantic_task["benchmark_success"] == "habitat.pddl_success"


def test_controlled_workload_admission_matches_goal_coverage() -> None:
    """Admission is ready only while both runners cover both official predicates."""
    workload = _workload()
    admission = _object(workload["admission"])
    coverage = _object(workload["current_runner_coverage"])
    emos_goals = _text_list(_object(coverage["emos"])["goal_predicates"])
    roboguide_goals = _text_list(_object(coverage["roboguide"])["goal_predicates"])
    assert emos_goals == ["any_at(any_targets|0)", "any_at(TARGET_any_targets|0)"]
    if admission["status"] == "ready":
        # The admitted state requires equal goal coverage plus an evidence anchor.
        assert roboguide_goals == emos_goals
        assert isinstance(admission.get("evidence"), str)
        assert admission["evidence"].endswith("summary.json")
    else:
        assert admission["status"] == "blocked"
        assert roboguide_goals != emos_goals
