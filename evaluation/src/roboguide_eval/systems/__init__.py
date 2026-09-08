"""Named system runners for the systems under test.

Each module in this package wraps one external system behind the shared
:class:`roboguide_eval.runner.ProcessSystemRunner` lifecycle. Runners only
launch and observe the external system across a process boundary; they never
import its packages, re-implement its internals, or bypass the RoboGuide
Controller path.
"""

from collections.abc import Mapping
from pathlib import Path

from roboguide_eval.process import ProcessManager
from roboguide_eval.runner import ProcessSystemRunner, SystemRunnerError
from roboguide_eval.systems.emos import EmosRunner
from roboguide_eval.systems.roboguide import RoboGuideRunner

SYSTEM_RUNNERS: Mapping[str, type[ProcessSystemRunner]] = {
    EmosRunner.system: EmosRunner,
    RoboGuideRunner.system: RoboGuideRunner,
}

__all__ = ["EmosRunner", "RoboGuideRunner", "SYSTEM_RUNNERS", "build_runner"]


def build_runner(
    system: str,
    *,
    environment: Mapping[str, str],
    repository_root: Path,
    process_manager: ProcessManager | None = None,
) -> ProcessSystemRunner:
    """Create the named system runner with its own process manager.

    Args:
        system: System under test name (``emos`` or ``roboguide``).
        environment: Harness environment used for ``${VAR}`` expansion and
            provenance overrides.
        repository_root: RoboGuide checkout used for Git provenance.
        process_manager: Optional prebuilt process manager (used by tests to
            tune termination behavior); a fresh manager is created when
            omitted.

    Returns:
        The runner instance for the requested system.

    Raises:
        SystemRunnerError: If the system name has no registered runner.
    """
    runner_type = SYSTEM_RUNNERS.get(system)
    if runner_type is None:
        raise SystemRunnerError(
            f"unknown system under test {system!r}; registered: {sorted(SYSTEM_RUNNERS)}"
        )
    manager = process_manager if process_manager is not None else ProcessManager()
    return runner_type(
        process_manager=manager, environment=environment, repository_root=repository_root
    )
