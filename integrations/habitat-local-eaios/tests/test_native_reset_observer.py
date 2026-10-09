"""Deterministic reset-boundary and subprocess tests without Habitat or Providers."""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.native_reset_observer import (  # noqa: E402
    ObservedEnvFactory,
    install_reset_observer,
)


class FakeEnv:
    """Provide actual post-reset pose properties and untouched cached metrics."""

    def __init__(self) -> None:
        """Keep reset, step and act counters separate from observer reads."""
        self.resets = 0
        self.steps = 0
        self.acts = 0
        self.current_episode = SimpleNamespace(episode_id="8", scene_id="scene-real")
        self._config = SimpleNamespace(seed=9)
        self.agent = SimpleNamespace(
            base_pos=[0.0, 1.0, 2.0],
            base_rot=0.75,
            base_transformation=SimpleNamespace(translation=[0.0, 1.5, 2.0]),
        )
        self.sim = SimpleNamespace(num_articulated_agents=1, get_agent_data=self.agent_data)
        self.task = SimpleNamespace(pddl_problem=None)
        self.observations: dict[str, object] = {"original": True}

    def agent_data(self, agent_id: int) -> Any:
        """Return the actual articulated agent without creating a new state."""
        assert agent_id == 0
        return SimpleNamespace(articulated_agent=self.agent)

    def get_metrics(self) -> dict[str, object]:
        """Read the official measurement cache without recomputing metrics."""
        return {"pddl_success": False}

    def reset(self, marker: str = "reset") -> dict[str, object]:
        """Change pose once as the original reset would and return the original object."""
        assert marker == "reset"
        self.resets += 1
        self.agent.base_pos[0] = float(self.resets)
        return self.observations


def original_factory(marker: str) -> FakeEnv:
    """Stand in for the unchanged vendor factory, preserving its arguments."""
    assert marker == "factory-argument"
    return FakeEnv()


def worker_probe(factory: ObservedEnvFactory) -> None:
    """Emulate a fresh spawned native worker whose parent has no reset patch."""
    module = ModuleType("habitat.core.env")
    module.Env = FakeEnv  # type: ignore[attr-defined]
    sys.modules["habitat.core.env"] = module
    env = factory("factory-argument")
    assert env.reset() is env.observations
    env.reset()
    assert env.resets == 2 and env.steps == env.acts == 0


def test_observer_captures_first_actual_pose_without_auto_reset_overwrite(tmp_path: Path) -> None:
    """Record returned state once; preserve action counts and the original observation object."""
    original = FakeEnv.reset
    restore = install_reset_observer(FakeEnv, tmp_path, "run", "8")
    try:
        env = FakeEnv()
        assert env.reset() is env.observations
        initial_bytes = (tmp_path / "diagnostics-initial.json").read_bytes()
        env.reset()
        assert (tmp_path / "diagnostics-initial.json").read_bytes() == initial_bytes
        initial = json.loads(initial_bytes)
        assert initial["agents"]["0"]["position"] == [1.0, 1.0, 2.0]
        assert initial["agents"]["0"]["rotation"]["value"] == 0.75
        status = json.loads((tmp_path / "reset-observer.json").read_text())
        assert status["status"] == "available"
        assert status["later_resets_ignored"] == 1
        assert status["episode_matches_requested"] is True
        assert env.resets == 2 and env.steps == env.acts == 0
    finally:
        restore()
    assert FakeEnv.reset is original


@pytest.mark.parametrize("start_method", ["spawn", "forkserver"])
def test_spawned_worker_installs_its_own_observer(tmp_path: Path, start_method: str) -> None:
    """Install observation in fresh workers without depending on parent globals."""
    factory = ObservedEnvFactory(original_factory, tmp_path, "spawn-run", "8")
    context: Any = multiprocessing.get_context(start_method)
    process = context.Process(target=worker_probe, args=(factory,))
    process.start()
    try:
        process.join(timeout=20)
        assert process.exitcode == 0
        paths = list(tmp_path.glob("worker-*/diagnostics-initial.json"))
        assert len(paths) == 1
        assert json.loads(paths[0].read_text())["agents"]["0"]["position"] == [1.0, 1.0, 2.0]
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        process.close()


def test_unexpected_episode_is_recorded_not_replaced(tmp_path: Path) -> None:
    """Identity disagreement is retained for pair rejection, never fixed by another reset."""
    restore = install_reset_observer(FakeEnv, tmp_path, "run", "99")
    try:
        env = FakeEnv()
        env.reset()
        status = json.loads((tmp_path / "reset-observer.json").read_text())
        assert status["episode_id"] == "8"
        assert status["episode_matches_requested"] is False
        assert env.resets == 1
    finally:
        restore()


def test_snapshot_read_or_storage_failure_does_not_change_reset(tmp_path: Path) -> None:
    """Unreadable simulator state remains unavailable while the original reset succeeds."""
    restore = install_reset_observer(FakeEnv, tmp_path, "run", "8")
    try:
        env = FakeEnv()
        env.sim.num_articulated_agents = None
        assert env.reset() is env.observations
        assert env.resets == 1
        assert json.loads((tmp_path / "reset-observer.json").read_text())["status"] == "unavailable"
    finally:
        restore()
    restore = install_reset_observer(FakeEnv, tmp_path / "missing-directory", "run", "8")
    try:
        env = FakeEnv()
        assert env.reset() is env.observations
        assert env.resets == 1
    finally:
        restore()


def test_original_reset_exception_is_preserved(tmp_path: Path) -> None:
    """Observer failure metadata cannot swallow or replace an original reset exception."""
    error = RuntimeError("original reset failed")

    class BrokenEnv(FakeEnv):
        """Fail at the original environment boundary."""

        def reset(self, marker: str = "reset") -> dict[str, object]:
            """Raise the same physical initialization exception every time."""
            raise error

    restore = install_reset_observer(BrokenEnv, tmp_path, "run", "8")
    try:
        with pytest.raises(RuntimeError) as caught:
            BrokenEnv().reset()
        assert caught.value is error
        assert json.loads((tmp_path / "reset-observer.json").read_text())["status"] == "unavailable"
    finally:
        restore()


def test_observer_initialization_failure_preserves_original_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed worker observer does not prevent original environment construction."""

    def fail_import(name: str) -> Any:
        """Simulate a missing observation integration without invoking vendor code."""
        raise ImportError("missing observer dependency")

    monkeypatch.setattr(
        "habitat_local_eaios.native_reset_observer.importlib.import_module", fail_import
    )
    env = ObservedEnvFactory(original_factory, tmp_path, "run", "8")("factory-argument")
    assert isinstance(env, FakeEnv)
    assert env.resets == env.steps == env.acts == 0
    paths = list(tmp_path.glob("worker-*/reset-observer.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["status"] == "unavailable"


def test_failed_first_reset_cannot_be_replaced_by_a_later_success(tmp_path: Path) -> None:
    """A later world reset cannot manufacture missing evidence for the first attempt."""

    class FlakyEnv(FakeEnv):
        """Fail the first original reset and allow a later one."""

        def __init__(self) -> None:
            """Start with one pending original initialization failure."""
            super().__init__()
            self.fail_next = True

        def reset(self, marker: str = "reset") -> dict[str, object]:
            """Preserve the failure before allowing the original successful reset."""
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("first reset failed")
            return super().reset(marker)

    restore = install_reset_observer(FlakyEnv, tmp_path, "run", "8")
    try:
        env = FlakyEnv()
        with pytest.raises(RuntimeError):
            env.reset()
        assert env.reset() is env.observations
        status = json.loads((tmp_path / "reset-observer.json").read_text())
        assert status["status"] == "unavailable"
        assert status["reset_attempts"] == 2
        assert status["reset_failures"] == 1
        assert not (tmp_path / "diagnostics-initial.json").exists()
    finally:
        restore()


def test_native_entry_restores_factory_and_arguments_on_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forward exact evaluator arguments and preserve its original exception and factory."""
    from habitat_local_eaios.native_reset_observer import main

    factory_module = SimpleNamespace(make_gym_from_config=original_factory)
    original_argv = sys.argv
    error = RuntimeError("original evaluator failure")

    def module_lookup(name: str) -> Any:
        """Return the native factory stub for this isolated entry-point test."""
        assert name == "habitat_baselines.common.habitat_env_factory"
        return factory_module

    def original_evaluator(name: str, **kwargs: Any) -> None:
        """Check the unmodified argument list before simulating evaluator failure."""
        assert name == "habitat_baselines.run"
        assert sys.argv == [
            "habitat_baselines.run",
            "--config-name=original.yaml",
            "habitat.seed=9",
        ]
        assert isinstance(factory_module.make_gym_from_config, ObservedEnvFactory)
        raise error

    monkeypatch.setattr(
        "habitat_local_eaios.native_reset_observer.runpy.run_module", original_evaluator
    )
    monkeypatch.setattr(
        "habitat_local_eaios.native_reset_observer.importlib.import_module", module_lookup
    )
    with pytest.raises(RuntimeError) as caught:
        main(
            [
                "--evidence-dir",
                str(tmp_path / "unused"),
                "--run-id",
                "run",
                "--episode-id",
                "8",
                "--",
                "--config-name=original.yaml",
                "habitat.seed=9",
            ]
        )
    assert caught.value is error
    assert factory_module.make_gym_from_config is original_factory
    assert sys.argv is original_argv
