"""Tests for the ProcessManager lifecycle: success, failure, timeout, cleanup."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from helpers import FIXTURE_ENV_SCRIPT, FIXTURE_SLEEPING_SCRIPT, interpreter
from roboguide_eval.process import (
    ProcessConfigError,
    ProcessManager,
    ProcessOutcome,
    ProcessSpec,
    conda_run_prefix,
    expand_environment_values,
)


def spec_for(script: str, *arguments: str, timeout: float = 30.0) -> ProcessSpec:
    """Build a ProcessSpec running one inline Python script.

    Args:
        script: Python source executed by the child interpreter.
        arguments: Additional argv entries after ``-c``.
        timeout: Process timeout in seconds.

    Returns:
        The process specification.
    """
    return ProcessSpec(
        argv=(interpreter(), "-u", "-c", script, *arguments),
        working_directory=None,
        environment_overrides={},
        timeout_seconds=timeout,
    )


def run_to_tmp(
    tmp_path: Path,
    spec: ProcessSpec,
    *,
    timeout: float | None = None,
) -> tuple[ProcessOutcome, Path, Path]:
    """Run a spec with log files inside the test directory.

    Args:
        tmp_path: Per-test temporary directory.
        spec: The process specification.
        timeout: Optional timeout override.

    Returns:
        The outcome plus the stdout and stderr paths.
    """
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    with ProcessManager() as manager:
        outcome = manager.run(
            spec,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=timeout,
        )
    return outcome, stdout_path, stderr_path


def test_successful_process_records_completed_outcome(tmp_path: Path) -> None:
    """A zero-exit child completes, persists logs, and reports duration."""
    outcome, stdout_path, _ = run_to_tmp(tmp_path, spec_for("print('hello')"))
    assert outcome.succeeded
    assert outcome.exit_code == 0
    assert outcome.status == "completed"
    assert not outcome.timed_out
    assert outcome.failure_reason() is None
    assert outcome.duration_seconds >= 0.0
    assert stdout_path.read_text(encoding="utf-8").strip() == "hello"


def test_nonzero_exit_still_persists_logs_and_reason(tmp_path: Path) -> None:
    """A failing child yields evidence: exit code, reason, and both logs."""
    script = "import sys\nprint('out-line')\nprint('err-line', file=sys.stderr)\nsys.exit(2)\n"
    outcome, stdout_path, stderr_path = run_to_tmp(tmp_path, spec_for(script))
    assert not outcome.succeeded
    assert outcome.exit_code == 2
    assert outcome.failure_reason() == "process exited with code 2"
    assert "out-line" in stdout_path.read_text(encoding="utf-8")
    assert "err-line" in stderr_path.read_text(encoding="utf-8")


def test_timeout_terminates_child_but_keeps_evidence(tmp_path: Path) -> None:
    """Timed-out children are terminated (then killed) and keep all evidence."""
    outcome, stdout_path, stderr_path = run_to_tmp(
        tmp_path,
        spec_for(FIXTURE_SLEEPING_SCRIPT, "60", timeout=0.5),
    )
    assert outcome.status == "timeout"
    assert outcome.timed_out
    assert not outcome.succeeded
    assert "timed out" in (outcome.failure_reason() or "")
    assert stdout_path.exists() and stderr_path.exists()


def test_timeout_does_not_touch_unrelated_processes(tmp_path: Path) -> None:
    """Cleanup only signals its own children; unrelated processes survive."""
    unrelated = subprocess.Popen(
        [interpreter(), "-u", "-c", FIXTURE_SLEEPING_SCRIPT, "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        outcome, _, _ = run_to_tmp(
            tmp_path,
            spec_for(FIXTURE_SLEEPING_SCRIPT, "60", timeout=0.5),
        )
        assert outcome.timed_out
        time.sleep(0.2)
        assert unrelated.poll() is None, "unrelated process must survive harness cleanup"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)


def test_start_failure_returns_outcome_instead_of_raising(tmp_path: Path) -> None:
    """A missing executable yields a start_failed outcome with full files."""
    spec = ProcessSpec(
        argv=("definitely-not-a-real-executable-xyz", "--version"),
        working_directory=None,
        environment_overrides={},
        timeout_seconds=5.0,
    )
    outcome, stdout_path, stderr_path = run_to_tmp(tmp_path, spec)
    assert outcome.status == "start_failed"
    assert outcome.exit_code is None
    assert outcome.failure_reason() is not None
    assert stdout_path.exists() and stderr_path.exists()


def test_environment_variable_expansion_and_fail_closed(tmp_path: Path) -> None:
    """${VAR} references resolve at spawn time and fail closed when absent."""
    spec = ProcessSpec(
        argv=(interpreter(), "-u", "-c", FIXTURE_ENV_SCRIPT),
        working_directory=None,
        environment_overrides={"FIXTURE_TOKEN": "${PROBE_TOKEN}"},
        timeout_seconds=30.0,
    )
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    with ProcessManager() as manager:
        outcome = manager.run(
            spec,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            environment={"PROBE_TOKEN": "secret-value"},
        )
    assert outcome.succeeded
    assert stdout_path.read_text(encoding="utf-8").strip() == "secret-value"
    with pytest.raises(ProcessConfigError, match=r"missing \$PROBE_TOKEN"):
        expand_environment_values({"FIXTURE_TOKEN": "${PROBE_TOKEN}"}, {})


def test_conda_prefix_shape_and_explicit_spec_field() -> None:
    """Conda argv is a fixed prefix and the environment name lives on the spec.

    Regression guard: a non-Conda command whose arguments merely contain
    ``-n <name>`` must not be mistaken for a Conda launch, because the
    environment name is carried by ``ProcessSpec.conda_environment`` instead
    of being parsed back out of the argv.
    """
    prefix = conda_run_prefix("conda", "emos-env")
    assert prefix == ("conda", "run", "--no-capture-output", "-n", "emos-env")
    plain = ProcessSpec(
        argv=(interpreter(), "-u", "-c", "print('ok')", "-n", "looks-like-conda"),
        working_directory=None,
        environment_overrides={},
        timeout_seconds=30.0,
    )
    assert plain.conda_environment is None
    assert (
        ProcessSpec(
            argv=prefix + (interpreter(), "-u"),
            working_directory=None,
            environment_overrides={},
            timeout_seconds=30.0,
            conda_environment="emos-env",
        ).conda_environment
        == "emos-env"
    )


def test_environment_expansion_is_single_pass() -> None:
    """Resolved values are never rescanned for further placeholders.

    If ``${A}`` resolves to text that itself looks like ``${B}``, that text
    must survive verbatim instead of being cross-substituted with ``B``'s
    secret.
    """
    resolved = expand_environment_values(
        {"COMPOSITE": "${A} ${B}", "B": "real-secret"},
        {"A": "${B}", "B": "real-secret"},
    )
    assert resolved["COMPOSITE"] == "${B} real-secret"
    assert resolved["B"] == "real-secret"


def test_outcome_records_exact_argv_and_working_directory(tmp_path: Path) -> None:
    """Outcomes carry the exact argv and working directory used."""
    spec = ProcessSpec(
        argv=(interpreter(), "-u", "-c", "print('cwd-ok')"),
        working_directory=tmp_path,
        environment_overrides={},
        timeout_seconds=30.0,
    )
    outcome, _, _ = run_to_tmp(tmp_path, spec)
    assert outcome.argv == spec.argv
    assert outcome.working_directory == tmp_path


def test_child_environment_inherits_parent_and_overrides(tmp_path: Path) -> None:
    """The child environment is the parent environment plus explicit overrides."""
    script = (
        "import os\nprint(os.environ.get('PATH') is not None, os.environ.get('HARNESS_MARKER'))\n"
    )
    spec = ProcessSpec(
        argv=(interpreter(), "-u", "-c", script),
        working_directory=None,
        environment_overrides={"HARNESS_MARKER": "present"},
        timeout_seconds=30.0,
    )
    stdout_path = tmp_path / "stdout.log"
    with ProcessManager() as manager:
        outcome = manager.run(
            spec,
            stdout_path=stdout_path,
            stderr_path=tmp_path / "stderr.log",
            environment=dict(os.environ),
        )
    assert outcome.succeeded
    assert stdout_path.read_text(encoding="utf-8").strip() == "True present"


def test_shutdown_after_completed_run_is_safe(tmp_path: Path) -> None:
    """Explicit shutdown after normal completion neither hangs nor kills."""
    manager = ProcessManager()
    outcome = manager.run(
        spec_for("print('done')"),
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    manager.shutdown()
    manager.shutdown()  # shutdown is idempotent
    assert outcome.succeeded
