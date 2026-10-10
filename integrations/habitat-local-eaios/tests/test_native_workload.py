"""Exact pre-construction selection and original-call observation without Habitat."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from habitat_local_eaios.native_workload import (  # noqa: E402
    install_execution_observer,
    install_workload,
    select_episode,
)


class Dataset:
    """Retain original episode object identity through the native filter API."""

    def __init__(self, episodes: list[Any]) -> None:
        """Keep episode objects without changing poses or consuming randomness."""
        self.episodes = episodes

    def filter_episodes(self, predicate: Any) -> Dataset:
        """Return the exact original matching objects in a separate dataset."""
        return Dataset([episode for episode in self.episodes if predicate(episode)])


class Env:
    """Expose one constructor, reset and step counter plus an actual metric cache."""

    constructions = 0

    def __init__(self, config: Any, dataset: Dataset) -> None:
        """Construct directly in the selected scene, without an implicit reset."""
        type(self).constructions += 1
        self._config = config
        self.current_episode = dataset.episodes[0]
        self.resets = self.steps = self.closes = 0
        self.episode_over = False
        self.success = False
        self.fail_step = False
        self.sim = SimpleNamespace(num_articulated_agents=0)
        self.task = SimpleNamespace(pddl_problem=None)
        self.result = {"same_observation": True}

    def reset(self) -> dict[str, bool]:
        """Reset once and return the original observation object."""
        self.resets += 1
        return self.result

    def step(self, action: Any) -> dict[str, bool]:
        """Expose original action and metric behavior without synthesized outcomes."""
        self.steps += 1
        if self.fail_step:
            raise RuntimeError("original physical exception")
        self.episode_over = True
        return self.result

    def close(self) -> None:
        """Count original close calls independently from diagnostics."""
        self.closes += 1

    def get_metrics(self) -> dict[str, bool]:
        """Return a previously computed official metric without updating the simulator."""
        return {"pddl_success": self.success}


@pytest.mark.parametrize("rows", [[], [("7", "wrong")], [("7", "scene"), ("7", "other")]])
def test_selection_rejects_missing_duplicate_and_wrong_scene(rows: list[tuple[str, str]]) -> None:
    """An episode label or matching seed alone never establishes the selected workload."""
    dataset = Dataset(
        [SimpleNamespace(episode_id=episode, scene_id=scene) for episode, scene in rows]
    )
    with pytest.raises(ValueError, match="identity"):
        select_episode(dataset, "7", "scene")


def test_selected_episode_reaches_first_constructor_without_mutation(tmp_path: Path) -> None:
    """Exclude wrong initial scenes, extra resets and edited starting poses."""
    source = tmp_path / "original.json.gz"
    source.write_bytes(b"original frozen dataset")
    wanted = SimpleNamespace(episode_id="7", scene_id="scene", start_position=[1, 2, 3])
    dataset = Dataset([SimpleNamespace(episode_id="0", scene_id="wrong"), wanted])
    config = SimpleNamespace(
        dataset=SimpleNamespace(data_path=str(source), split="val", type="original"),
        seed=40,
        environment=SimpleNamespace(max_episode_steps=4000),
    )
    module = SimpleNamespace(Env=Env, make_dataset=lambda **kwargs: dataset)
    restore = install_workload(
        module,
        directory=tmp_path,
        dataset_path=source,
        dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        episode_id="7",
        scene_id="scene",
    )
    before = Env.constructions
    try:
        env = Env(config, None)  # type: ignore[arg-type]
        assert env.current_episode is wanted
        assert env.resets == env.steps == 0 and Env.constructions == before + 1
        assert wanted.start_position == [1, 2, 3] and len(dataset.episodes) == 2
        identity = json.loads((tmp_path / "workload-selection.json").read_text())
        assert identity["episode_id"] == "7" and identity["max_episode_steps"] == 4000
        source.write_bytes(b"changed")
        with pytest.raises(ValueError, match="bytes changed"):
            Env(config, None)  # type: ignore[arg-type]
        assert Env.constructions == before + 1
    finally:
        restore()


@pytest.mark.parametrize("success", [False, True])
def test_observer_preserves_terminal_metric_and_auto_reset(tmp_path: Path, success: bool) -> None:
    """Preserve the original terminal result through a later automatic reset."""
    restore = install_execution_observer(Env, tmp_path)
    try:
        env = Env(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.success = success
        assert env.reset() is env.result and env.step({"original_action": 1}) is env.result
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["official_pddl_success"] is success
        assert outcome["simulator_steps"] == 1 and outcome["episode_terminal"] is True
        env.reset()
        env.close()
        assert json.loads((tmp_path / "native-outcome.json").read_text()) == outcome
        assert (env.resets, env.steps, env.closes) == (2, 1, 1)
    finally:
        restore()


def test_step_exception_and_unexecuted_close_preserve_unknown_outcome(tmp_path: Path) -> None:
    """Physical exceptions propagate unchanged; no completed metric is fabricated."""
    restore = install_execution_observer(Env, tmp_path)
    try:
        env = Env(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        env.fail_step = True
        with pytest.raises(RuntimeError, match="original physical exception"):
            env.step("action")
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["official_pddl_success"] is None and outcome["simulator_steps"] == 0
        assert outcome["termination_reason"] == "original-step-exception:RuntimeError"
        env.close()
        assert env.closes == 1 and env.steps == 1
    finally:
        restore()


def test_read_and_storage_failures_do_not_change_original_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostics may be unavailable but cannot change original actions or return identity."""
    from habitat_local_eaios import native_workload

    def broken(*args: Any, **kwargs: Any) -> None:
        """Simulate a filesystem or serialization failure without physical effects."""
        raise OSError("storage unavailable")

    monkeypatch.setattr(native_workload, "write_text_atomic", broken)
    restore = install_execution_observer(Env, tmp_path)
    try:
        env = Env(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        assert env.reset() is env.result
        assert env.step("action") is env.result
        env.close()
        assert (env.resets, env.steps, env.closes) == (1, 1, 1)
    finally:
        restore()


def test_successful_step_checkpoints_survive_parent_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable progress is a bounded lower bound, never an invented terminal metric."""
    original_step = Env.step

    def continuing_step(env: Env, action: Any) -> dict[str, bool]:
        """Keep the original action/result/counter while simulating a continuing episode."""
        result = original_step(env, action)
        env.episode_over = False
        return result

    monkeypatch.setattr(Env, "step", continuing_step)
    restore = install_execution_observer(Env, tmp_path)
    try:
        env = Env(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        for _ in range(65):
            assert env.step("unchanged action") is env.result
        progress = json.loads((tmp_path / "native-progress.json").read_text())
        assert progress["simulator_steps"] == 64
        assert progress["checkpoint_period_steps"] == 32
        assert progress["official_outcome_available"] is False
        assert (env.resets, env.steps, env.closes) == (1, 65, 0)
        assert not (tmp_path / "native-outcome.json").exists()
        env.close()
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["simulator_steps"] == 65 and outcome["official_pddl_success"] is None
    finally:
        restore()


def test_metric_and_terminal_diagnostic_failure_preserve_execution_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent observer failures cannot hide an original terminal step or close call."""
    from habitat_local_eaios.diagnostics import PhysicalDiagnostics

    def broken(*args: Any, **kwargs: Any) -> Any:
        """Simulate missing metrics or terminal serialization without physical effects."""
        raise RuntimeError("diagnostic read failed")

    monkeypatch.setattr(Env, "get_metrics", broken)
    monkeypatch.setattr(PhysicalDiagnostics, "record_terminal", broken)
    restore = install_execution_observer(Env, tmp_path)
    try:
        env = Env(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        assert env.step("original action") is env.result
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["episode_terminal"] is True and outcome["simulator_steps"] == 1
        assert outcome["official_pddl_success"] is None
        assert json.loads((tmp_path / "native-progress.json").read_text())["simulator_steps"] == 1
        env.close()
        assert (env.resets, env.steps, env.closes) == (1, 1, 1)
    finally:
        restore()
