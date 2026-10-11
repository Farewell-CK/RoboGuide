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


class ContinuingEnv(Env):
    """Keep base episode_over false while the RL wrapper may finish on official success."""

    def step(self, action: Any) -> dict[str, bool]:
        """Return one original physical step without applying RL terminal semantics."""
        result = super().step(action)
        self.episode_over = False
        return result


class RLEnv:
    """Mirror the original four-tuple RL boundary, including its one done computation."""

    def __init__(self, env: Env, done: Any) -> None:
        """Hold the exact base environment and a precomputed local done value."""
        self._env = env
        self.done = done
        self.done_calls = 0
        self.result: Any = None
        self.error: BaseException | None = None

    def step(self, action: Any) -> Any:
        """Return the original tuple after exactly one physical and done call."""
        observation = self._env.step(action)
        self.done_calls += 1
        if self.error is not None:
            raise self.error
        self.result = (observation, 0.0, self.done, self._env.get_metrics())
        return self.result


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("array_scalar", [False, True])
def test_rl_terminal_is_archived_before_native_auto_reset(
    tmp_path: Path, success: bool, array_scalar: bool
) -> None:
    """A returned done captures true or false before reset clears the first episode cache."""

    class DoneScalar:
        """Expose the original array scalar conversion without importing Habitat or NumPy."""

        def item(self) -> bool:
            """Return an already computed boolean; no second done computation occurs."""
            return True

    restore = install_execution_observer(ContinuingEnv, tmp_path, rl_env_type=RLEnv)
    try:
        env = ContinuingEnv(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        env.success = success
        rl = RLEnv(env, DoneScalar() if array_scalar else True)
        result = rl.step("unchanged action")
        assert result is rl.result and result[0] is env.result
        assert env.episode_over is False
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["termination_reason"] == "original-rl-done"
        assert outcome["episode_terminal"] is True and outcome["simulator_steps"] == 1
        assert outcome["official_pddl_success"] is success
        assert outcome["official_metric_source"].startswith("original RLEnv.step returned info")
        env.success = False
        env.current_episode = SimpleNamespace(episode_id="next", scene_id="other")
        env.reset()
        env.close()
        assert json.loads((tmp_path / "native-outcome.json").read_text()) == outcome
        assert (env.resets, env.steps, env.closes, rl.done_calls) == (2, 1, 1, 1)
    finally:
        restore()


def test_cached_success_without_returned_done_is_not_a_terminal_result(tmp_path: Path) -> None:
    """The observer cannot infer termination from a cached metric or alter native done."""
    restore = install_execution_observer(ContinuingEnv, tmp_path, rl_env_type=RLEnv)
    try:
        env = ContinuingEnv(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        env.success = True
        rl = RLEnv(env, False)
        assert rl.step("unchanged action") is rl.result
        assert not (tmp_path / "native-outcome.json").exists()
        env.close()
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["episode_terminal"] is False and outcome["official_pddl_success"] is None
        assert (env.resets, env.steps, env.closes, rl.done_calls) == (1, 1, 1, 1)
    finally:
        restore()


def test_rl_exception_after_physical_step_is_preserved_exactly(tmp_path: Path) -> None:
    """A done/reward failure retains executed progress but cannot manufacture official truth."""
    restore = install_execution_observer(ContinuingEnv, tmp_path, rl_env_type=RLEnv)
    try:
        env = ContinuingEnv(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        rl = RLEnv(env, True)
        error = RuntimeError("original reward failure")
        rl.error = error
        with pytest.raises(RuntimeError) as caught:
            rl.step("unchanged action")
        assert caught.value is error
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["termination_reason"] == "original-rl-step-exception:RuntimeError"
        assert outcome["simulator_steps"] == 1 and outcome["official_pddl_success"] is None
        assert (env.resets, env.steps, rl.done_calls) == (1, 1, 1)
    finally:
        restore()


def test_rl_diagnostic_failure_and_foreign_environment_do_not_change_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing observer and an unrelated RL environment retain their exact original tuples."""
    from habitat_local_eaios.diagnostics import PhysicalDiagnostics

    def broken(*args: Any, **kwargs: Any) -> None:
        """Simulate unavailable terminal diagnostics without touching the original execution."""
        raise RuntimeError("diagnostic terminal failure")

    monkeypatch.setattr(PhysicalDiagnostics, "record_terminal", broken)
    restore = install_execution_observer(ContinuingEnv, tmp_path, rl_env_type=RLEnv)
    try:
        env = ContinuingEnv(
            SimpleNamespace(seed=40), Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
        )
        env.reset()
        foreign = Env(
            SimpleNamespace(seed=40),
            Dataset([SimpleNamespace(episode_id="other", scene_id="else")]),
        )
        foreign_rl = RLEnv(foreign, True)
        assert foreign_rl.step("foreign action") is foreign_rl.result
        assert not (tmp_path / "native-outcome.json").exists()
        env.success = True
        rl = RLEnv(env, True)
        assert rl.step("original action") is rl.result
        outcome = json.loads((tmp_path / "native-outcome.json").read_text())
        assert outcome["episode_id"] == "7" and outcome["official_pddl_success"] is True
        assert (env.steps, rl.done_calls, foreign.steps, foreign_rl.done_calls) == (1, 1, 1, 1)
    finally:
        restore()


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


@pytest.mark.parametrize("fault", ["robot-read", "identity-read", "agent-count", "json", "write"])
def test_constructor_observation_failure_preserves_selected_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Added read/JSON/write failures retain the one constructed world and mark missing evidence."""
    from habitat_local_eaios import native_workload
    from habitat_local_eaios.evidence_io import write_text_atomic

    source = tmp_path / "dataset.json.gz"
    source.write_bytes(b"original dataset")
    wanted = SimpleNamespace(episode_id="7", scene_id="scene", start_position=[1, 2, 3])
    dataset = Dataset([wanted])
    config = SimpleNamespace(
        dataset=SimpleNamespace(data_path=str(source), split="val", type="original"),
        seed=40,
        environment=SimpleNamespace(max_episode_steps=4000),
    )
    reads: list[int] = []

    def read_robot(index: int) -> Any:
        """Fail only an added observation; never perform simulation or randomness."""
        reads.append(index)
        raise OSError("private diagnostic detail must not enter evidence")

    class UnreadableEpisode:
        """Simulate an unavailable diagnostic accessor after a valid original construction."""

        @property
        def episode_id(self) -> str:
            """Raise only when the observer reads identity, without mutating the selected object."""
            raise OSError("identity unavailable")

    class ConstructedEnv(Env):
        """Return the original environment before any fault in its observation."""

        def __init__(self, config: Any, dataset: Dataset) -> None:
            """Keep original construction and introduce only the requested diagnostic failure."""
            super().__init__(config, dataset)
            if fault == "robot-read":
                self.sim = SimpleNamespace(num_articulated_agents=1, get_agent_data=read_robot)
            elif fault == "identity-read":
                self.current_episode = UnreadableEpisode()
            elif fault == "agent-count":
                self.sim = SimpleNamespace(num_articulated_agents=129, get_agent_data=read_robot)

    original_dumps = json.dumps
    original_write = write_text_atomic

    def serialize(document: Any, *args: Any, **kwargs: Any) -> str:
        """Fail the evidence body while allowing the independent status sidecar to serialize."""
        if fault == "json" and document.get("schema_version") == (
            "roboguide.native-workload-selection/v0.1"
        ):
            raise TypeError("unserializable observation")
        return original_dumps(document, *args, **kwargs)

    def write(path: Path, value: str) -> None:
        """Fail the selection file without preventing the unavailable status sidecar."""
        if fault == "write" and path.name == "workload-selection.json":
            raise OSError("selection storage unavailable")
        original_write(path, value)

    monkeypatch.setattr(json, "dumps", serialize)
    monkeypatch.setattr(native_workload, "write_text_atomic", write)
    restore = install_workload(
        SimpleNamespace(Env=ConstructedEnv),
        directory=tmp_path,
        dataset_path=source,
        dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        episode_id="7",
        scene_id="scene",
    )
    before = ConstructedEnv.constructions
    try:
        env = ConstructedEnv(config, dataset)
        assert ConstructedEnv.constructions == before + 1
        assert (env.resets, env.steps, env.closes) == (0, 0, 0)
        assert wanted.start_position == [1, 2, 3] and dataset.episodes[0] is wanted
        assert not (tmp_path / "workload-selection.json").exists()
        status = json.loads((tmp_path / "workload-selection-observer.json").read_text())
        assert status["status"] == "unavailable" and status["source"] is None
        assert status["reason"] in {"OSError", "ValueError", "serialization-or-storage-unavailable"}
        assert "private diagnostic detail" not in json.dumps(status)
        assert reads == ([0] if fault == "robot-read" else [])
        assert env.reset() is env.result and env.step("original action") is env.result
    finally:
        restore()


def test_original_constructor_exception_is_not_reclassified(tmp_path: Path) -> None:
    """Selection observation never catches, masks or replays the original constructor fault."""
    source = tmp_path / "dataset.json.gz"
    source.write_bytes(b"original dataset")
    dataset = Dataset([SimpleNamespace(episode_id="7", scene_id="scene")])
    config = SimpleNamespace(dataset=SimpleNamespace(data_path=str(source), split="val"))
    error = RuntimeError("original constructor failure")
    calls: list[Dataset] = []

    class FailingEnv:
        """Fail in the real construction boundary before observation can begin."""

        def __init__(self, config: Any, dataset: Dataset) -> None:
            """Raise the exact original exception after recording one original call."""
            calls.append(dataset)
            raise error

    original = FailingEnv.__init__
    restore = install_workload(
        SimpleNamespace(Env=FailingEnv),
        directory=tmp_path,
        dataset_path=source,
        dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        episode_id="7",
        scene_id="scene",
    )
    try:
        with pytest.raises(RuntimeError) as caught:
            FailingEnv(config, dataset)
        assert caught.value is error
        assert len(calls) == 1 and calls[0].episodes[0] is dataset.episodes[0]
        assert not (tmp_path / "workload-selection-observer.json").exists()
    finally:
        restore()
    assert FailingEnv.__init__ is original


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
