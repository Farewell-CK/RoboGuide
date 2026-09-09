"""EMOS system runner: launches the official EMOS entry point as configured.

This runner is deliberately thin. It starts the EMOS/Habitat-MAS command that
the operator configures (typically inside the dedicated EMOS Conda
environment, executed via ``conda run``), observes it across the process
boundary, and converts official EMOS outputs into the canonical schema. It
never imports ``habitat``/``emos`` packages, never re-implements EMOS logic,
and never decides what EMOS task success means.

Official EMOS outputs consumed (all benchmark-side, none invented):

- stdout banner ``Episode ID: <id>, Num Steps: <steps>`` printed by
  habitat's ``Env.log_episode_steps`` on episode reset — the resolved
  benchmark episode identity and the official step count;
- ``episode_log/<dataset>/<ablation>/`` step logs under the EMOS working
  directory — a fallback identity source compared against a snapshot taken
  during ``prepare``;
- ``chat_history_output/<episode_id>/token_usage.json`` under the EMOS
  working directory — per-agent actual LLM token totals written by EMOS
  itself (chat-history saving is on by default); the file is copied into the
  run directory as raw evidence;
- evaluator summary lines ``Average episode <key>: <value>`` at the end of
  stdout — ``pddl_success`` and any other aggregate the task reports.

Metrics without a reliable official source for a run are left absent and
recorded with a reason under ``details.unavailable_metrics`` — never
zero-filled, never guessed.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Final

from roboguide_eval.metrics import MetricsPayload, RawEvidenceRef
from roboguide_eval.models import EnvironmentSpec, ExperimentSpec, JSONObject, JSONValue
from roboguide_eval.process import ProcessOutcome, ProcessSpec, read_whole_text_output
from roboguide_eval.runner import PreparedSystem, ProcessSystemRunner

AVERAGE_EPISODE_LINE: Final = re.compile(
    r"Average episode (?P<key>[A-Za-z0-9_.\-]+): (?P<value>[01]?\.\d+|\d+\.\d+)\s*$"
)
EPISODE_STEP_LINE: Final = re.compile(r"Episode ID: (?P<episode_id>\d+), Num Steps: (?P<steps>\d+)")
EPISODE_SAMPLE_OVERRIDE: Final = re.compile(r"iterator_options\.num_episode_sample=(\d+)\Z")
PDDL_SUCCESS_METRIC: Final = "pddl_success"
TOKEN_EVIDENCE_DIRECTORY: Final = "raw-evidence"


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


def parse_episode_step_lines(stdout_text: str) -> dict[str, int]:
    """Extract official ``Episode ID / Num Steps`` banners from stdout.

    Args:
        stdout_text: The full persisted stdout of one EMOS evaluation
            process. Habitat prints this banner when an environment resets,
            so it carries the episode that just finished.

    Returns:
        A mapping of episode id to official step count, in log order; later
        duplicates of one id overwrite earlier ones.
    """
    parsed: dict[str, int] = {}
    for line in stdout_text.splitlines():
        match = EPISODE_STEP_LINE.search(line)
        if match is not None:
            parsed[match.group("episode_id")] = int(match.group("steps"))
    return parsed


def read_episode_log_steps(working_directory: Path | None) -> dict[str, str]:
    """Merge every EMOS ``episode_log`` step record under the working directory.

    Args:
        working_directory: The EMOS checkout the process ran in, or ``None``.

    Returns:
        A mapping of ``"episode_id: <id>"`` record key to its
        ``"num_steps: <n>"`` value across all step-log files; empty when the
        directory does not exist or no file parses.
    """
    if working_directory is None:
        return {}
    merged: dict[str, str] = {}
    for log_path in sorted(working_directory.glob("episode_log/**/*_steps_log.json")):
        try:
            document = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            merged.update({str(key): str(value) for key, value in document.items()})
    return merged


def read_token_usage(working_directory: Path | None, episode_id: str) -> dict[str, int] | None:
    """Read EMOS's official per-agent token totals for one episode.

    Args:
        working_directory: The EMOS checkout the process ran in, or ``None``.
        episode_id: The resolved benchmark episode id.

    Returns:
        A mapping of agent name to actual total tokens, or ``None`` when the
        official ``token_usage.json`` for the episode does not exist or does
        not parse.
    """
    if working_directory is None:
        return None
    token_path = working_directory / "chat_history_output" / episode_id / "token_usage.json"
    try:
        document = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    totals: dict[str, int] = {}
    for agent, value in document.items():
        if isinstance(value, int) and not isinstance(value, bool):
            totals[str(agent)] = value
    return totals or None


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

    def __init__(self, **kwargs: object) -> None:
        """Create the runner with an empty episode-log baseline snapshot.

        Args:
            **kwargs: Forwarded to the base runner constructor
                (``process_manager``, ``environment``, ``repository_root``).
        """
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._episode_log_baseline: dict[str, str] = {}
        self._resolved_cache: tuple[Path, dict[str, int]] | None = None

    def prepare(
        self,
        experiment: ExperimentSpec,
        process_spec: ProcessSpec,
        environment_spec: EnvironmentSpec,
    ) -> PreparedSystem:
        """Snapshot episode-log state, then run the base preparation.

        The baseline snapshot lets identity resolution attribute newly added
        step-log records to this run when the stdout banner is absent; the
        baseline is refreshed after every run so a later run's fallback diff
        never claims an earlier run's episodes.

        Args:
            experiment: The experiment being prepared.
            process_spec: The resolved process specification.
            environment_spec: Portable environment expectations.

        Returns:
            The prepared-system handle from the base implementation.
        """
        self._episode_log_baseline = read_episode_log_steps(process_spec.working_directory)
        self._resolved_cache = None
        return super().prepare(experiment, process_spec, environment_spec)

    def _resolved_episodes(self, run_directory: Path, prepared: PreparedSystem) -> dict[str, int]:
        """Determine the benchmark episodes this run actually executed.

        Prefers the official stdout banners; falls back to newly added
        records in EMOS's persistent episode-log step files (diffed against
        the snapshot refreshed after the previous run). The result is cached
        per run directory so :meth:`collect_result` and
        :meth:`resolve_episode_identity` agree without re-reading files.
        the snapshot taken during :meth:`prepare`).

        Args:
            run_directory: The finished run's directory with stdout.log.
            prepared: The prepared-system handle carrying the working
                directory.

        Returns:
            A mapping of resolved episode id to official step count; step
            counts are 0 for fallback-sourced ids.
        """
        cached = self._resolved_cache
        if cached is not None and cached[0] == run_directory:
            return dict(cached[1])
        current = read_episode_log_steps(prepared.process_spec.working_directory)
        banners = parse_episode_step_lines(read_whole_text_output(run_directory / "stdout.log"))
        if banners:
            episodes = banners
        else:
            episodes = {}
            for key, value in current.items():
                if key in self._episode_log_baseline:
                    continue
                digits = value.replace("num_steps:", "").strip()
                if value.startswith("num_steps:") and digits.isdigit():
                    episodes[key.replace("episode_id:", "").strip()] = int(digits)
        # Refresh the baseline to the post-run state so the next run's
        # fallback diff attributes only its own episodes, and cache the
        # per-run result for the second consumer.
        self._episode_log_baseline = current
        self._resolved_cache = (run_directory, dict(episodes))
        return episodes

    def resolve_episode_identity(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> JSONObject:
        """Resolve the real Habitat episode identity from official outputs.

        Args:
            prepared: The prepared-system handle carrying the resolved
                process specification.
            run_directory: The finished run's directory with persisted logs.
            outcome: The observed process outcome for this episode run.

        Returns:
            The resolution object recorded in the manifest's
            ``episode_selection.resolution``: a single id resolves, multiple
            ids are reported as a batch, and none yields an honest
            ``unresolved`` status with the reason.
        """
        episodes = self._resolved_episodes(run_directory, prepared)
        if len(episodes) == 1:
            episode_id, steps = next(iter(episodes.items()))
            return {
                "status": "resolved",
                "resolved_episode_id": episode_id,
                "resolved_scene_id": None,
                "dataset_index": None,
                "official_num_steps": steps or None,
                "evidence_source": (
                    "stdout: Episode Step Info banner (habitat Env.log_episode_steps)"
                ),
                "scene_note": (
                    "scene id is not printed by official stdout; derivable from the pinned "
                    "dataset in a later slice"
                ),
            }
        if len(episodes) > 1:
            episode_ids: list[JSONValue] = [str(episode_id) for episode_id in sorted(episodes)]
            return {
                "status": "multiple-episodes",
                "resolved_episode_ids": episode_ids,
                "resolved_scene_id": None,
                "dataset_index": None,
                "evidence_source": "stdout: Episode Step Info banners",
            }
        return {
            "status": "unresolved",
            "resolved_episode_id": None,
            "resolved_scene_id": None,
            "dataset_index": None,
            "reason": (
                "no official Episode Step Info banner in stdout and no new episode_log step "
                "records; habitat emits the banner on environment reset"
            ),
        }

    def collect_result(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> MetricsPayload:
        """Convert official EMOS outputs into canonical metrics.

        Sources: evaluator ``Average episode`` lines (success and any other
        aggregates), the official episode banner (simulation steps), and
        EMOS's own ``token_usage.json`` (token_usage, copied into the run
        directory as raw evidence). Metrics without a reliable official
        source stay absent and are explained in
        ``details.unavailable_metrics``.

        Args:
            prepared: The prepared-system handle carrying environment
                expectations.
            run_directory: The finished run's directory.
            outcome: The observed process outcome for this episode run.

        Returns:
            The canonical metrics payload for the run.
        """
        payload = super().collect_result(prepared, run_directory, outcome)
        values = dict(payload.values)
        details = dict(payload.details)
        evidence = list(payload.raw_evidence)
        unavailable: dict[str, str] = {
            "coordination_latency": (
                "measurement boundary frozen in evaluation/README.md; collection gated on "
                "explicit protocol agreement before formal E1 runs"
            )
        }
        episodes = self._resolved_episodes(run_directory, prepared)
        averages = parse_average_episode_lines(read_whole_text_output(run_directory / "stdout.log"))

        pddl_success = averages.pop(PDDL_SUCCESS_METRIC, None)
        if pddl_success is not None and "success" not in values and len(episodes) == 1:
            values["success"] = pddl_success >= 0.5
        elif pddl_success is None and "success" not in values:
            unavailable["success"] = "evaluator reported no pddl_success average in stdout"
        details["emos_pddl_success_average"] = pddl_success
        resolved_ids: list[JSONValue] = [str(episode_id) for episode_id in sorted(episodes)]
        details["emos_episode_ids"] = resolved_ids
        details["emos_episode_batch_size"] = sampled_episode_count(prepared.process_spec.argv)

        if len(episodes) == 1 and next(iter(episodes.values())) > 0:
            values["simulation_steps"] = next(iter(episodes.values()))
        elif "simulation_steps" not in values:
            unavailable["simulation_steps"] = (
                "no official Episode Step Info banner carried a step count for this run"
            )

        if len(episodes) == 1:
            episode_id = next(iter(episodes))
            token_totals = read_token_usage(prepared.process_spec.working_directory, episode_id)
            if token_totals is not None:
                values["token_usage"] = sum(token_totals.values())
                details["emos_token_usage_by_agent"] = dict(token_totals)
                working_directory = prepared.process_spec.working_directory or Path(".")
                source_file = (
                    working_directory / "chat_history_output" / episode_id / "token_usage.json"
                )
                if source_file.is_file():
                    evidence_directory = run_directory / TOKEN_EVIDENCE_DIRECTORY
                    evidence_directory.mkdir(parents=True, exist_ok=True)
                    copied = evidence_directory / f"token_usage-{episode_id}.json"
                    shutil.copyfile(source_file, copied)
                    evidence.append(
                        RawEvidenceRef(
                            path=f"{TOKEN_EVIDENCE_DIRECTORY}/{copied.name}",
                            description=(
                                f"EMOS official per-agent token totals for episode {episode_id}"
                            ),
                            media_type="application/json",
                        )
                    )
            else:
                unavailable["token_usage"] = (
                    f"EMOS wrote no chat_history_output/{episode_id}/token_usage.json; "
                    "chat-history saving may be disabled"
                )

        # Only an explicit rate-named aggregate maps to the canonical rate.
        # A bare ``subgoal_success`` aggregate has unverified semantics (it
        # may be a completed-count on some tasks), so it stays verbatim in
        # details instead of being forced into a [0, 1] field.
        subgoal_rate = averages.pop("subgoal_success_rate", None)
        if subgoal_rate is not None and "subgoal_success_rate" not in values:
            values["subgoal_success_rate"] = subgoal_rate
        else:
            unavailable.setdefault(
                "subgoal_success_rate",
                "EMOS evaluator reported no subgoal_success_rate average; raw subgoal "
                "aggregates stay in details (mobility defines no stage-goal measurement; "
                "see evaluation/README.md)",
            )

        if averages:
            details["emos_average_metrics"] = dict(averages)
        if unavailable:
            details["unavailable_metrics"] = dict(unavailable)
        return MetricsPayload(values=values, details=details, raw_evidence=tuple(evidence))
