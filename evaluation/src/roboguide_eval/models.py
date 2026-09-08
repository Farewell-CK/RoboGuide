"""Versioned ExperimentSpec domain values for the RoboGuide Eval Harness.

This module owns the portable, machine-independent description of an
experiment: what is being evaluated, on which benchmark and episodes, with
which systems under test, model configuration, seeds, and canonical metrics.
It deliberately knows nothing about how any external system is launched;
process-level details live in :mod:`roboguide_eval.process` and machine
specific overrides live in :mod:`roboguide_eval.config`.

The Eval Harness is independent evaluation infrastructure. It is not part of
RoboGuide Core, Runtime, Control Plane, State & Memory Plane, or any Local
EAIOS, and it never changes Proposal/Commit/Binding/Runtime semantics.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

EXPERIMENT_SPEC_VERSION: Final = "roboguide-eval.experiment-spec/v0.1"
EXPERIMENT_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
NAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# File names the harness itself owns inside every run directory; declared
# paths from an ExperimentSpec (metrics sources, expected outputs) must never
# target them.
RESERVED_RUN_FILE_NAMES: Final = frozenset(
    {"manifest.json", "metrics.json", "trace.jsonl", "stdout.log", "stderr.log"}
)


class ExperimentSpecError(ValueError):
    """Report an invalid or unsafe experiment specification."""


def is_safe_run_relative_path(relative_path: str) -> bool:
    """Check that one experiment-declared path stays inside its run directory.

    Args:
        relative_path: A run-directory-relative path declared by an
            ExperimentSpec, for example a metrics source or expected output.

    Returns:
        ``True`` when the path is relative, contains no parent traversal,
        and does not target a harness-owned run file; ``False`` otherwise.
    """
    if not relative_path or relative_path in RESERVED_RUN_FILE_NAMES:
        return False
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    return True


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Identify the benchmark dataset behind an experiment when it is known.

    All fields are optional because dataset identity, revision, and content
    digest are frequently only discoverable at run time; the run manifest
    records whatever is available so runs stay traceable.
    """

    name: str | None = None
    revision: str | None = None
    digest: str | None = None

    def identity(self) -> str | None:
        """Return a compact ``name@revision`` identity label or ``None``.

        Returns:
            A human-readable dataset identity combining the available fields,
            or ``None`` when the dataset carries no identity at all.
        """
        if self.name is None:
            return None
        if self.revision is None:
            return self.name
        return f"{self.name}@{self.revision}"


@dataclass(frozen=True, slots=True)
class LlmSpec:
    """Describe the requested LLM provider, model alias, and reasoning setup.

    ``model`` is the configured alias, not the identifier the provider API
    actually returned; the run manifest keeps a separate ``reported_model``
    field reserved for the concrete returned identifier.
    """

    provider: str
    model: str
    reasoning_effort: str | None = None
    reasoning_options: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    """Describe the portable, experiment-owned expectations for one system.

    This is the experiment-side half of an external environment reference:
    what artifacts the system under test is expected to produce inside a run
    directory and any per-experiment timeout override. Machine-specific
    launch details (working directory, executable, Conda environment,
    credentials) never appear here; they are supplied by local configuration
    at resolution time.
    """

    name: str
    timeout_seconds: float | None = None
    metrics_source_path: str | None = None
    expected_output_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """Describe one evaluation experiment independent of any machine setup.

    The spec is the unit of reproducibility for the harness: its canonical
    serialization is digested into every run manifest so that a run can always
    be attributed to the exact experiment configuration that produced it.
    """

    experiment_id: str
    benchmark: str
    task: str
    dataset: DatasetSpec
    context: Mapping[str, str]
    episodes: tuple[str, ...]
    systems: tuple[str, ...]
    llm: LlmSpec
    seeds: tuple[int, ...]
    metrics: tuple[str, ...]
    timeout_seconds: float
    environments: Mapping[str, EnvironmentSpec]
    description: str = ""

    def environment(self, system: str) -> EnvironmentSpec:
        """Return the portable environment expectations for one system.

        Args:
            system: System under test name declared in ``systems``.

        Returns:
            The declared :class:`EnvironmentSpec`, or an empty default when
            the experiment declares no environment entry for the system.

        Raises:
            ExperimentSpecError: If ``system`` is not part of the experiment.
        """
        if system not in self.systems:
            raise ExperimentSpecError(
                f"system {system!r} is not declared by experiment {self.experiment_id!r}"
            )
        return self.environments.get(system, EnvironmentSpec(name=system))

    def selected_episodes(self, episode_filter: tuple[str, ...]) -> tuple[str, ...]:
        """Return spec episodes intersected with an optional CLI override.

        Args:
            episode_filter: Episodes requested on the command line; empty
                means "use every declared episode".

        Returns:
            Declared episodes in declaration order, filtered to the override.

        Raises:
            ExperimentSpecError: If an override selects an undeclared episode.
        """
        if not episode_filter:
            return self.episodes
        unknown = [episode for episode in episode_filter if episode not in self.episodes]
        if unknown:
            raise ExperimentSpecError(f"episodes not declared by the spec: {unknown}")
        return tuple(episode for episode in self.episodes if episode in episode_filter)

    def selected_seeds(self, seed_filter: tuple[int, ...]) -> tuple[int, ...]:
        """Return spec seeds intersected with an optional CLI override.

        Args:
            seed_filter: Seeds requested on the command line; empty means
                "use every declared seed".

        Returns:
            Declared seeds in declaration order, filtered to the override.

        Raises:
            ExperimentSpecError: If an override selects an undeclared seed.
        """
        if not seed_filter:
            return self.seeds
        unknown = [seed for seed in seed_filter if seed not in self.seeds]
        if unknown:
            raise ExperimentSpecError(f"seeds not declared by the spec: {unknown}")
        return tuple(seed for seed in self.seeds if seed in seed_filter)


def experiment_spec_to_json(spec: ExperimentSpec) -> JSONObject:
    """Serialize an :class:`ExperimentSpec` into its canonical JSON object.

    The serialization is intentionally key-order independent and contains no
    machine-specific data, so its SHA-256 digest is a stable identity for the
    experiment configuration.

    Args:
        spec: The validated experiment specification.

    Returns:
        A JSON object with sorted-stable content for digest computation.
    """
    return {
        "schema": EXPERIMENT_SPEC_VERSION,
        "experiment_id": spec.experiment_id,
        "description": spec.description,
        "benchmark": spec.benchmark,
        "task": spec.task,
        "dataset": {
            "name": spec.dataset.name,
            "revision": spec.dataset.revision,
            "digest": spec.dataset.digest,
        },
        "context": dict(sorted(spec.context.items())),
        "episodes": list(spec.episodes),
        "systems": list(spec.systems),
        "llm": {
            "provider": spec.llm.provider,
            "model": spec.llm.model,
            "reasoning_effort": spec.llm.reasoning_effort,
            "reasoning_options": dict(sorted((spec.llm.reasoning_options or {}).items())),
        },
        "seeds": list(spec.seeds),
        "metrics": list(spec.metrics),
        "timeout_seconds": spec.timeout_seconds,
        "environments": {
            name: {
                "timeout_seconds": environment.timeout_seconds,
                "metrics_source_path": environment.metrics_source_path,
                "expected_output_paths": list(environment.expected_output_paths),
            }
            for name, environment in sorted(spec.environments.items())
        },
    }
