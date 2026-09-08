"""EMOS system runner: launches the official EMOS entry point as configured.

This runner is deliberately thin. It starts the EMOS/Habitat-MAS command that
the operator configures (typically inside the dedicated EMOS Conda
environment, executed via ``conda run``), observes it across the process
boundary, and converts its raw output into the canonical schema. It never
imports ``habitat``/``emos`` packages, never re-implements EMOS logic, and
never decides what EMOS task success means.

The EMOS-specific part implemented here is metric conversion only: the
habitat-baselines evaluator logs aggregate episode metrics as
``Average episode <key>: <value>`` lines at the end of stdout. ``pddl_success``
maps to the canonical boolean ``success`` only when the configured command
pins a single episode per run (``iterator_options.num_episode_sample=1``);
multi-episode batch runs leave ``success`` unset because a boolean cannot
represent an average, and every logged key — including the raw
``pddl_success`` average — is preserved verbatim under ``details``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, cast

from roboguide_eval.metrics import MetricsPayload
from roboguide_eval.models import JSONValue
from roboguide_eval.process import ProcessOutcome, read_whole_text_output
from roboguide_eval.runner import PreparedSystem, ProcessSystemRunner

AVERAGE_EPISODE_LINE: Final = re.compile(
    r"Average episode (?P<key>[A-Za-z0-9_.\-]+): (?P<value>[01]?\.\d+|\d+\.\d+)\s*$"
)
PDDL_SUCCESS_METRIC: Final = "pddl_success"
EPISODE_SAMPLE_OVERRIDE: Final = re.compile(r"iterator_options\.num_episode_sample=(\d+)\Z")


def parse_average_episode_lines(stdout_text: str) -> dict[str, float]:
    """Extract ``Average episode <key>: <value>`` pairs from evaluator stdout.

    Args:
        stdout_text: The full persisted stdout of one EMOS evaluation
            process. It must not be truncated: the evaluator emits these
            lines only after the last episode finishes.

    Returns:
        A mapping of metric key to its reported average value, in log order;
        later duplicates of one key overwrite earlier ones.
    """
    parsed: dict[str, float] = {}
    for line in stdout_text.splitlines():
        match = AVERAGE_EPISODE_LINE.search(line)
        if match is not None:
            parsed[match.group("key")] = float(match.group("value"))
    return parsed


def sampled_episode_count(argv: tuple[str, ...]) -> int | None:
    """Return the per-run episode count from EMOS iterator override args.

    Args:
        argv: The resolved EMOS command argv, carrying hydra overrides such
            as ``habitat.environment.iterator_options.num_episode_sample=1``.

    Returns:
        The configured sample count, or ``None`` when the command does not
        pin episode sampling (the run then covers the whole filtered set).
    """
    for argument in argv:
        match = EPISODE_SAMPLE_OVERRIDE.search(argument)
        if match is not None:
            return int(match.group(1))
    return None


class EmosRunner(ProcessSystemRunner):
    """Run the official EMOS system as an external, configured process."""

    system = "emos"

    def collect_result(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> MetricsPayload:
        """Convert EMOS evaluator log lines into canonical metrics.

        Starts from the generic JSON-file conversion, then folds in the
        ``Average episode`` lines parsed from the run's full persisted
        stdout. ``pddl_success`` becomes the canonical boolean ``success``
        only for single-episode runs (sample count pinned to one); batch
        runs keep ``success`` unset and preserve the average in ``details``.
        All other logged EMOS metrics are kept verbatim in ``details``
        because the harness does not interpret them.

        Args:
            prepared: The prepared-system handle carrying environment
                expectations.
            run_directory: The finished run's directory.
            outcome: The observed process outcome for this episode run.

        Returns:
            The canonical metrics payload for the run.
        """
        payload = super().collect_result(prepared, run_directory, outcome)
        averages = parse_average_episode_lines(read_whole_text_output(run_directory / "stdout.log"))
        if not averages:
            return payload
        values = dict(payload.values)
        details = dict(payload.details)
        episode_count = sampled_episode_count(prepared.process_spec.argv)
        details["emos_episode_batch_size"] = episode_count
        pddl_success = averages.pop(PDDL_SUCCESS_METRIC, None)
        if pddl_success is not None and "success" not in values and episode_count == 1:
            values["success"] = pddl_success >= 0.5
        details["emos_pddl_success_average"] = pddl_success
        details["emos_average_metrics"] = cast(JSONValue, averages)
        return MetricsPayload(
            values=values,
            details=details,
            raw_evidence=payload.raw_evidence,
        )
