"""SystemRunner lifecycle and experiment orchestration.

Every system under test goes through the same four-phase lifecycle:

1. ``prepare`` — validate readiness and probe the external system version;
2. ``run_episode`` — launch one configured external process for a single
   (episode, seed) pair and persist its evidence;
3. ``collect_result`` — convert the system's raw output into the canonical
   metric schema, keeping pointers to the untouched raw evidence;
4. ``cleanup`` — shut down anything the runner still owns.

The base implementation :class:`ProcessSystemRunner` is real infrastructure:
it substitutes command placeholders, launches the process through
:class:`~roboguide_eval.process.ProcessManager`, writes manifests, traces,
and metrics, and guarantees evidence on failure. What is intentionally *not*
implemented yet is the official EMOS command mapping and the RoboGuide
Controller-driving path; those arrive as configured commands and dedicated
runners in later slices, without changing this lifecycle.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from re import Match
from typing import Final

from roboguide_eval.config import (
    EnvironmentOverrides,
    load_local_config,
    resolve_process_spec,
    spec_digest,
)
from roboguide_eval.metrics import (
    METRICS_SCHEMA,
    MetricsError,
    MetricsPayload,
    RawEvidenceRef,
)
from roboguide_eval.models import (
    EnvironmentSpec,
    ExperimentSpec,
    JSONObject,
    is_safe_run_relative_path,
)
from roboguide_eval.process import (
    ProcessManager,
    ProcessOutcome,
    ProcessSpec,
    read_text_output,
)
from roboguide_eval.results import (
    RunArtifactWriter,
    RunManifest,
    create_run_directory,
    current_git_commit,
    harness_environment_information,
    new_run_id,
    utc_now_iso,
)

COMMAND_PLACEHOLDERS: Final = frozenset(
    {
        "experiment_id",
        "system",
        "benchmark",
        "task",
        "episode_id",
        "seed",
        "model",
        "model_provider",
        "reasoning_effort",
        "run_id",
        "output_dir",
    }
)
# ``{name}`` placeholders must not match inside ``${NAME}`` credential
# references, so the pattern rejects a preceding dollar sign.
_PLACEHOLDER_PATTERN: Final = re.compile(r"(?<!\$)\{([a-z_][a-z0-9_]*)\}")
# ``${name}``-shaped tokens in argv are shell-style typos: argv entries are
# never environment-expanded, so they must fail closed instead of reaching
# the child as literal garbage.
_DOLLAR_PLACEHOLDER_PATTERN: Final = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")


class SystemRunnerError(ValueError):
    """Report an unknown system under test or a failed runner preparation."""


class CommandBuildError(ValueError):
    """Report an unresolved or malformed command placeholder."""


def substitute_text(text: str, values: Mapping[str, object]) -> str:
    """Substitute known ``{name}`` placeholders in one configured string.

    Substitution is single-pass and limited to exact ``{name}`` tokens with
    lowercase-leading identifier names. A token of that shape whose name is
    not declared fails closed. Brace content in any other shape — quoted
    keys, colons, capital letters — passes through verbatim, and ``${NAME}``
    credential references are never treated as placeholders (the dollar sign
    shields them), so values like ``"${OPENAI_API_KEY}"`` survive untouched
    for spawn-time environment expansion.

    Args:
        text: One configured string (an argv entry or an environment value).
        values: Placeholder values keyed by placeholder name.

    Returns:
        The substituted string.

    Raises:
        CommandBuildError: If the text references a lowercase
            ``{name}``-shaped token whose name is not part of the supplied
            values.
    """

    def replace(match: Match[str]) -> str:
        """Replace one matched placeholder with its value.

        Args:
            match: The regex match carrying the placeholder name.

        Returns:
            The string form of the placeholder value.

        Raises:
            CommandBuildError: If the name is not in the supplied values.
        """
        name = match.group(1)
        if name not in values:
            supported = sorted(COMMAND_PLACEHOLDERS)
            raise CommandBuildError(
                f"unknown command placeholder {{{name}}}; supported: {supported}"
            )
        return str(values[name])

    return _PLACEHOLDER_PATTERN.sub(replace, text)


def substitute_placeholders(argv: tuple[str, ...], values: Mapping[str, object]) -> tuple[str, ...]:
    """Substitute known ``{name}`` placeholders in a configured argv list.

    Applies :func:`substitute_text` to every argument; see that function for
    the exact token shape and fail-closed behavior. argv entries are never
    environment-expanded, so a shell-style ``${name}`` token in argv is
    rejected explicitly — it is always a typo for ``{name}`` and would
    otherwise reach the child process as literal garbage.

    Args:
        argv: The configured argument list.
        values: Placeholder values keyed by placeholder name.

    Returns:
        The substituted argv list.

    Raises:
        CommandBuildError: If an argument references an unknown placeholder
            or uses a ``${name}`` environment-reference shape.
    """
    for argument in argv:
        dollar_match = _DOLLAR_PLACEHOLDER_PATTERN.search(argument)
        if dollar_match is not None:
            name = dollar_match.group(1)
            raise CommandBuildError(
                f"argv entries do not support ${{{name}}} environment references; "
                f"use the {{{name}}} placeholder instead: {argument!r}"
            )
    return tuple(substitute_text(argument, values) for argument in argv)


@dataclass(frozen=True, slots=True)
class PreparedSystem:
    """Carry the outcome of runner preparation into episode runs.

    ``system_version`` comes from the configured version probe command (for
    example ``git rev-parse HEAD`` inside the EMOS checkout) when available.
    """

    system: str
    process_spec: ProcessSpec
    environment_spec: EnvironmentSpec
    system_version: str | None
    config_digest: str


@dataclass(frozen=True, slots=True)
class RunResult:
    """Hold the manifest, canonical metrics, and raw process outcome of a run."""

    manifest: RunManifest
    metrics: MetricsPayload
    outcome: ProcessOutcome

    @property
    def succeeded(self) -> bool:
        """Report whether the episode process completed successfully.

        Returns:
            ``True`` only when the underlying process succeeded.
        """
        return self.outcome.succeeded


def backfill_wall_time(payload: MetricsPayload, outcome: ProcessOutcome) -> MetricsPayload:
    """Fill the canonical ``wall_time`` metric from the measured duration.

    Systems under test frequently do not report their own wall time. When the
    payload lacks the metric, the harness records the process duration it
    measured itself and marks the source in ``details`` so downstream
    analysis can distinguish system-reported from harness-measured values.

    Args:
        payload: The collected canonical metrics payload.
        outcome: The observed process outcome carrying the duration.

    Returns:
        The payload, unchanged when ``wall_time`` is already reported,
        otherwise a new payload with the harness-measured value added.
    """
    if "wall_time" in payload.values:
        return payload
    values = dict(payload.values)
    details = dict(payload.details)
    values["wall_time"] = round(outcome.duration_seconds, 3)
    details["wall_time_source"] = "harness_process_duration"
    return MetricsPayload(values=values, details=details, raw_evidence=payload.raw_evidence)


class ProcessSystemRunner:
    """Generic process-backed implementation of the SystemRunner lifecycle.

    Subclasses name the system and may add system-specific behavior later;
    the lifecycle, evidence handling, and process boundary defined here are
    shared and real.
    """

    system: str = "unset"

    def __init__(
        self,
        *,
        process_manager: ProcessManager,
        environment: Mapping[str, str],
        repository_root: Path,
    ) -> None:
        """Create a runner.

        Args:
            process_manager: Manager used for every child process.
            environment: Harness environment used for ``${VAR}`` expansion
                and provenance overrides.
            repository_root: RoboGuide checkout used for Git provenance.
        """
        self._process_manager = process_manager
        self._environment = environment
        self._repository_root = repository_root

    def prepare(
        self,
        experiment: ExperimentSpec,
        process_spec: ProcessSpec,
        environment_spec: EnvironmentSpec,
    ) -> PreparedSystem:
        """Validate readiness and probe the external system version.

        Args:
            experiment: The experiment being prepared.
            process_spec: The resolved process specification.
            environment_spec: Portable environment expectations for the system.

        Returns:
            The prepared-system handle carried into episode runs.

        Raises:
            SystemRunnerError: If a configured readiness command fails or the
                version probe cannot be executed.
        """
        if process_spec.readiness_command is not None:
            readiness_spec = ProcessSpec(
                argv=process_spec.readiness_command,
                working_directory=process_spec.working_directory,
                environment_overrides=process_spec.environment_overrides,
                timeout_seconds=min(process_spec.timeout_seconds, 300.0),
            )
            with tempfile.TemporaryDirectory() as temporary:
                temporary_path = Path(temporary)
                outcome = self._process_manager.run(
                    readiness_spec,
                    stdout_path=temporary_path / "readiness.out",
                    stderr_path=temporary_path / "readiness.err",
                    environment=self._environment,
                )
            if not outcome.succeeded:
                raise SystemRunnerError(
                    f"readiness check for system {self.system!r} failed: {outcome.failure_reason()}"
                )
        system_version = self._probe_version(process_spec)
        return PreparedSystem(
            system=self.system,
            process_spec=process_spec,
            environment_spec=environment_spec,
            system_version=system_version,
            config_digest=spec_digest(experiment),
        )

    def _probe_version(self, process_spec: ProcessSpec) -> str | None:
        """Run the configured version probe and return its first output line.

        Args:
            process_spec: The resolved process specification carrying the
                optional probe command.

        Returns:
            The first nonblank stdout line, or ``None`` when no probe is
            configured.

        Raises:
            SystemRunnerError: If a configured probe fails.
        """
        if process_spec.version_probe_command is None:
            return None
        probe_spec = ProcessSpec(
            argv=process_spec.version_probe_command,
            working_directory=process_spec.working_directory,
            environment_overrides=process_spec.environment_overrides,
            timeout_seconds=60.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            outcome = self._process_manager.run(
                probe_spec,
                stdout_path=temporary_path / "version-probe.out",
                stderr_path=temporary_path / "version-probe.err",
                environment=self._environment,
            )
            probe_output = read_text_output(outcome.stdout_path)
        if not outcome.succeeded:
            raise SystemRunnerError(
                f"version probe for system {self.system!r} failed: {outcome.failure_reason()}"
            )
        first_line = next(
            (line.strip() for line in probe_output.splitlines() if line.strip()),
            None,
        )
        return first_line

    def run_episode(
        self,
        experiment: ExperimentSpec,
        prepared: PreparedSystem,
        *,
        episode_id: str,
        seed: int,
        results_root: Path,
    ) -> RunResult:
        """Run one (episode, seed) pair as a fully evidenced run directory.

        The episode always produces a manifest, persisted logs, trace events,
        and a metrics document — even when the external process fails.

        Args:
            experiment: The experiment being executed.
            prepared: The prepared-system handle from :meth:`prepare`.
            episode_id: Declared episode to run.
            seed: Declared seed to run.
            results_root: Root directory receiving the run directory.

        Returns:
            The :class:`RunResult` for this episode.

        Raises:
            CommandBuildError: If the configured command uses unknown
                placeholders.
        """
        run_id = new_run_id()
        run_directory = create_run_directory(results_root, experiment.experiment_id, run_id)
        writer = RunArtifactWriter(run_directory)
        started_at = utc_now_iso()
        writer.trace.append(
            "run_started",
            {
                "experiment_id": experiment.experiment_id,
                "system": self.system,
                "episode_id": episode_id,
                "seed": seed,
                "run_id": run_id,
            },
        )
        process_spec = prepared.process_spec
        values: dict[str, object] = {
            "experiment_id": experiment.experiment_id,
            "system": self.system,
            "benchmark": experiment.benchmark,
            "task": experiment.task,
            "episode_id": episode_id,
            "seed": seed,
            "model": experiment.llm.model,
            "model_provider": experiment.llm.provider,
            "reasoning_effort": experiment.llm.reasoning_effort or "",
            "run_id": run_id,
            "output_dir": run_directory.as_posix(),
        }
        argv = substitute_placeholders(process_spec.argv, values)
        environment_overrides = {
            name: substitute_text(value, values)
            for name, value in process_spec.environment_overrides.items()
        }
        run_spec = ProcessSpec(
            argv=argv,
            working_directory=process_spec.working_directory,
            environment_overrides=environment_overrides,
            timeout_seconds=process_spec.timeout_seconds,
        )
        outcome = self._process_manager.run(
            run_spec,
            stdout_path=writer.stdout_path,
            stderr_path=writer.stderr_path,
            environment=self._environment,
        )
        writer.trace.append(
            "process_completed",
            {
                "status": outcome.status,
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
            },
        )
        metrics = self._collect_validated_metrics(prepared, run_directory, outcome, writer)
        identity = self.resolve_episode_identity(prepared, run_directory, outcome)
        writer.trace.append("episode_identity", {"resolution": identity.get("status")})
        ended_at = utc_now_iso()
        manifest = RunManifest(
            experiment_id=experiment.experiment_id,
            system=self.system,
            benchmark=experiment.benchmark,
            task=experiment.task,
            episode_id=episode_id,
            seed=seed,
            run_id=run_id,
            config_digest=prepared.config_digest,
            started_at=started_at,
            ended_at=ended_at,
            command=argv,
            working_directory=(
                process_spec.working_directory.as_posix()
                if process_spec.working_directory
                else None
            ),
            process_status=outcome.status,
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            failure_reason=outcome.failure_reason(),
            llm_provider=experiment.llm.provider,
            requested_model=experiment.llm.model,
            reasoning_effort=experiment.llm.reasoning_effort,
            reasoning_options=dict(experiment.llm.reasoning_options or {}),
            roboguide_git_sha=current_git_commit(self._repository_root, self._environment),
            system_version=prepared.system_version,
            dataset_identity=experiment.dataset.identity(),
            dataset_digest=experiment.dataset.digest,
            environment_overrides=dict(environment_overrides),
            context=dict(experiment.context),
            environment_information=harness_environment_information(process_spec.conda_environment),
            episode_selection={
                "selector": episode_id,
                "seed": seed,
                "resolution": identity,
            },
        )
        writer.trace.append(
            "run_completed" if outcome.succeeded else "run_failed",
            {"failure_reason": manifest.failure_reason},
        )
        writer.write_manifest(manifest)
        writer.write_metrics(metrics.to_json())
        return RunResult(manifest=manifest, metrics=metrics, outcome=outcome)

    def _collect_validated_metrics(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
        writer: RunArtifactWriter,
    ) -> MetricsPayload:
        """Collect metrics and enforce the canonical contract before writing.

        The payload is validated (names, kinds, bounds) so no mis-mapped
        value can reach ``metrics.json``; a payload that would fail
        ``from_json`` on read is never written. A validation failure does not
        destroy evidence: the run keeps its manifest, logs, and trace, the
        offending values are dropped, the error is recorded in
        ``details.metrics_validation_error`` and a ``metrics_invalid`` trace
        event, and the raw evidence references survive.

        Args:
            prepared: The prepared-system handle.
            run_directory: The finished run's directory.
            outcome: The observed process outcome for this episode run.
            writer: The run's artifact writer for the trace event.

        Returns:
            The validated metrics payload, or the degraded empty-values
            payload with the validation error recorded.
        """
        collected: MetricsPayload | None = None
        try:
            collected = self.collect_result(prepared, run_directory, outcome)
            validated = backfill_wall_time(collected, outcome)
            validated.validate()
            return validated
        except MetricsError as error:
            writer.trace.append("metrics_invalid", {"error": str(error)})
            raw_evidence = collected.raw_evidence if collected is not None else ()
            return MetricsPayload(
                values={},
                details={"metrics_validation_error": str(error)},
                raw_evidence=raw_evidence,
            )

    def collect_result(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> MetricsPayload:
        """Convert a run's raw system output into canonical metrics.

        The configured ``metrics_source_path`` (relative to the run
        directory) is parsed as the system's raw metric report. All declared
        expected outputs that exist are recorded as raw evidence references;
        missing outputs are simply absent from the evidence list so a failed
        run keeps whatever evidence exists. Subclasses may override this
        method to parse system-specific log formats instead; the outcome is
        supplied so they can derive harness-measured facts.

        Args:
            prepared: The prepared-system handle carrying environment
                expectations.
            run_directory: The finished run's directory.
            outcome: The observed process outcome, for harness-measured
                facts (duration, status) that subclass parsers may fold in.

        Returns:
            The validated canonical metrics payload; empty when the system
            produced no parsable metrics file.

        Raises:
            MetricsError: If the raw metrics file exists but violates the
                canonical metric contract.
        """
        expected_outputs = prepared.environment_spec.expected_output_paths
        raw_evidence = tuple(
            RawEvidenceRef(path=relative_path, description=f"raw output from system {self.system}")
            for relative_path in expected_outputs
            if is_safe_run_relative_path(relative_path)
            and (run_directory / relative_path).is_file()
        )
        raw_evidence = raw_evidence + (
            RawEvidenceRef(
                path="stdout.log", description="persisted process stdout", media_type="text/plain"
            ),
            RawEvidenceRef(
                path="stderr.log", description="persisted process stderr", media_type="text/plain"
            ),
        )
        source = prepared.environment_spec.metrics_source_path
        if source is None or not is_safe_run_relative_path(source):
            return MetricsPayload(values={}, details={}, raw_evidence=raw_evidence)
        source_path = run_directory / source
        if not source_path.is_file():
            return MetricsPayload(values={}, details={}, raw_evidence=raw_evidence)
        try:
            document = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return MetricsPayload(values={}, details={}, raw_evidence=raw_evidence)
        if not isinstance(document, dict) or not all(isinstance(key, str) for key in document):
            return MetricsPayload(values={}, details={}, raw_evidence=raw_evidence)
        typed_document: JSONObject = document
        return MetricsPayload.from_json(
            {
                "schema": METRICS_SCHEMA,
                "values": typed_document.get("values", {}),
                "details": typed_document.get("details", {}),
                "raw_evidence": [evidence.to_json() for evidence in raw_evidence],
            }
        )

    def resolve_episode_identity(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> JSONObject:
        """Resolve which real benchmark episode this run executed.

        The manifest must distinguish the episode *selector* (what the
        experiment requested, e.g. a seed-pinned sample label) from the
        *resolved* benchmark episode identity. The base implementation is
        honestly unresolved: a generic process runner has no benchmark-side
        source, and fabricating an id is forbidden. System runners with
        official outputs override this.

        Args:
            prepared: The prepared-system handle carrying the resolved
                process specification.
            run_directory: The finished run's directory with persisted logs.
            outcome: The observed process outcome for this episode run.

        Returns:
            A JSON object describing the resolution: ``status`` is one of
            ``resolved``, ``multiple-episodes``, or ``unresolved``, plus
            ``resolved_episode_id`` / ``resolved_scene_id`` /
            ``dataset_index`` when known, ``evidence_source`` naming the
            official output used, and ``reason`` when unresolved.
        """
        return {
            "status": "unresolved",
            "resolved_episode_id": None,
            "resolved_scene_id": None,
            "dataset_index": None,
            "reason": (
                f"system runner {self.system!r} provides no benchmark episode identity source"
            ),
        }

    def cleanup(self, prepared: PreparedSystem) -> None:
        """Release everything the runner still owns after an experiment.

        Only children this runner's manager spawned are ever signaled; no
        process enumeration or name matching is performed, so cleanup cannot
        affect unrelated experiments or user processes.

        Args:
            prepared: The prepared-system handle being released.
        """
        self._process_manager.shutdown()


def run_experiment_spec(
    spec: ExperimentSpec,
    runner: ProcessSystemRunner,
    *,
    results_root: Path,
    episodes: tuple[str, ...] = (),
    seeds: tuple[int, ...] = (),
    local_config_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> list[RunResult]:
    """Run one experiment spec for one system across all selected episodes.

    This is the harness's top-level orchestration: it resolves the machine
    configuration, prepares the system once, runs every selected
    (episode, seed) pair into its own run directory, and always cleans the
    runner up. A failing episode does not abort the remaining episodes; each
    failure is fully evidenced in its run directory. The runner itself owns
    the harness environment and repository root used for provenance.

    Args:
        spec: The validated experiment specification.
        runner: The system runner under test.
        results_root: Root directory receiving run directories.
        episodes: Optional episode selection override from the CLI.
        seeds: Optional seed selection override from the CLI.
        local_config_path: Explicit local config path; ``None`` uses the
            default discovery rules.
        environment: Harness environment used for local-config discovery and
            ``${VAR}`` expansion; defaults to ``os.environ``.

    Returns:
        One :class:`RunResult` per executed (episode, seed) pair.

    Raises:
        ExperimentSpecError: If the system, an episode, or a seed is not
            declared by the spec.
        EvaluationConfigError: If machine-specific configuration is missing
            or invalid.
        SystemRunnerError: If preparation fails.
    """
    source_environment = environment if environment is not None else os.environ
    local_config = load_local_config(local_config_path, environment=source_environment)
    process_spec = resolve_process_spec(
        spec,
        runner.system,
        local_config.get(runner.system, EnvironmentOverrides()),
        environment=source_environment,
    )
    prepared = runner.prepare(spec, process_spec, spec.environment(runner.system))
    try:
        selected_episodes = spec.selected_episodes(episodes)
        selected_seeds = spec.selected_seeds(seeds)
        results: list[RunResult] = []
        for episode_id in selected_episodes:
            for seed in selected_seeds:
                results.append(
                    runner.run_episode(
                        spec,
                        prepared,
                        episode_id=episode_id,
                        seed=seed,
                        results_root=results_root,
                    )
                )
        return results
    finally:
        runner.cleanup(prepared)
