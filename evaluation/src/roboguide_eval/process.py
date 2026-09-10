"""Subprocess lifecycle management for external systems under test.

The Eval Harness runs external systems (EMOS, RoboGuide, future simulation
backends) strictly across a process boundary: it never imports their Python
packages. This module owns that boundary for the harness. It provides argv
list execution (no shell string concatenation), explicit environment
overrides with ``${VAR}`` credential interpolation, per-run stdout/stderr
persistence, timeouts with graceful SIGTERM escalation to SIGKILL, and a
typed :class:`ProcessOutcome` so that even a failed spawn still produces
inspectable evidence.

Safety rules:

- The manager only ever signals process objects it spawned itself. Cleanup
  never enumerates or matches unrelated processes by name, so it cannot kill
  other experiments or user processes.
- On POSIX children start in their own session; timeouts signal exactly that
  child process group, so wrappers such as ``conda run`` do not leave
  orphaned grandchildren behind.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

_ENV_REFERENCE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TERMINATE_GRACE_SECONDS: Final = 5.0

type ProcessStatus = Literal["completed", "timeout", "start_failed"]


class ProcessConfigError(ValueError):
    """Report an unusable process specification, such as a missing secret."""


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Describe one external process launch without any shell involvement.

    ``argv`` is the final child argv list; when a Conda environment is
    configured, config resolution prepends the ``conda run`` prefix here and
    records the environment name in ``conda_environment`` explicitly, so no
    consumer has to guess it back out of the argv. ``environment_overrides``
    values may contain ``${NAME}`` references that are resolved from the
    harness environment at spawn time and never persisted in resolved form.
    """

    argv: tuple[str, ...]
    working_directory: Path | None
    environment_overrides: Mapping[str, str]
    timeout_seconds: float
    conda_environment: str | None = None
    readiness_command: tuple[str, ...] | None = None
    version_probe_command: tuple[str, ...] | None = None
    # Pinned benchmark dataset file used for read-only episode identity
    # resolution; machine-specific, so it arrives only via local config.
    dataset_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """Record the observable result of one managed process execution.

    Every field exists so that a run directory keeps full evidence even when
    the external system fails: persisted log paths, exit code, timeout flag,
    and a human-readable failure reason.
    """

    argv: tuple[str, ...]
    working_directory: Path | None
    status: ProcessStatus
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Report whether the process ran to completion with exit code zero.

        Returns:
            ``True`` only when the status is ``completed`` and the exit code
            is exactly zero.
        """
        return self.status == "completed" and self.exit_code == 0

    def failure_reason(self) -> str | None:
        """Return a stable human-readable reason when the process failed.

        Returns:
            ``None`` for successful runs; otherwise a string naming the
            timeout, start failure, or nonzero exit code.
        """
        if self.status == "timeout":
            return f"process timed out after {self.duration_seconds:.1f}s and was terminated"
        if self.status == "start_failed":
            return f"process failed to start: {self.error}"
        if self.exit_code != 0:
            return f"process exited with code {self.exit_code}"
        return None


def conda_run_prefix(conda_command: str, conda_environment: str) -> tuple[str, ...]:
    """Build the ``conda run`` argv prefix used to enter a Conda environment.

    ``--no-capture-output`` keeps the child's stdout/stderr streaming into the
    run logs instead of being buffered by conda. No interactive
    ``conda activate`` is ever required.

    Args:
        conda_command: Executable used to invoke conda (path or ``PATH`` name).
        conda_environment: Name of the target Conda environment.

    Returns:
        The argv prefix to place before the child executable.
    """
    return (conda_command, "run", "--no-capture-output", "-n", conda_environment)


def referenced_environment_names(values: Mapping[str, str]) -> tuple[str, ...]:
    """Return the sorted unique ``${VAR}`` names referenced by configured values.

    Args:
        values: Configured override values, possibly containing references.

    Returns:
        A sorted tuple of referenced variable names; empty when none are
        referenced.
    """
    names: set[str] = set()
    for value in values.values():
        names.update(_ENV_REFERENCE.findall(value))
    return tuple(sorted(names))


def expand_environment_values(
    values: Mapping[str, str], environment: Mapping[str, str]
) -> dict[str, str]:
    """Resolve ``${NAME}`` references in configured environment variables.

    Resolution is single-pass: a referenced variable's own value is never
    rescanned, so a secret that itself contains ``${OTHER}`` text cannot be
    cross-substituted.

    Args:
        values: Configured override values, possibly containing references.
        environment: The harness environment used for resolution.

    Returns:
        Fully resolved override values safe to hand to ``subprocess``.

    Raises:
        ProcessConfigError: If a referenced variable is absent from the
            harness environment; credentials fail closed instead of being
            silently passed as empty strings.
    """
    resolved: dict[str, str] = {}
    for key, value in values.items():
        for name in _ENV_REFERENCE.findall(value):
            if name not in environment:
                raise ProcessConfigError(f"environment variable {key!r} references missing ${name}")

        def lookup(match: re.Match[str], _environment: Mapping[str, str] = environment) -> str:
            """Return the resolved value for one matched reference.

            Args:
                match: The ``${NAME}`` regex match to resolve.
                _environment: Environment captured from the enclosing loop
                    iteration; bound as a default to avoid late binding.

            Returns:
                The environment value the reference resolves to.
            """
            return _environment[match.group(1)]

        resolved[key] = _ENV_REFERENCE.sub(lookup, value)
    return resolved


def _signal_process_group(process_id: int, signal_number: int) -> None:
    """Signal one spawned child's whole process group, POSIX only.

    Args:
        process_id: The child's process id, which is also its group leader id
            because children are started with ``start_new_session``.
        signal_number: The signal to deliver to the group.
    """
    if os.name != "posix":
        return
    try:
        os.killpg(process_id, signal_number)
    except ProcessLookupError:
        return


class ProcessManager:
    """Spawn, observe, and terminate harness-owned external processes.

    The manager is intentionally stateless across runs: :meth:`run` always
    waits for its child (or terminates it), so it never leaks background
    work. :meth:`shutdown` exists as a defensive cleanup that can only ever
    act on children this manager itself created and had not yet reaped.
    """

    def __init__(self, *, terminate_grace_seconds: float = _TERMINATE_GRACE_SECONDS) -> None:
        """Create a manager.

        Args:
            terminate_grace_seconds: How long a timed-out child is given to
                exit after SIGTERM before SIGKILL is sent.
        """
        self._terminate_grace_seconds = terminate_grace_seconds
        self._children: set[subprocess.Popen[bytes]] = set()

    def __enter__(self) -> ProcessManager:
        """Return the manager for ``with`` statement use.

        Returns:
            The manager itself.
        """
        return self

    def __exit__(self, *exception_info: object) -> None:
        """Shut down any remaining harness-owned children on scope exit.

        Args:
            exception_info: Unused exception propagation information.
        """
        self.shutdown()

    def run(
        self,
        spec: ProcessSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessOutcome:
        """Execute one process specification to completion or timeout.

        stdout/stderr are redirected into the given files so output is
        durably persisted even when the child is killed. Spawn failures are
        returned as a ``start_failed`` outcome rather than raised, so callers
        always obtain evidence.

        Args:
            spec: The resolved process specification (final argv, cwd, env).
            stdout_path: File the child's stdout is written to (truncated).
            stderr_path: File the child's stderr is written to (truncated).
            environment: Harness environment used for ``${VAR}`` expansion;
                defaults to ``os.environ``.
            timeout_seconds: Overrides ``spec.timeout_seconds`` when given.

        Returns:
            The observed :class:`ProcessOutcome`; never raises for child
            failures.
        """
        source_environment = os.environ if environment is None else environment
        overrides = expand_environment_values(spec.environment_overrides, source_environment)
        child_environment = dict(source_environment) | overrides
        timeout = spec.timeout_seconds if timeout_seconds is None else timeout_seconds
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            try:
                process = subprocess.Popen(
                    list(spec.argv),
                    cwd=str(spec.working_directory) if spec.working_directory else None,
                    env=child_environment,
                    stdout=out,
                    stderr=err,
                    start_new_session=os.name == "posix",
                )
            except OSError as error:
                duration = time.monotonic() - started
                return ProcessOutcome(
                    argv=spec.argv,
                    working_directory=spec.working_directory,
                    status="start_failed",
                    exit_code=None,
                    timed_out=False,
                    duration_seconds=duration,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    error=str(error),
                )
            self._children.add(process)
            timed_out = False
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _signal_process_group(process.pid, signal.SIGTERM)
                try:
                    return_code = process.wait(timeout=self._terminate_grace_seconds)
                except subprocess.TimeoutExpired:
                    _signal_process_group(process.pid, signal.SIGKILL)
                    return_code = process.wait()
            finally:
                self._children.discard(process)
        duration = time.monotonic() - started
        status: ProcessStatus = "timeout" if timed_out else "completed"
        return ProcessOutcome(
            argv=spec.argv,
            working_directory=spec.working_directory,
            status=status,
            exit_code=return_code,
            timed_out=timed_out,
            duration_seconds=duration,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def shutdown(self) -> None:
        """Terminate any still-running harness-owned children.

        Only children this manager spawned and has not yet reaped are acted
        on; unrelated processes are never inspected or signaled. Under normal
        operation :meth:`run` already reaped every child, making this a no-op.
        """
        for process in list(self._children):
            _signal_process_group(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + self._terminate_grace_seconds
        for process in list(self._children):
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(remaining, 0.0))
            except subprocess.TimeoutExpired:
                _signal_process_group(process.pid, signal.SIGKILL)
                process.wait()
        self._children.clear()


def read_text_output(path: Path, *, max_bytes: int = 4096) -> str:
    """Read the head of a persisted log file as text for probes and messages.

    Args:
        path: The log file to read.
        max_bytes: Maximum number of bytes returned from the head of file.

    Returns:
        Decoded text (undecodable bytes replaced) from the head of the file,
        or an empty string when the file does not exist.
    """
    try:
        return path.read_bytes()[:max_bytes].decode(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_whole_text_output(path: Path) -> str:
    """Read one persisted log file fully as text, without truncation.

    Unlike :func:`read_text_output` this reads the entire file, so summary
    lines emitted at the very end of a long run survive. Intended for
    metric parsing over run logs; probes and failure snippets should keep
    using the bounded variant.

    Args:
        path: The log file to read.

    Returns:
        Decoded text (undecodable bytes replaced), or an empty string when
        the file does not exist.
    """
    try:
        return path.read_bytes().decode(encoding="utf-8", errors="replace")
    except OSError:
        return ""
