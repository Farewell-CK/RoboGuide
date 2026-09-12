"""Command-line boundary of the RoboGuide Eval Harness.

Three commands are provided:

- ``doctor`` — validate an experiment spec and the machine-specific
  environment (working directories, executables, Conda availability, Git
  provenance, result directory writability) without running any episode;
- ``run`` — execute one system under test across the selected episodes and
  seeds, producing one fully evidenced run directory per episode run;
- ``summarize`` — produce a structured summary over a results directory.

The CLI owns no evaluation logic; it only loads configuration, invokes the
runner lifecycle, and renders results. Machine-specific values always come
from local configuration or environment variables, never from committed
specs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from roboguide_eval.config import (
    EnvironmentOverrides,
    EvaluationConfigError,
    load_experiment_spec,
    load_local_config,
    resolve_process_spec,
)
from roboguide_eval.metrics import MetricsError
from roboguide_eval.models import ExperimentSpec, ExperimentSpecError, JSONObject
from roboguide_eval.process import (
    ProcessConfigError,
    ProcessManager,
    ProcessSpec,
    expand_environment_values,
    read_text_output,
    referenced_environment_names,
)
from roboguide_eval.results import current_git_commit, summarize_results
from roboguide_eval.runner import SystemRunnerError, run_experiment_spec
from roboguide_eval.systems import SYSTEM_RUNNERS, build_runner

DEFAULT_RESULTS_ROOT: Final = Path("evaluation/results")
_CONDA_VERSION_TIMEOUT_SECONDS: Final = 60.0
_CONDA_ENVIRONMENT_TIMEOUT_SECONDS: Final = 120.0


def _parser() -> argparse.ArgumentParser:
    """Build the CLI parser without touching configuration or the network.

    Returns:
        The configured argument parser with the doctor/run/summarize
        subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="roboguide-eval",
        description="RoboGuide Eval Harness: run and summarize embodied collaboration experiments.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="validate an experiment spec and local environment"
    )
    doctor.add_argument("--spec", type=Path, required=True, help="experiment spec YAML path")
    doctor.add_argument("--config", type=Path, help="local machine config YAML path")
    doctor.add_argument("--system", help="check only this system instead of all declared systems")
    doctor.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help="results directory to verify",
    )

    run = subparsers.add_parser("run", help="run one system under test on an experiment spec")
    run.add_argument("--spec", type=Path, required=True, help="experiment spec YAML path")
    run.add_argument(
        "--system", required=True, choices=sorted(SYSTEM_RUNNERS), help="system to run"
    )
    run.add_argument("--config", type=Path, help="local machine config YAML path")
    run.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help="root receiving run directories",
    )
    run.add_argument("--episode", action="append", default=[], help="episode override (repeatable)")
    run.add_argument(
        "--seed", action="append", type=int, default=[], help="seed override (repeatable)"
    )

    summarize = subparsers.add_parser("summarize", help="summarize a results directory")
    summarize.add_argument(
        "--results", type=Path, required=True, help="run or results root directory"
    )
    summarize.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    proxy = subparsers.add_parser(
        "proxy",
        help="run the local LLM accounting proxy (forwards to the real endpoint, records usage)",
    )
    proxy.add_argument("--upstream", required=True, help="real OpenAI-compatible endpoint base URL")
    proxy.add_argument(
        "--port", type=int, default=8901, help="local port to listen on (default 8901)"
    )
    proxy.add_argument(
        "--log",
        type=Path,
        required=True,
        help="NDJSON accounting log path (one record per LLM call)",
    )

    mission_front = subparsers.add_parser(
        "mission-front",
        help="run Mission Front-half eval cases through the real Mission Intelligence pipeline",
    )
    mission_front.add_argument(
        "--cases",
        type=Path,
        default=Path("evaluation/mission_front_cases/baseline.yaml"),
        help="case definition YAML (default: the baseline set)",
    )
    mission_front.add_argument(
        "--out",
        type=Path,
        default=Path("evaluation/results/mission-front"),
        help="output directory receiving suite evidence",
    )
    mission_front.add_argument(
        "--repository-root", type=Path, default=Path.cwd(), help="repository root"
    )
    mission_front.add_argument("--limit", type=int, default=0, help="run at most N cases (0 = all)")
    mission_front.add_argument(
        "--only", action="append", default=[], help="run only these case ids (repeatable)"
    )
    return parser


def _check_process_environment(
    spec: ExperimentSpec,
    system: str,
    config_path: Path | None,
    lines: list[str],
) -> bool:
    """Run all environment checks for one system and collect report lines.

    Args:
        spec: The loaded experiment spec.
        system: System under test being checked.
        config_path: Explicit local config path or ``None`` for default
            discovery.
        lines: Report lines to append ``PASS``/``FAIL`` entries to.

    Returns:
        ``True`` when every check for this system passed.
    """
    try:
        local_config = load_local_config(config_path)
        process_spec = resolve_process_spec(
            spec, system, local_config.get(system, EnvironmentOverrides())
        )
    except (EvaluationConfigError, ExperimentSpecError) as error:
        lines.append(f"FAIL system {system}: configuration unresolved: {error}")
        return False
    lines.append(f"PASS system {system}: configuration resolved")
    overall = True
    working_directory = process_spec.working_directory
    if working_directory is not None and working_directory.is_dir():
        lines.append(f"PASS system {system}: working directory exists: {working_directory}")
    else:
        lines.append(f"FAIL system {system}: working directory missing: {working_directory}")
        overall = False
    executable = process_spec.argv[0]
    if Path(executable).is_file() or shutil.which(executable) is not None:
        lines.append(f"PASS system {system}: executable found: {executable}")
    else:
        lines.append(f"FAIL system {system}: executable not found: {executable}")
        overall = False
    overall = _check_environment_references(system, process_spec, lines) and overall
    conda_environment = process_spec.conda_environment
    if conda_environment is not None:
        overall = _check_conda(system, process_spec, conda_environment, lines) and overall
    return overall


def _check_environment_references(
    system: str,
    process_spec: ProcessSpec,
    lines: list[str],
) -> bool:
    """Verify every configured ``${VAR}`` reference resolves in this shell.

    Credential references fail closed at spawn time; doctor surfaces the same
    problem before any episode runs so operators can export the missing
    variable up front.

    Args:
        system: System under test being checked.
        process_spec: The resolved process specification.
        lines: Report lines to append ``PASS``/``FAIL`` entries to.

    Returns:
        ``True`` when all references resolve in the current environment.
    """
    try:
        expand_environment_values(process_spec.environment_overrides, dict(os.environ))
    except ProcessConfigError as error:
        lines.append(f"FAIL system {system}: unresolved environment reference: {error}")
        return False
    references = referenced_environment_names(process_spec.environment_overrides)
    if references:
        lines.append(f"PASS system {system}: environment references resolve: {list(references)}")
    return True


def _check_conda(
    system: str,
    process_spec: ProcessSpec,
    conda_environment: str,
    lines: list[str],
) -> bool:
    """Verify the conda command starts and the target environment is usable.

    The environment probe launches ``python --version`` inside the configured
    environment; it verifies the environment only and never runs an episode.

    Args:
        system: System under test being checked.
        process_spec: The resolved process specification.
        conda_environment: Conda environment name from the resolved argv.
        lines: Report lines to append ``PASS``/``FAIL`` entries to.

    Returns:
        ``True`` when conda and the environment are both usable.
    """
    conda_command = process_spec.argv[0]
    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        with ProcessManager() as manager:
            version_outcome = manager.run(
                ProcessSpec(
                    argv=(conda_command, "--version"),
                    working_directory=None,
                    environment_overrides=process_spec.environment_overrides,
                    timeout_seconds=_CONDA_VERSION_TIMEOUT_SECONDS,
                ),
                stdout_path=temporary_path / "conda-version.out",
                stderr_path=temporary_path / "conda-version.err",
            )
            if not version_outcome.succeeded:
                lines.append(f"FAIL system {system}: conda command not usable: {conda_command}")
                return False
            lines.append(
                f"PASS system {system}: conda command usable "
                f"({read_text_output(version_outcome.stdout_path).strip() or conda_command})"
            )
            env_outcome = manager.run(
                ProcessSpec(
                    argv=(
                        conda_command,
                        "run",
                        "--no-capture-output",
                        "-n",
                        conda_environment,
                        "python",
                        "--version",
                    ),
                    working_directory=process_spec.working_directory,
                    environment_overrides=process_spec.environment_overrides,
                    timeout_seconds=_CONDA_ENVIRONMENT_TIMEOUT_SECONDS,
                ),
                stdout_path=temporary_path / "conda-env.out",
                stderr_path=temporary_path / "conda-env.err",
            )
            if not env_outcome.succeeded:
                lines.append(
                    f"FAIL system {system}: conda environment {conda_environment!r} not usable: "
                    f"{env_outcome.failure_reason()}"
                )
                return False
            lines.append(
                f"PASS system {system}: conda environment {conda_environment!r} starts "
                f"({read_text_output(env_outcome.stdout_path).strip()})"
            )
    return True


def _check_git_provenance(lines: list[str]) -> bool:
    """Verify the RoboGuide Git SHA used for run provenance is resolvable.

    Args:
        lines: Report lines to append ``PASS``/``FAIL`` entries to.

    Returns:
        ``True`` when a concrete SHA (or explicit override) is available.
    """
    commit = current_git_commit(Path.cwd(), dict(os.environ))
    if commit == "unknown":
        lines.append("FAIL RoboGuide git SHA unavailable for run provenance")
        return False
    lines.append(f"PASS RoboGuide git SHA: {commit[:12]}")
    return True


def _check_results_root(results_root: Path, lines: list[str]) -> bool:
    """Verify the results root directory is writable.

    Args:
        results_root: Directory that would receive run directories.
        lines: Report lines to append ``PASS``/``FAIL`` entries to.

    Returns:
        ``True`` when the directory can be created and written.
    """
    try:
        results_root.mkdir(parents=True, exist_ok=True)
        probe = results_root / ".doctor-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        lines.append(f"FAIL results root not writable: {results_root}: {error}")
        return False
    lines.append(f"PASS results root writable: {results_root}")
    return True


def _doctor(arguments: argparse.Namespace) -> int:
    """Execute the doctor subcommand.

    Args:
        arguments: Parsed CLI arguments.

    Returns:
        Process exit code: zero when every check passed, one otherwise.
    """
    lines: list[str] = []
    try:
        spec = load_experiment_spec(arguments.spec)
        lines.append(f"PASS experiment spec valid: {arguments.spec}")
    except ExperimentSpecError as error:
        lines.append(f"FAIL experiment spec invalid: {error}")
        _print_lines(lines)
        return 1
    try:
        if arguments.system is None:
            systems = spec.systems
        elif arguments.system in spec.systems:
            systems = (arguments.system,)
        else:
            raise ExperimentSpecError(f"system {arguments.system!r} is not declared by the spec")
    except ExperimentSpecError as error:
        lines.append(f"FAIL systems unresolved: {error}")
        _print_lines(lines)
        return 1
    overall = True
    for system in systems:
        overall = _check_process_environment(spec, system, arguments.config, lines) and overall
    overall = _check_git_provenance(lines) and overall
    overall = _check_results_root(arguments.results_root, lines) and overall
    _print_lines(lines)
    return 0 if overall else 1


def _print_lines(lines: Sequence[str]) -> None:
    """Print doctor report lines in order.

    Args:
        lines: The collected report lines.
    """
    for line in lines:
        print(line)


def _run(arguments: argparse.Namespace) -> int:
    """Execute the run subcommand.

    Args:
        arguments: Parsed CLI arguments.

    Returns:
        Process exit code: zero when every episode process succeeded, one
        otherwise. Failures still leave complete evidence directories.
    """
    try:
        spec = load_experiment_spec(arguments.spec)
        runner = build_runner(
            arguments.system, environment=dict(os.environ), repository_root=Path.cwd()
        )
        results = run_experiment_spec(
            spec,
            runner,
            results_root=arguments.results_root,
            episodes=tuple(arguments.episode),
            seeds=tuple(arguments.seed),
            local_config_path=arguments.config,
        )
    except (
        ExperimentSpecError,
        EvaluationConfigError,
        ProcessConfigError,
        MetricsError,
        SystemRunnerError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for result in results:
        manifest = result.manifest
        status = "ok" if result.succeeded else f"failed ({manifest.failure_reason})"
        print(f"{manifest.episode_id} seed={manifest.seed} -> {manifest.run_id}: {status}")
        print(
            f"  run directory: {arguments.results_root / manifest.experiment_id / manifest.run_id}"
        )
    failed = sum(1 for result in results if not result.succeeded)
    print(f"completed {len(results) - failed}/{len(results)} episode runs")
    return 0 if results and failed == 0 else 1


def _summarize(arguments: argparse.Namespace) -> int:
    """Execute the summarize subcommand.

    Args:
        arguments: Parsed CLI arguments.

    Returns:
        Process exit code: zero when at least one run was found, one when
        the results directory contains no runs.
    """
    report = summarize_results(arguments.results)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["runs"] else 1
    print(f"results root: {report['results_root']}")
    runs_value = report["runs"]
    run_entries: list[JSONObject] = [
        entry
        for entry in (runs_value if isinstance(runs_value, list) else [])
        if isinstance(entry, dict)
    ]
    for run_object in run_entries:
        metrics_value = run_object.get("metrics")
        values: object = metrics_value.get("values") if isinstance(metrics_value, dict) else None
        success = values.get("success") if isinstance(values, dict) else None
        print(
            f"- {run_object.get('experiment_id')}/{run_object.get('run_id')} "
            f"system={run_object.get('system')} episode={run_object.get('episode_id')} "
            f"seed={run_object.get('seed')} status={run_object.get('process_status')} "
            f"exit={run_object.get('exit_code')} success={success}"
        )
        if run_object.get("failure_reason"):
            print(f"  failure: {run_object.get('failure_reason')}")
    aggregate_value = report["aggregate"]
    aggregate = aggregate_value if isinstance(aggregate_value, dict) else {}
    print(
        f"total={aggregate.get('total')} completed={aggregate.get('completed')} "
        f"failed={aggregate.get('failed')} "
        f"success_rate={aggregate.get('success_rate')} "
        f"total_wall_time={aggregate.get('total_wall_time')}"
    )
    return 0 if run_entries else 1


def _proxy(arguments: argparse.Namespace) -> int:
    """Execute the proxy subcommand: serve until interrupted.

    Args:
        arguments: Parsed CLI arguments (upstream, port, log path).

    Returns:
        Process exit code: zero after a clean shutdown, one on startup
        failure (unusable port or log path).
    """
    from roboguide_eval.accounting import AccountingProxyConfig, AccountingProxyServer

    config = AccountingProxyConfig(
        upstream_base_url=arguments.upstream,
        log_path=arguments.log,
    )
    try:
        server = AccountingProxyServer(("127.0.0.1", arguments.port), config)
    except OSError as error:
        print(f"error: cannot bind accounting proxy: {error}", file=sys.stderr)
        return 1
    bound_host, bound_port = server.server_address[:2]
    print(
        f"accounting proxy listening on http://{str(bound_host)}:{bound_port}"
        f" -> {arguments.upstream}"
    )
    print(f"accounting log: {arguments.log}")
    print("point the system's OPENAI_BASE_URL at this address; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("accounting proxy stopped")
    finally:
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested subcommand and return a process-compatible status.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Zero on success, one on failure.
    """
    arguments = _parser().parse_args(argv)
    if arguments.command == "doctor":
        return _doctor(arguments)
    if arguments.command == "run":
        return _run(arguments)
    if arguments.command == "proxy":
        return _proxy(arguments)
    if arguments.command == "mission-front":
        return _mission_front(arguments)
    return _summarize(arguments)


def _mission_front(arguments: argparse.Namespace) -> int:
    """Execute the mission-front subcommand.

    Args:
        arguments: Parsed CLI arguments.

    Returns:
        Process exit code: zero when every case passed its invariants, one
        otherwise or when the suite could not start.
    """
    from roboguide_eval.mission_front import load_cases, run_suite

    try:
        cases = load_cases(arguments.cases)
        summary = run_suite(
            cases,
            repository_root=arguments.repository_root.resolve(),
            out_dir=arguments.out,
            only=tuple(arguments.only),
            limit=arguments.limit,
        )
    except Exception as error:  # noqa: BLE001 - CLI boundary reports cleanly
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"suite: {summary['suite_id']}  model: {summary['model']}")
    print(
        f"cases passed {summary['cases_passed']}/{summary['cases_executed']} "
        f"(llm calls: {summary['llm_calls']}, tokens: {summary['token_totals']})"
    )
    failed_invariants = summary["failed_invariants"]
    if isinstance(failed_invariants, dict):
        for name, count in failed_invariants.items():
            print(f"  failed invariant {name}: {count}")
    return 0 if summary["cases_passed"] == summary["cases_executed"] else 1
