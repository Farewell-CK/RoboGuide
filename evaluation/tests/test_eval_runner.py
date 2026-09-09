"""Tests for the SystemRunner lifecycle and experiment orchestration."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import helpers
import pytest
from roboguide_eval.config import load_experiment_spec, spec_digest
from roboguide_eval.metrics import MetricsPayload
from roboguide_eval.models import EnvironmentSpec, ExperimentSpec
from roboguide_eval.process import ProcessManager, ProcessOutcome, ProcessSpec
from roboguide_eval.runner import (
    CommandBuildError,
    PreparedSystem,
    ProcessSystemRunner,
    RunResult,
    SystemRunnerError,
    backfill_wall_time,
    run_experiment_spec,
    substitute_placeholders,
    substitute_text,
)
from roboguide_eval.systems import SYSTEM_RUNNERS, build_runner
from roboguide_eval.systems.emos import parse_average_episode_lines

SpecFactory = Callable[..., Path]
LocalConfigFactory = Callable[..., Path]


def require_object(value: object) -> dict[str, object]:
    """Narrow one JSON value to a plain object or fail the test.

    Args:
        value: The decoded JSON value to narrow.

    Returns:
        The value typed as a string-keyed mapping.

    Raises:
        AssertionError: If the value is not an object.
    """
    assert isinstance(value, dict)
    return value


def require_str(value: object) -> str:
    """Narrow one JSON value to a string or fail the test.

    Args:
        value: The decoded JSON value to narrow.

    Returns:
        The value typed as a string.

    Raises:
        AssertionError: If the value is not a string.
    """
    assert isinstance(value, str)
    return value


@pytest.fixture
def fixture_spec(tmp_path: Path, make_spec: SpecFactory) -> ExperimentSpec:
    """Load the standard fixture experiment spec.

    Args:
        tmp_path: Per-test temporary directory.
        make_spec: Spec factory fixture.

    Returns:
        The loaded :class:`ExperimentSpec`.
    """
    return load_experiment_spec(make_spec())


def test_runner_registry_covers_declared_systems() -> None:
    """The named runners cover exactly the E1 systems under test."""
    assert sorted(SYSTEM_RUNNERS) == ["emos", "roboguide"]
    for name, runner_type in SYSTEM_RUNNERS.items():
        assert runner_type.system == name


def test_build_runner_rejects_unknown_system() -> None:
    """Unknown systems fail closed before any process work."""
    with pytest.raises(SystemRunnerError, match="unknown system under test"):
        build_runner("habitat-direct", environment={}, repository_root=Path.cwd())


def test_substitute_placeholders_expands_known_names_and_rejects_unknown() -> None:
    """Placeholder substitution covers documented keys and fails closed."""
    values: dict[str, object] = {
        "episode_id": "ep-1",
        "seed": 7,
        "model": "gpt-5.6-luna",
        "output_dir": "/runs/run-1",
    }
    argv = substitute_placeholders(
        ("--episode", "{episode_id}", "--seed", "{seed}", "{output_dir}"), values
    )
    assert argv == ("--episode", "ep-1", "--seed", "7", "/runs/run-1")
    # Non-placeholder brace content (for example JSON inside script
    # arguments) passes through untouched.
    assert substitute_placeholders(('{"values": 1}', "{broken"), values) == (
        '{"values": 1}',
        "{broken",
    )
    with pytest.raises(CommandBuildError, match="unknown command placeholder"):
        substitute_placeholders(("--flag", "{undeclared}"), values)


def run_emos_once(
    tmp_path: Path,
    spec: ExperimentSpec,
    local_config_path: Path,
    environment: dict[str, str],
    episode_id: str,
    seed: int,
) -> RunResult:
    """Run exactly one (episode, seed) pair through the emos runner.

    Args:
        tmp_path: Per-test temporary directory.
        spec: The loaded experiment spec.
        local_config_path: Written local config for the emos system.
        environment: Deterministic harness environment.
        episode_id: The episode to run.
        seed: The seed to run.

    Returns:
        The single produced run result.
    """
    runner = build_runner("emos", environment=environment, repository_root=tmp_path)
    results = run_experiment_spec(
        spec,
        runner,
        results_root=tmp_path / "results",
        local_config_path=local_config_path,
        episodes=(episode_id,),
        seeds=(seed,),
        environment=environment,
    )
    assert len(results) == 1
    return results[0]


def test_full_lifecycle_produces_complete_run_evidence(
    tmp_path: Path,
    fixture_spec: ExperimentSpec,
    fixture_local_config: Path,
    fixed_environment: dict[str, str],
) -> None:
    """A successful fixture episode leaves the complete evidence layout."""
    result = run_emos_once(
        tmp_path, fixture_spec, fixture_local_config, fixed_environment, "ep-000", 7
    )
    assert result.succeeded
    manifest = result.manifest
    assert manifest.experiment_id == "fixture-experiment"
    assert manifest.system == "emos"
    assert manifest.episode_id == "ep-000"
    assert manifest.seed == 7
    assert manifest.exit_code == 0
    assert manifest.failure_reason is None
    assert manifest.roboguide_git_sha == "fixture0000000000000000000000000000000"
    assert manifest.system_version == "v-fixture-1.2.3"
    assert manifest.config_digest == spec_digest(fixture_spec)
    assert manifest.dataset_identity == "fixture-dataset@r1"
    assert manifest.requested_model == "gpt-5.6-luna"
    assert manifest.command[0] == sys.executable
    run_directory = tmp_path / "results" / "fixture-experiment" / manifest.run_id
    assert {item.name for item in run_directory.iterdir()} == {
        "manifest.json",
        "metrics.json",
        "trace.jsonl",
        "stdout.log",
        "stderr.log",
        "raw-metrics.json",
        "episode.log",
    }
    stored_manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    assert stored_manifest["run_id"] == manifest.run_id
    metrics_document = json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics_document["values"] == {"success": True, "wall_time": 1.5}
    assert metrics_document["details"]["note"] == "fixture"
    evidence_paths = {entry["path"] for entry in metrics_document["raw_evidence"]}
    assert evidence_paths == {"raw-metrics.json", "episode.log", "stdout.log", "stderr.log"}
    assert "fixture-stdout" in (run_directory / "stdout.log").read_text(encoding="utf-8")
    assert "fixture-stderr" in (run_directory / "stderr.log").read_text(encoding="utf-8")
    trace_events = [
        json.loads(line)["event"]
        for line in (run_directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert trace_events == ["run_started", "process_completed", "episode_identity", "run_completed"]


def test_failing_episode_still_leaves_manifest_and_logs(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """A nonzero child still produces a full evidence directory."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_FAILING_SCRIPT, "{output_dir}"],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert not result.succeeded
    assert result.manifest.failure_reason == "process exited with code 2"
    assert result.manifest.exit_code == 2
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    assert (run_directory / "manifest.json").is_file()
    assert (run_directory / "stdout.log").is_file()
    assert (run_directory / "stderr.log").is_file()
    trace_events = [
        json.loads(line)["event"]
        for line in (run_directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert trace_events == ["run_started", "process_completed", "episode_identity", "run_failed"]
    # Raw metrics the child produced before failing are still converted and
    # stored beside the failure evidence.
    stored_metrics = json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))
    assert stored_metrics["values"] == {"success": False, "wall_time": 0.25}


def test_timed_out_episode_is_recorded_as_timeout(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """A child exceeding the timeout is terminated and marked timed out."""
    spec = load_experiment_spec(make_spec(helpers.BASE_TIMEOUT_SPEC_YAML))
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_SLEEPING_SCRIPT, "60"],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-slow", 1)
    assert result.outcome.status == "timeout"
    assert result.manifest.timed_out is True
    assert "timed out" in (result.manifest.failure_reason or "")


def test_episode_grid_runs_every_combination_with_unique_directories(
    tmp_path: Path,
    fixture_spec: ExperimentSpec,
    fixture_local_config: Path,
    fixed_environment: dict[str, str],
) -> None:
    """Two episodes times two seeds produce four distinct run directories."""
    runner = build_runner("emos", environment=fixed_environment, repository_root=tmp_path)
    results = run_experiment_spec(
        fixture_spec,
        runner,
        results_root=tmp_path / "results",
        local_config_path=fixture_local_config,
        environment=fixed_environment,
    )
    assert len(results) == 4
    assert len({result.manifest.run_id for result in results}) == 4
    assert {(result.manifest.episode_id, result.manifest.seed) for result in results} == {
        ("ep-000", 7),
        ("ep-000", 11),
        ("ep-001", 7),
        ("ep-001", 11),
    }


def test_missing_metrics_source_yields_empty_payload_with_evidence(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """A system that writes no metrics file still records stdout evidence."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", "print('ran without metrics')"],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    # No system metrics were reported; the harness still measured wall time.
    assert set(result.metrics.values) == {"wall_time"}
    assert result.metrics.details["wall_time_source"] == "harness_process_duration"
    evidence_paths = {entry.path for entry in result.metrics.raw_evidence}
    assert "stdout.log" in evidence_paths


def test_runner_lifecycle_hooks_run_in_documented_order(
    tmp_path: Path,
    fixture_spec: ExperimentSpec,
    fixture_local_config: Path,
    fixed_environment: dict[str, str],
) -> None:
    """prepare, run_episode, and cleanup are invoked in lifecycle order."""

    class RecordingRunner(ProcessSystemRunner):
        """Runner recording lifecycle hook invocations for ordering checks."""

        system = "emos"

        def __init__(
            self,
            *,
            process_manager: ProcessManager,
            environment: dict[str, str],
            repository_root: Path,
        ) -> None:
            """Create the recording runner with an empty call log.

            Args:
                process_manager: Manager used for every child process.
                environment: Harness environment used for provenance.
                repository_root: RoboGuide checkout used for provenance.
            """
            super().__init__(
                process_manager=process_manager,
                environment=environment,
                repository_root=repository_root,
            )
            self.calls: list[str] = []

        def prepare(
            self,
            experiment: ExperimentSpec,
            process_spec: ProcessSpec,
            environment_spec: EnvironmentSpec,
        ) -> PreparedSystem:
            """Record prepare, then delegate to the base implementation.

            Args:
                experiment: The experiment being prepared.
                process_spec: The resolved process specification.
                environment_spec: Portable environment expectations.

            Returns:
                The prepared-system handle.
            """
            self.calls.append("prepare")
            return super().prepare(experiment, process_spec, environment_spec)

        def run_episode(
            self,
            experiment: ExperimentSpec,
            prepared: PreparedSystem,
            *,
            episode_id: str,
            seed: int,
            results_root: Path,
        ) -> RunResult:
            """Record run_episode, then delegate to the base implementation.

            Args:
                experiment: The experiment being executed.
                prepared: The prepared-system handle.
                episode_id: Declared episode to run.
                seed: Declared seed to run.
                results_root: Root directory receiving run directories.

            Returns:
                The run result for this episode.
            """
            self.calls.append("run_episode")
            return super().run_episode(
                experiment,
                prepared,
                episode_id=episode_id,
                seed=seed,
                results_root=results_root,
            )

        def cleanup(self, prepared: PreparedSystem) -> None:
            """Record cleanup, then delegate to the base implementation.

            Args:
                prepared: The prepared-system handle.
            """
            self.calls.append("cleanup")
            super().cleanup(prepared)

    runner = RecordingRunner(
        process_manager=ProcessManager(),
        environment=fixed_environment,
        repository_root=tmp_path,
    )
    run_experiment_spec(
        fixture_spec,
        runner,
        results_root=tmp_path / "results",
        local_config_path=fixture_local_config,
        episodes=("ep-000",),
        seeds=(7,),
        environment=fixed_environment,
    )
    assert runner.calls == ["prepare", "run_episode", "cleanup"]


def test_environment_overrides_reach_child_but_stay_placeholder_in_manifest(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """${VAR} credentials reach the child while manifests keep placeholders."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_ENV_SCRIPT],
            environment_variables={"FIXTURE_TOKEN": "${PROBE_TOKEN}"},
        )
    )
    child_environment = {**fixed_environment, "PROBE_TOKEN": "live-secret"}
    result = run_emos_once(tmp_path, spec, config_path, child_environment, "ep-000", 7)
    assert result.succeeded
    assert result.manifest.environment_overrides == {"FIXTURE_TOKEN": "${PROBE_TOKEN}"}
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    assert (run_directory / "stdout.log").read_text(encoding="utf-8").strip() == "live-secret"
    assert "live-secret" not in json.dumps(result.manifest.to_json())


def test_substitute_text_shields_credential_references() -> None:
    """{name} placeholders never fire inside ${NAME} credential references."""
    values: dict[str, object] = {"model": "gpt-5.6-luna"}
    assert substitute_text("${OPENAI_API_KEY}", values) == "${OPENAI_API_KEY}"
    assert substitute_text("{model} via ${OPENAI_API_KEY}", values) == (
        "gpt-5.6-luna via ${OPENAI_API_KEY}"
    )
    with pytest.raises(CommandBuildError, match="unknown command placeholder"):
        substitute_text("{undeclared}", values)


def test_substitute_placeholders_rejects_dollar_shaped_argv_typos() -> None:
    """Shell-style ${seed} in argv fails closed instead of passing through.

    argv entries are never environment-expanded, so the typo cannot be
    shielded like a credential reference in an environment value.
    """
    values: dict[str, object] = {"seed": 7}
    with pytest.raises(CommandBuildError, match=r"do not support \$\{seed\}"):
        substitute_placeholders(("--seed", "${seed}"), values)
    assert substitute_placeholders(("--seed", "{seed}"), values) == ("--seed", "7")


def test_environment_placeholders_reach_child_and_manifest(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """{model} in an environment value follows the spec into the child env."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_ENV_SCRIPT],
            environment_variables={
                "FIXTURE_TOKEN": "{model} ${PROBE_TOKEN}",
            },
        )
    )
    child_environment = {**fixed_environment, "PROBE_TOKEN": "live-secret"}
    result = run_emos_once(tmp_path, spec, config_path, child_environment, "ep-000", 7)
    assert result.succeeded
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    stdout = (run_directory / "stdout.log").read_text(encoding="utf-8").strip()
    assert stdout == "gpt-5.6-luna live-secret"
    # The manifest records the substituted placeholder but never the secret.
    assert result.manifest.environment_overrides == {"FIXTURE_TOKEN": "gpt-5.6-luna ${PROBE_TOKEN}"}
    assert "live-secret" not in json.dumps(result.manifest.to_json())


def test_parse_average_episode_lines_extracts_emos_evaluator_metrics() -> None:
    """The parser handles logger prefixes and the evaluator's value format."""
    stdout = (
        "INFO habitat.mas - discussion started\n"
        "INFO evaluator - Average episode pddl_success: 0.0000\n"
        "Average episode composite_success: 0.5000\n"
        "Average episode reward: 12.2500\n"
        "unrelated line with no metrics\n"
    )
    assert parse_average_episode_lines(stdout) == {
        "pddl_success": 0.0,
        "composite_success": 0.5,
        "reward": 12.25,
    }
    assert parse_average_episode_lines("no metrics here") == {}


def test_emos_runner_converts_evaluator_logs_to_canonical_metrics(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """EmosRunner maps official outputs to canonical metrics per-episode."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=[
                "-u",
                "-c",
                helpers.FIXTURE_EMOS_LOG_SCRIPT,
                "habitat.environment.iterator_options.num_episode_sample=1",
            ],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    values = result.metrics.values
    assert values["success"] is True
    assert values["simulation_steps"] == 120  # from the official Episode Step Info banner
    assert "wall_time" in values  # backfilled from the harness-measured duration
    details = result.metrics.details
    assert details["emos_pddl_success_average"] == 1.0
    assert details["emos_average_metrics"] == {"composite_success": 0.5, "reward": 12.25}
    assert details["emos_episode_ids"] == ["5"]
    assert details["emos_episode_batch_size"] == 1
    assert details["wall_time_source"] == "harness_process_duration"
    unavailable = require_object(details["unavailable_metrics"])
    assert require_str(unavailable["subgoal_success_rate"]).startswith(
        "EMOS evaluator reported no subgoal"
    )
    assert require_str(unavailable["token_usage"]).startswith("EMOS wrote no chat_history_output")
    assert "coordination_latency" in unavailable
    selection = require_object(result.manifest.episode_selection)
    assert selection["selector"] == "ep-000"
    assert selection["seed"] == 7
    resolution = require_object(selection["resolution"])
    assert resolution["status"] == "resolved"
    assert resolution["resolved_episode_id"] == "5"
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    stored = json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))
    assert stored["values"]["success"] is True


def test_emos_runner_collects_official_token_usage(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """token_usage comes from EMOS's own per-agent totals, copied as evidence."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_EMOS_EPISODE_SCRIPT],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    values = result.metrics.values
    assert values["token_usage"] == 2000
    assert values["simulation_steps"] == 512
    details = result.metrics.details
    assert details["emos_token_usage_by_agent"] == {"agent_0": 1200, "agent_1": 800}
    selection = require_object(result.manifest.episode_selection)
    resolution = require_object(selection["resolution"])
    assert resolution["resolved_episode_id"] == "42"
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    evidence = run_directory / "raw-evidence" / "token_usage-42.json"
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8")) == {"agent_0": 1200, "agent_1": 800}
    evidence_paths = {ref.path for ref in result.metrics.raw_evidence}
    assert "raw-evidence/token_usage-42.json" in evidence_paths


def test_emos_runner_marks_identity_unresolved_without_official_output(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """No official banner and no new episode_log records: honest unresolved."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", "print('ran with no official outputs')"],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    resolution = require_object(require_object(result.manifest.episode_selection)["resolution"])
    assert resolution["status"] == "unresolved"
    assert resolution["resolved_episode_id"] is None
    assert "reason" in resolution
    unavailable = require_object(result.metrics.details["unavailable_metrics"])
    assert require_str(unavailable["success"]) == (
        "evaluator reported no pddl_success average in stdout"
    )
    assert "simulation_steps" in unavailable


def test_emos_runner_parses_metrics_after_large_log_prefix(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """Metric parsing reads the whole stdout, not a truncated head.

    Regression guard: real EMOS runs log many kilobytes of discussion before
    the evaluator prints its summary lines at the very end.
    """
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=[
                "-u",
                "-c",
                helpers.FIXTURE_EMOS_LONG_LOG_SCRIPT,
                "habitat.environment.iterator_options.num_episode_sample=1",
            ],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    assert (run_directory / "stdout.log").stat().st_size > 4096
    assert result.metrics.values["success"] is True
    assert result.metrics.values["simulation_steps"] == 480
    assert result.metrics.details["emos_pddl_success_average"] == 1.0


def test_emos_runner_batch_average_is_not_booleanized(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """Multi-episode batch runs keep success unset and preserve the average."""
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=[
                "-u",
                "-c",
                helpers.FIXTURE_EMOS_BATCH_SCRIPT,
                "habitat.environment.iterator_options.num_episode_sample=3",
            ],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    assert "success" not in result.metrics.values
    assert "simulation_steps" not in result.metrics.values
    assert result.metrics.details["emos_pddl_success_average"] == 0.6
    assert result.metrics.details["emos_episode_ids"] == ["3", "4"]
    resolution = require_object(require_object(result.manifest.episode_selection)["resolution"])
    assert resolution["status"] == "multiple-episodes"
    assert resolution["resolved_episode_ids"] == ["3", "4"]


def test_emos_runner_episode_log_fallback_attributes_per_run(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """The fallback diff attributes each run only its own new episode.

    Regression guard: with a single prepare-time baseline, the second run of
    a seed grid would wrongly claim the first run's episode as its own.
    """
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_EMOS_APPEND_LOG_SCRIPT],
        )
    )
    runner = build_runner("emos", environment=fixed_environment, repository_root=tmp_path)
    results = run_experiment_spec(
        spec,
        runner,
        results_root=tmp_path / "results",
        local_config_path=config_path,
        episodes=("ep-000",),
        seeds=(7, 11),
        environment=fixed_environment,
    )
    assert len(results) == 2
    for index, result in enumerate(results):
        resolution = require_object(require_object(result.manifest.episode_selection)["resolution"])
        assert resolution["status"] == "resolved"
        assert resolution["resolved_episode_id"] == str(11 + index)
        assert result.metrics.values["simulation_steps"] == 77


def test_invalid_metrics_degrade_with_full_evidence(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """A contract-violating payload is dropped, never written unreadable.

    The process outcome, manifest, logs, and trace all survive; the offense
    is recorded in metrics details and a trace event.
    """
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_INVALID_METRICS_SCRIPT, "{output_dir}"],
        )
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    assert result.manifest.process_status == "completed"
    assert result.manifest.failure_reason is None
    assert result.metrics.values == {}
    error_text = require_str(require_object(result.metrics.details)["metrics_validation_error"])
    assert "must stay within" in error_text
    run_directory = tmp_path / "results" / "fixture-experiment" / result.manifest.run_id
    assert (run_directory / "manifest.json").is_file()
    assert (run_directory / "metrics.json").is_file()
    stored = json.loads((run_directory / "metrics.json").read_text(encoding="utf-8"))
    assert stored["values"] == {}
    trace_events = [
        json.loads(line)["event"]
        for line in (run_directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "metrics_invalid" in trace_events


def test_subgoal_count_aggregate_not_mapped_as_rate(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
    fixed_environment: dict[str, str],
) -> None:
    """A bare subgoal_success aggregate keeps unverified semantics in details."""
    spec = load_experiment_spec(make_spec())
    script = helpers.FIXTURE_EMOS_LOG_SCRIPT.replace(
        "Average episode composite_success: 0.5000",
        "Average episode subgoal_success: 2.5000",
    )
    config_path = make_local_config(
        helpers.local_config_yaml(fixture_workdir, arguments=["-u", "-c", script])
    )
    result = run_emos_once(tmp_path, spec, config_path, fixed_environment, "ep-000", 7)
    assert result.succeeded
    values = result.metrics.values
    assert "subgoal_success_rate" not in values
    averages = require_object(result.metrics.details["emos_average_metrics"])
    assert averages["subgoal_success"] == 2.5
    unavailable = require_object(result.metrics.details["unavailable_metrics"])
    assert require_str(unavailable["subgoal_success_rate"]).startswith(
        "EMOS evaluator reported no subgoal_success_rate"
    )


def test_wall_time_backfill_respects_system_reported_values() -> None:
    """A system-reported wall_time is never overwritten by the harness."""
    outcome = ProcessOutcome(
        argv=("python",),
        working_directory=None,
        status="completed",
        exit_code=0,
        timed_out=False,
        duration_seconds=99.0,
        stdout_path=Path("stdout.log"),
        stderr_path=Path("stderr.log"),
    )
    reported = MetricsPayload(values={"wall_time": 1.5}, details={}, raw_evidence=())
    assert backfill_wall_time(reported, outcome) is reported
    missing = MetricsPayload(values={}, details={}, raw_evidence=())
    backfilled = backfill_wall_time(missing, outcome)
    assert backfilled.values["wall_time"] == 99.0
    assert backfilled.details["wall_time_source"] == "harness_process_duration"
