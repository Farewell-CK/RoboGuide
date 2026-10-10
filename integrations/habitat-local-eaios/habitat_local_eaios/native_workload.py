"""Exact native workload selection before original Habitat environment construction.

The native Leader, Stage2, skills, resets, step calls and benchmark metrics remain
original. Selection filters original episode objects, never edits their poses or
the external checkout. Observation uses only existing returned states and metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .diagnostics import PhysicalDiagnostics, _json_scalar
from .evidence_io import write_text_atomic
from .native_reset_observer import install_reset_observer
from .source_provenance import build_runtime_source_manifest


def select_episode(dataset: Any, episode_id: str, scene_id: str) -> Any:
    """Filter the existing dataset exactly, rejecting missing, duplicate or wrong-scene IDs."""
    matches = [episode for episode in dataset.episodes if str(episode.episode_id) == episode_id]
    if len(matches) != 1 or str(matches[0].scene_id) != scene_id:
        raise ValueError("native episode identity is missing, ambiguous or mismatched")
    selected = matches[0]
    result = dataset.filter_episodes(lambda episode: episode is selected)
    if len(result.episodes) != 1 or result.episodes[0] is not selected:
        raise ValueError("native dataset filter did not preserve the original episode")
    return result


def _save(directory: Path, name: str, document: dict[str, Any]) -> None:
    """Isolate observational storage errors; absence never establishes a paired fact."""
    try:
        write_text_atomic(
            directory / name, json.dumps(document, allow_nan=False, default=_json_scalar)
        )
    except Exception:  # noqa: BLE001 - never change the original reset/step result
        return


def install_workload(
    module: Any,
    *,
    directory: Path,
    dataset_path: Path,
    dataset_sha256: str,
    episode_id: str,
    scene_id: str,
) -> Callable[[], None]:
    """Select before the first simulator constructor, retaining one original constructor call."""
    env_type = module.Env
    original = env_type.__init__

    def construct(env: Any, config: Any, dataset: Any = None) -> None:
        """Verify the configured source before loading and filtering original episode objects."""
        habitat = config.habitat if hasattr(config, "habitat") else config
        source = Path(habitat.dataset.data_path.format(split=habitat.dataset.split))
        if source.resolve() != dataset_path.resolve():
            raise ValueError("native configured dataset path changed")
        actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_digest != dataset_sha256:
            raise ValueError("native dataset bytes changed")
        if dataset is None:
            dataset = module.make_dataset(id_dataset=habitat.dataset.type, config=habitat.dataset)
        selected = select_episode(dataset, episode_id, scene_id)
        original(env, config, selected)
        _save(
            directory,
            "workload-selection.json",
            {
                "schema_version": "roboguide.native-workload-selection/v0.1",
                "dataset_path": str(source.resolve()),
                "dataset_sha256": actual_digest,
                "episode_id": str(env.current_episode.episode_id),
                "scene_id": str(env.current_episode.scene_id),
                "selection_boundary": "original dataset filtered before Env.__init__",
                "episode_pose_mutated": False,
                "seed": habitat.seed,
                "max_episode_steps": habitat.environment.max_episode_steps,
                "loaded_robot_types": {
                    f"agent_{index}": type(env.sim.get_agent_data(index).articulated_agent).__name__
                    for index in range(env.sim.num_articulated_agents)
                },
            },
        )

    env_type.__init__ = construct

    def restore() -> None:
        """Restore only this constructor hook without replacing another owner's hook."""
        if env_type.__init__ is construct:
            env_type.__init__ = original

    return restore


def install_execution_observer(env_type: Any, directory: Path) -> Callable[[], None]:
    """Observe the first episode's real reset, steps and terminal cached official metrics."""
    original_reset, original_step, original_close = env_type.reset, env_type.step, env_type.close
    diagnostics: PhysicalDiagnostics | None = None
    reset_count = 0
    steps = 0
    finished = False

    def boundary(env: Any, reason: str, terminal: bool) -> None:
        """Record available state without publishing a metric from an unexecuted episode."""
        nonlocal finished
        if finished or reset_count != 1:
            return
        try:
            metrics = env.get_metrics()
            value = metrics.get("pddl_success")
            try:
                scalar = (
                    value
                    if value is None or type(value) in (bool, int, float)
                    else _json_scalar(value)
                )
            except (TypeError, ValueError):
                scalar = None
            official = bool(scalar) if terminal and steps > 0 and scalar in (False, True) else None
            _save(directory, "native-official-metrics.json", metrics)
            if diagnostics is not None:
                if terminal:
                    diagnostics.record_terminal(env, steps, reason)
                else:
                    diagnostics.record_stop(env, steps, reason, 0)
            _save(
                directory,
                "native-outcome.json",
                {
                    "schema_version": "roboguide.native-execution-outcome/v0.1",
                    "episode_id": str(env.current_episode.episode_id),
                    "scene_id": str(env.current_episode.scene_id),
                    "simulator_steps": steps,
                    "physical_episode_executed": steps > 0,
                    "episode_terminal": terminal,
                    "termination_reason": reason,
                    "official_pddl_success": official,
                    "official_metric_source": "original Env.get_metrics after original Env.step",
                    "official_metrics": {"pddl_success": official},
                    "full_metric_archive": "native-official-metrics.json",
                },
            )
        except Exception:  # noqa: BLE001 - observation never masks an original exception
            pass
        finished = True

    def reset(env: Any, *args: Any, **kwargs: Any) -> Any:
        """Preserve original reset calls and observe only the first real returned world."""
        nonlocal reset_count, diagnostics
        result = original_reset(env, *args, **kwargs)
        reset_count += 1
        if reset_count == 1:
            try:
                diagnostics = PhysicalDiagnostics(
                    directory / "physical", tuple(range(env.sim.num_articulated_agents)), True, 5000
                )
                diagnostics.record_reset(env, env._config)
            except Exception:  # noqa: BLE001 - initialization is optional observation
                diagnostics = None
        return result

    def step(env: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute one original step and preserve its return value and exception."""
        nonlocal steps
        try:
            result = original_step(env, *args, **kwargs)
        except BaseException as error:
            boundary(env, "original-step-exception:" + type(error).__name__, False)
            raise
        if reset_count == 1 and not finished:
            steps += 1
            try:
                if diagnostics is not None:
                    diagnostics.record_step(
                        steps,
                        [],
                        args[0] if args else kwargs,
                        env,
                        None,
                        env.episode_over,
                        env.get_metrics(),
                        result,
                    )
                if env.episode_over:
                    boundary(env, "original-episode-over", True)
            except Exception:  # noqa: BLE001 - reads cannot change physical execution
                pass
        return result

    def close(env: Any, *args: Any, **kwargs: Any) -> Any:
        """Save unfinished evidence before original close, retaining original close exceptions."""
        boundary(env, "original-close-before-episode-terminal", False)
        return original_close(env, *args, **kwargs)

    env_type.reset, env_type.step, env_type.close = reset, step, close

    def restore() -> None:
        """Restore only the hooks installed by this observer."""
        for name, wrapper, previous in (
            ("reset", reset, original_reset),
            ("step", step, original_step),
            ("close", close, original_close),
        ):
            if getattr(env_type, name) is wrapper:
                setattr(env_type, name, previous)

    return restore


class SelectedEnvFactory:
    """Serializable selection and observation installed in the original native worker."""

    def __init__(
        self,
        original: Callable[..., Any],
        directory: Path,
        run_id: str,
        dataset_path: Path,
        dataset_sha256: str,
        episode_id: str,
        scene_id: str,
    ) -> None:
        """Freeze public workload identity without constructing an environment in the parent."""
        self.original = original
        self.directory = directory
        self.run_id = run_id
        self.dataset_path = dataset_path
        self.dataset_sha256 = dataset_sha256
        self.episode_id = episode_id
        self.scene_id = scene_id

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Fail closed on selection errors, then construct the unchanged original environment."""
        directory = self.directory / f"worker-{os.getpid()}"
        directory.mkdir(parents=True, exist_ok=False)
        module = importlib.import_module("habitat.core.env")
        install_workload(
            module,
            directory=directory,
            dataset_path=self.dataset_path,
            dataset_sha256=self.dataset_sha256,
            episode_id=self.episode_id,
            scene_id=self.scene_id,
        )
        install_reset_observer(module.Env, directory, self.run_id, self.episode_id)
        install_execution_observer(module.Env, directory)
        return self.original(*args, **kwargs)


def main(argv: list[str] | None = None) -> None:
    """Run the original evaluator with exact selection and bounded run-local observation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--dataset-path", type=Path, required=True)
    parser.add_argument("--dataset-sha256", required=True)
    parser.add_argument("native_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    args.evidence_dir.mkdir(parents=True, exist_ok=False)
    factory: Any = importlib.import_module("habitat_baselines.common.habitat_env_factory")
    original = factory.make_gym_from_config
    factory.make_gym_from_config = SelectedEnvFactory(
        original,
        args.evidence_dir,
        args.run_id,
        args.dataset_path,
        args.dataset_sha256,
        args.episode_id,
        args.scene_id,
    )
    previous_argv = sys.argv
    native_arguments = args.native_arguments
    sys.argv = [
        "habitat_baselines.run",
        *(native_arguments[1:] if native_arguments[:1] == ["--"] else native_arguments),
    ]
    try:
        runpy.run_module("habitat_baselines.run", run_name="__main__", alter_sys=True)
    finally:
        try:
            _save(
                args.evidence_dir,
                "runtime-source-manifest.json",
                build_runtime_source_manifest(
                    (
                        "habitat.tasks.rearrange.actions.habitat_mas_actions",
                        "habitat.tasks.rearrange.actions.oracle_nav_action",
                        "habitat_sim",
                        "habitat_sim._ext.habitat_sim_bindings",
                        "habitat_baselines.rl.hrl.hl.llm_policy",
                        "habitat_baselines.rl.hrl.skills.wait",
                        "habitat_baselines.rl.multi_agent.multi_agent_access_mgr",
                        "habitat_baselines.rl.multi_agent.multi_llm_policy",
                        "habitat_mas.agents.crab_agent",
                        "habitat_mas.utils.models",
                    )
                ),
            )
        except Exception:  # noqa: BLE001 - source observation cannot mask original failure
            pass
        factory.make_gym_from_config = original
        sys.argv = previous_argv


if __name__ == "__main__":
    main()
