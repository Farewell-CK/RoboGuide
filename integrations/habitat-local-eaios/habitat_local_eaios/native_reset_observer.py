"""Opt-in post-reset evidence for the original EMOS single-episode evaluator.

The entry point decorates its existing environment factory, so the observer
also reaches forkserver/spawn workers. It never edits the external checkout,
changes the multiprocessing mode, or adds reset, action, step or RNG calls.
Each worker preserves its first real reset; later automatic resets cannot
overwrite that initial snapshot. Missing evidence never proves a paired start.
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

from .diagnostics import PhysicalDiagnostics
from .evidence_io import write_text_atomic

OBSERVER_SCHEMA = "roboguide.native-reset-observation/v0.1"


def _write_status(directory: Path, document: dict[str, Any]) -> None:
    """Best-effort persist bounded observer metadata without affecting the evaluator."""
    try:
        write_text_atomic(directory / "reset-observer.json", json.dumps(document, allow_nan=False))
    except Exception:  # noqa: BLE001 - even storage failure cannot change execution
        return


def install_reset_observer(
    env_type: Any,
    directory: Path,
    run_id: str,
    expected_episode_id: str,
) -> Callable[[], None]:
    """Observe the first reset attempt, preserving its return value and exceptions.

    Callers own an unused worker directory. The restoration handle is useful
    for isolated tests; native worker processes keep the observer until exit.
    PhysicalDiagnostics supplies the same bounded, read-only pose/goal reader
    as the RoboGuide arm. Native simulator RNG state remains unavailable.
    """
    original = env_type.reset
    reset_attempts = 0
    resets_observed = 0
    document: dict[str, Any] = {
        "schema_version": OBSERVER_SCHEMA,
        "run_id": run_id,
        "expected_episode_id": expected_episode_id,
        "observation_boundary": "Habitat Env.reset returned after task and measure reset",
        "resets_observed": 0,
        "reset_attempts": 0,
        "later_resets_ignored": 0,
        "reset_failures": 0,
        "status": "unavailable",
        "reason": "no completed reset observed",
        "simulator_rng_state": {
            "status": "unavailable",
            "reason": "no admitted read-only simulator RNG accessor",
        },
    }
    _write_status(directory, document)

    def observe_reset(env: Any, *args: Any, **kwargs: Any) -> Any:
        """Call the original reset once, then record evidence without changing its result."""
        nonlocal reset_attempts, resets_observed
        reset_attempts += 1
        document["reset_attempts"] = reset_attempts
        try:
            result = original(env, *args, **kwargs)
        except Exception as error:
            document["reset_failures"] += 1
            document["later_resets_ignored"] = max(0, reset_attempts - 1)
            if reset_attempts == 1:
                document.update(
                    status="unavailable",
                    reason=f"original reset did not return: {type(error).__name__}",
                )
            _write_status(directory, document)
            raise
        resets_observed += 1
        document["resets_observed"] = resets_observed
        if reset_attempts > 1:
            document["later_resets_ignored"] = reset_attempts - 1
            _write_status(directory, document)
            return result
        try:
            agent_count = env.sim.num_articulated_agents
            if type(agent_count) is not int or not 1 <= agent_count <= 128:
                raise ValueError("articulated agent count is unavailable or out of bounds")
            diagnostics = PhysicalDiagnostics(directory, tuple(range(agent_count)), True, 0)
            diagnostics.record_reset(env, env._config)
            initial_path = directory / "diagnostics-initial.json"
            raw = initial_path.read_bytes()
            initial = json.loads(raw)
            if not isinstance(initial, dict) or initial.get("_status") == "unavailable":
                raise ValueError("post-reset snapshot unavailable")
            document.update(
                status="available",
                reason=None,
                episode_id=initial.get("episode_id"),
                scene_id=initial.get("scene_id"),
                episode_matches_requested=initial.get("episode_id") == expected_episode_id,
                initial_snapshot="diagnostics-initial.json",
                initial_snapshot_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
        except Exception as error:  # noqa: BLE001 - isolate reads, serialization and storage
            document.update(status="unavailable", reason=type(error).__name__)
        _write_status(directory, document)
        return result

    env_type.reset = observe_reset

    def restore() -> None:
        """Remove only this observer without overwriting another installed wrapper."""
        if env_type.reset is observe_reset:
            env_type.reset = original

    return restore


class ObservedEnvFactory:
    """Serializable decorator of the original factory used inside native workers."""

    def __init__(
        self,
        original: Callable[..., Any],
        directory: Path,
        run_id: str,
        expected_episode_id: str,
    ) -> None:
        """Retain the original factory and frozen public observation identity."""
        self._original = original
        self._directory = directory
        self._run_id = run_id
        self._episode_id = expected_episode_id

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Install the observer in this worker, then construct its original environment once."""
        directory = self._directory / f"worker-{os.getpid()}"
        try:
            directory.mkdir(parents=True, exist_ok=False)
            env_module = importlib.import_module("habitat.core.env")
            install_reset_observer(env_module.Env, directory, self._run_id, self._episode_id)
        except Exception as error:  # noqa: BLE001 - initialization is observational only
            _write_status(
                directory,
                {
                    "schema_version": OBSERVER_SCHEMA,
                    "run_id": self._run_id,
                    "status": "unavailable",
                    "reason": f"observer initialization: {type(error).__name__}",
                },
            )
        return self._original(*args, **kwargs)


def main(argv: list[str] | None = None) -> None:
    """Run the unchanged native evaluator with explicit, run-local reset observation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("native_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    args.evidence_dir.mkdir(parents=True, exist_ok=False)
    factory: Any = importlib.import_module("habitat_baselines.common.habitat_env_factory")
    original = factory.make_gym_from_config
    factory.make_gym_from_config = ObservedEnvFactory(
        original, args.evidence_dir, args.run_id, args.episode_id
    )
    native_arguments = args.native_arguments
    if native_arguments[:1] == ["--"]:
        native_arguments = native_arguments[1:]
    previous_argv = sys.argv
    sys.argv = ["habitat_baselines.run", *native_arguments]
    try:
        runpy.run_module("habitat_baselines.run", run_name="__main__", alter_sys=True)
    finally:
        factory.make_gym_from_config = original
        sys.argv = previous_argv


if __name__ == "__main__":
    main()
