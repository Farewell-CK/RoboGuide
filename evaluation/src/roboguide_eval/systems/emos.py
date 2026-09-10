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
  during ``prepare`` and refreshed after every run;
- ``chat_history_output/<date>/<config>/<ablation>/<episode_id>/
  token_usage.json`` — EMOS's real per-agent token totals (the nested layout
  comes from ``MultiLLMPolicy.act``; chat-history saving is on by default).
  The file is located by a prepare-time snapshot diff, so a token record
  left by an older run of the same episode is never mistaken for this run's
  usage; the fresh file is copied into the run directory as raw evidence
  with its source path recorded;
- the pinned dataset file (optional local-config ``dataset_path``) resolves
  the episode's scene id and dataset index read-only, digest-verified
  against the ExperimentSpec;
- evaluator summary lines ``Average episode <key>: <value>`` emitted through
  Python ``logging`` at the end of the process — they land on **stderr**
  (with logger timestamp prefixes), so both persisted streams are parsed
  and merged. ``pddl_success`` maps to ``success``; the official
  ``pddl_stage_goals.<stage>_success`` aggregates average into
  ``subgoal_success_rate`` with the raw stage values kept in details.

Metrics without a reliable official source for a run are left absent and
recorded with a reason under ``details.unavailable_metrics`` — never
zero-filled, never guessed. Token cost breakdown (input/output/cached/
reasoning) is a harness accounting responsibility served by a dedicated
proxy in a later slice; the canonical fields are reserved in the metric
registry.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Final, TypedDict, cast

from roboguide_eval.dataset_identity import resolve_episode_in_dataset
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
TOKEN_HISTORY_DIRECTORY: Final = "chat_history_output"
EPISODE_IDENTITY_SOURCES: Final = [
    "stdout: Episode Step Info banner (habitat Env.log_episode_steps)",
    "pinned dataset (digest-verified)",
]


def parse_average_episode_lines(log_text: str) -> dict[str, float]:
    """Extract ``Average episode <key>: <value>`` pairs from evaluator logs.

    Args:
        log_text: Persisted process log text. The evaluator emits these
            lines only after the last episode finishes, through Python
            ``logging`` — logger timestamp prefixes on the same line are
            tolerated.

    Returns:
        A mapping of metric key to its reported average value, in log order;
        later duplicates of one key overwrite earlier ones.
    """
    parsed: dict[str, float] = {}
    for line in log_text.splitlines():
        match = AVERAGE_EPISODE_LINE.search(line)
        if match is not None:
            parsed[match.group("key")] = float(match.group("value"))
    return parsed


def parse_evaluator_averages(run_directory: Path) -> dict[str, float]:
    """Collect evaluator averages from both persisted process logs.

    The habitat-baselines evaluator logs its ``Average episode ...`` summary
    through Python ``logging``, which reaches stderr, while some runs also
    mirror plain prints on stdout; both streams are parsed and merged.

    Args:
        run_directory: The finished run's directory holding stdout.log and
            stderr.log.

    Returns:
        The merged mapping of metric key to average value.
    """
    averages = parse_average_episode_lines(read_whole_text_output(run_directory / "stdout.log"))
    averages.update(
        parse_average_episode_lines(read_whole_text_output(run_directory / "stderr.log"))
    )
    return averages


def stage_goal_success_average(averages: dict[str, float]) -> float | None:
    """Average the official per-stage success aggregates into one rate.

    The ``PddlStageGoals`` measurement (registered via the
    ``composite_stage_goals`` config node) reports one
    ``pddl_stage_goals.<stage>_success`` average per defined stage goal;
    their mean is the episode's subgoal success rate in [0, 1].

    Args:
        averages: The evaluator averages keyed by metric name.

    Returns:
        The mean over all ``pddl_stage_goals.*_success`` aggregates, or
        ``None`` when the task reports none.
    """
    stage_values = [
        value
        for key, value in averages.items()
        if key.startswith("pddl_stage_goals.") and key.endswith("_success")
    ]
    if not stage_values:
        return None
    return sum(stage_values) / len(stage_values)


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


def snapshot_token_files(working_directory: Path | None) -> dict[Path, tuple[int, int]]:
    """Snapshot every EMOS token record under the chat-history directory.

    Args:
        working_directory: The EMOS checkout, or ``None``.

    Returns:
        A mapping of token file path to its ``(mtime_ns, size)`` stamp; empty
        when the directory does not exist.
    """
    if working_directory is None:
        return {}
    history_root = working_directory / TOKEN_HISTORY_DIRECTORY
    if not history_root.is_dir():
        return {}
    snapshot: dict[Path, tuple[int, int]] = {}
    for pattern in ("token_usage.json", "token_usage_details.jsonl"):
        for token_path in history_root.rglob(pattern):
            try:
                stat = token_path.stat()
            except OSError:
                continue
            snapshot[token_path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def locate_fresh_token_file(
    working_directory: Path | None,
    episode_id: str,
    file_name: str,
    baseline: dict[Path, tuple[int, int]],
) -> tuple[Path | None, str | None]:
    """Locate this run's token record for one episode, refusing stale files.

    EMOS nests token records as
    ``chat_history_output/<date>/<config>/<ablation>/<episode_id>/<file_name>``
    (per-call totals, plus the per-call usage details written by the local
    accounting instrumentation), so the same episode id can carry records
    from older runs. Only files that are new or modified since the
    prepare-time snapshot count as this run's evidence; ambiguity stays
    unresolved.

    Args:
        working_directory: The EMOS checkout the process ran in, or ``None``.
        episode_id: The resolved benchmark episode id.
        file_name: The evidence file name to locate (``token_usage.json`` or
            ``token_usage_details.jsonl``).
        baseline: The prepare-time token snapshot.

    Returns:
        A ``(path, reason)`` pair: exactly one of the two is set. The path
        is this run's fresh evidence file; the reason explains why none
        could be trusted.
    """
    if working_directory is None:
        return None, "no working directory to locate token evidence in"
    history_root = working_directory / TOKEN_HISTORY_DIRECTORY
    if not history_root.is_dir():
        return None, f"EMOS wrote no {TOKEN_HISTORY_DIRECTORY}/ tree for this run"
    candidates = [path for path in history_root.rglob(file_name) if path.parent.name == episode_id]
    if not candidates:
        return None, f"EMOS wrote no {file_name} for episode {episode_id}"
    fresh: list[Path] = []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if baseline.get(path) != (stat.st_mtime_ns, stat.st_size):
            fresh.append(path)
    if not fresh:
        return None, (
            f"only {file_name} records from earlier runs exist for episode "
            f"{episode_id}; refusing stale token evidence"
        )
    if len(fresh) > 1:
        return None, (
            f"multiple {file_name} files for episode {episode_id} changed in "
            "this run; cannot attribute tokens unambiguously"
        )
    return fresh[0], None


def read_token_details_file(path: Path) -> list[dict[str, object]] | None:
    """Read one EMOS per-call usage details JSONL file.

    Args:
        path: The ``token_usage_details.jsonl`` written by the local
            accounting instrumentation.

    Returns:
        The list of per-call records, or ``None`` when the file does not
        exist or contains no parsable record.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[dict[str, object]] = []
    for line in lines:
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            records.append(document)
    return records or None


def _detail_int(usage: object, detail: str, field: str) -> int | None:
    """Read one nested usage-detail integer when present.

    Args:
        usage: The per-call usage mapping.
        detail: The detail object name (``prompt_tokens_details`` or
            ``completion_tokens_details``).
        field: The integer field inside the detail object.

    Returns:
        The integer value, or ``None`` when absent anywhere along the path.
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get(detail)
    if not isinstance(details, dict):
        return None
    value = details.get(field)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class TokenBreakdown(TypedDict):
    """Aggregated per-call usage totals for one episode.

    ``cached_tokens`` / ``reasoning_tokens`` are ``None`` when no record
    carried that detail (the upstream did not report it); the other sums
    are ``None`` only for an empty record set.
    """

    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    per_agent: dict[str, dict[str, int]]
    calls: int
    avg_latency_ms: float | None
    max_latency_ms: float | None


def aggregate_token_details(
    records: list[dict[str, object]],
) -> TokenBreakdown:
    """Aggregate per-call usage records into canonical breakdown totals.

    Args:
        records: Records from one ``token_usage_details.jsonl``.

    Returns:
        Sums over all calls: ``input_tokens``, ``output_tokens``,
        ``cached_tokens``, ``reasoning_tokens`` (each ``None`` when no
        record carried that field), per-agent input/output sums, and call
        count with latency summary.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    per_agent: dict[str, dict[str, int]] = {}
    latencies: list[float] = []

    def add_agent(agent: str, prompt: int, completion: int) -> None:
        """Accumulate one agent's prompt/completion sums.

        Args:
            agent: The agent name from the record.
            prompt: The call's prompt tokens.
            completion: The call's completion tokens.
        """
        entry = per_agent.setdefault(agent, {"prompt_tokens": 0, "completion_tokens": 0})
        entry["prompt_tokens"] += prompt
        entry["completion_tokens"] += completion

    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) and not isinstance(prompt, bool):
            input_tokens = (input_tokens or 0) + prompt
        if isinstance(completion, int) and not isinstance(completion, bool):
            output_tokens = (output_tokens or 0) + completion
        cached = _detail_int(usage, "prompt_tokens_details", "cached_tokens")
        if cached is not None:
            cached_tokens = (cached_tokens or 0) + cached
        reasoning = _detail_int(usage, "completion_tokens_details", "reasoning_tokens")
        if reasoning is not None:
            reasoning_tokens = (reasoning_tokens or 0) + reasoning
        agent_name = record.get("agent_name")
        if isinstance(agent_name, str) and isinstance(prompt, int) and isinstance(completion, int):
            add_agent(agent_name, prompt, completion)
        latency = record.get("latency_ms")
        if isinstance(latency, int | float) and not isinstance(latency, bool):
            latencies.append(float(latency))
    return TokenBreakdown(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        per_agent=per_agent,
        calls=len(records),
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
        max_latency_ms=round(max(latencies), 1) if latencies else None,
    )


def read_token_file(path: Path) -> dict[str, int] | None:
    """Read one EMOS token record file into per-agent totals.

    Args:
        path: The token_usage.json file to read.

    Returns:
        A mapping of agent name to actual total tokens, or ``None`` when the
        file does not exist or does not parse.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
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
        """Create the runner with empty evidence baselines.

        Args:
            **kwargs: Forwarded to the base runner constructor
                (``process_manager``, ``environment``, ``repository_root``).
        """
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._episode_log_baseline: dict[str, str] = {}
        self._token_baseline: dict[Path, tuple[int, int]] = {}
        self._resolved_cache: tuple[Path, dict[str, int]] | None = None

    def prepare(
        self,
        experiment: ExperimentSpec,
        process_spec: ProcessSpec,
        environment_spec: EnvironmentSpec,
    ) -> PreparedSystem:
        """Snapshot evidence baselines, then run the base preparation.

        Both baselines (episode-log records and token files) let collection
        attribute only this experiment's new evidence to each run; the
        episode-log baseline is refreshed after every run so a later run's
        fallback diff never claims an earlier run's episodes.

        Args:
            experiment: The experiment being prepared.
            process_spec: The resolved process specification.
            environment_spec: Portable environment expectations.

        Returns:
            The prepared-system handle from the base implementation.
        """
        self._episode_log_baseline = read_episode_log_steps(process_spec.working_directory)
        self._token_baseline = snapshot_token_files(process_spec.working_directory)
        self._resolved_cache = None
        return super().prepare(experiment, process_spec, environment_spec)

    def _resolved_episodes(self, run_directory: Path, prepared: PreparedSystem) -> dict[str, int]:
        """Determine the benchmark episodes this run actually executed.

        Prefers the official stdout banners; falls back to newly added
        records in EMOS's persistent episode-log step files (diffed against
        the snapshot refreshed after the previous run). The result is cached
        per run directory so :meth:`collect_result` and
        :meth:`resolve_episode_identity` agree without re-reading files.

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

        The stdout banner supplies the episode id; the optional pinned
        dataset (digest-verified against the ExperimentSpec) adds the scene
        id, dataset index, and record identity. Every lookup is honest:
        missing or ambiguous data stays unresolved with a reason.

        Args:
            prepared: The prepared-system handle carrying the resolved
                process specification.
            run_directory: The finished run's directory with persisted logs.
            outcome: The observed process outcome for this episode run.

        Returns:
            The resolution object recorded in the manifest's
            ``episode_selection.resolution``.
        """
        episodes = self._resolved_episodes(run_directory, prepared)
        if len(episodes) == 1:
            episode_id, steps = next(iter(episodes.items()))
            evidence_sources: list[JSONValue] = list(EPISODE_IDENTITY_SOURCES)
            return {
                "status": "resolved",
                "resolved_episode_id": episode_id,
                **self._dataset_identity_fields(prepared, episode_id),
                "official_num_steps": steps or None,
                "evidence_source": evidence_sources,
            }
        if len(episodes) > 1:
            episode_ids: list[JSONValue] = [str(episode) for episode in sorted(episodes)]
            return {
                "status": "multiple-episodes",
                "resolved_episode_ids": episode_ids,
                "resolved_scene_id": None,
                "dataset_index": None,
                "evidence_source": [EPISODE_IDENTITY_SOURCES[0]],
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

    def _dataset_identity_fields(self, prepared: PreparedSystem, episode_id: str) -> JSONObject:
        """Resolve the pinned-dataset fields for one episode id.

        Args:
            prepared: The prepared-system handle carrying the configured
                dataset path and the pinned digest.
            episode_id: The resolved habitat episode id.

        Returns:
            The dataset-derived manifest fields with an explicit
            ``dataset_status``; all payload fields stay ``None`` unless the
            digest-verified lookup succeeds.
        """
        dataset_path = prepared.process_spec.dataset_path
        if dataset_path is None:
            return {
                "resolved_scene_id": None,
                "dataset_index": None,
                "dataset_record": None,
                "dataset_status": "not-configured",
                "dataset_note": ("local config sets no dataset_path; scene id stays unresolved"),
            }
        identity = resolve_episode_in_dataset(dataset_path, prepared.dataset_digest, episode_id)
        return {
            "resolved_scene_id": identity.resolved_scene_id,
            "dataset_index": identity.dataset_index,
            "dataset_record": identity.dataset_record,
            "dataset_status": identity.status,
            "dataset_note": identity.detail,
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
        EMOS's own freshly-written ``token_usage.json`` (token_usage,
        located by snapshot diff, copied into the run directory as raw
        evidence with its source path recorded). Metrics without a reliable
        official source stay absent and are explained in
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
        averages = parse_evaluator_averages(run_directory)

        pddl_success = averages.pop(PDDL_SUCCESS_METRIC, None)
        if pddl_success is not None and "success" not in values and len(episodes) == 1:
            values["success"] = pddl_success >= 0.5
        elif pddl_success is None and "success" not in values:
            unavailable["success"] = "evaluator reported no pddl_success average in its logs"
        details["emos_pddl_success_average"] = pddl_success
        resolved_ids: list[JSONValue] = [str(episode) for episode in sorted(episodes)]
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
            working_directory = prepared.process_spec.working_directory or Path(".")
            evidence_directory = run_directory / TOKEN_EVIDENCE_DIRECTORY
            evidence_directory.mkdir(parents=True, exist_ok=True)
            token_path, token_reason = locate_fresh_token_file(
                prepared.process_spec.working_directory,
                episode_id,
                "token_usage.json",
                self._token_baseline,
            )
            token_totals = read_token_file(token_path) if token_path is not None else None
            if token_totals is not None and token_path is not None:
                values["token_usage"] = sum(token_totals.values())
                details["emos_token_usage_by_agent"] = dict(token_totals)
                details["emos_token_usage_source_path"] = token_path.relative_to(
                    working_directory
                ).as_posix()
                copied = evidence_directory / f"token_usage-{episode_id}.json"
                shutil.copyfile(token_path, copied)
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
                unavailable["token_usage"] = token_reason or "no fresh token record located"

            details_path, details_reason = locate_fresh_token_file(
                prepared.process_spec.working_directory,
                episode_id,
                "token_usage_details.jsonl",
                self._token_baseline,
            )
            detail_records = (
                read_token_details_file(details_path) if details_path is not None else None
            )
            if detail_records is not None and details_path is not None:
                breakdown = aggregate_token_details(detail_records)
                input_tokens = breakdown["input_tokens"]
                output_tokens = breakdown["output_tokens"]
                cached_tokens = breakdown["cached_tokens"]
                reasoning_tokens = breakdown["reasoning_tokens"]
                if input_tokens is not None:
                    values["token_input_usage"] = input_tokens
                else:
                    unavailable["token_input_usage"] = (
                        "per-call usage records carry no prompt-token counts"
                    )
                if output_tokens is not None:
                    values["token_output_usage"] = output_tokens
                else:
                    unavailable["token_output_usage"] = (
                        "per-call usage records carry no completion-token counts"
                    )
                if cached_tokens is not None:
                    values["cached_prompt_tokens"] = cached_tokens
                else:
                    unavailable["cached_prompt_tokens"] = (
                        "upstream usage records carry no cached-token detail for this episode"
                    )
                if reasoning_tokens is not None:
                    values["reasoning_tokens"] = reasoning_tokens
                else:
                    unavailable["reasoning_tokens"] = (
                        "upstream usage records carry no reasoning-token detail for this episode"
                    )
                details["emos_token_calls"] = breakdown["calls"]
                details["emos_token_breakdown_by_agent"] = cast(JSONValue, breakdown["per_agent"])
                details["emos_token_call_latency_ms"] = {
                    "avg": breakdown["avg_latency_ms"],
                    "max": breakdown["max_latency_ms"],
                }
                details["emos_token_details_source_path"] = details_path.relative_to(
                    working_directory
                ).as_posix()
                copied_details = evidence_directory / f"token_usage_details-{episode_id}.jsonl"
                shutil.copyfile(details_path, copied_details)
                evidence.append(
                    RawEvidenceRef(
                        path=f"{TOKEN_EVIDENCE_DIRECTORY}/{copied_details.name}",
                        description=(f"EMOS per-call LLM usage records for episode {episode_id}"),
                        media_type="application/x-ndjson",
                    )
                )
            else:
                unavailable.setdefault(
                    "token_input_usage",
                    details_reason or "no fresh per-call usage records located",
                )
                unavailable.setdefault(
                    "token_output_usage",
                    details_reason or "no fresh per-call usage records located",
                )
                unavailable.setdefault(
                    "cached_prompt_tokens",
                    "no per-call usage records; the accounting instrumentation patch "
                    "may be missing from the EMOS checkout",
                )
                unavailable.setdefault(
                    "reasoning_tokens",
                    "no per-call usage records; the accounting instrumentation patch "
                    "may be missing from the EMOS checkout",
                )
        else:
            # Batch (or identity-unresolved) runs: per-episode token
            # attribution is deferred, so the fields stay explicitly
            # unavailable instead of silently missing.
            batch_reason = (
                "run covered multiple or unresolved episodes; per-episode token "
                "attribution is deferred to single-episode runs"
            )
            for field in (
                "token_usage",
                "token_input_usage",
                "token_output_usage",
                "cached_prompt_tokens",
                "reasoning_tokens",
            ):
                unavailable.setdefault(field, batch_reason)

        # Subgoal rate prefers an explicit rate-named aggregate; otherwise the
        # official per-stage success aggregates (pddl_stage_goals.<stage>_
        # success) average into one rate. Raw stage values stay in details.
        subgoal_rate = averages.pop("subgoal_success_rate", None)
        stage_values = {
            key: value
            for key, value in averages.items()
            if key.startswith("pddl_stage_goals.") and key.endswith("_success")
        }
        if subgoal_rate is None:
            subgoal_rate = stage_goal_success_average(averages)
        if subgoal_rate is not None and "subgoal_success_rate" not in values:
            values["subgoal_success_rate"] = subgoal_rate
            if stage_values:
                details["emos_stage_goal_success"] = dict(stage_values)
                for stage_key in stage_values:
                    averages.pop(stage_key, None)
        else:
            unavailable.setdefault(
                "subgoal_success_rate",
                "evaluator logs carry no subgoal_success_rate aggregate and no "
                "pddl_stage_goals.*_success aggregates (see evaluation/README.md)",
            )

        if averages:
            details["emos_average_metrics"] = dict(averages)
        if unavailable:
            details["unavailable_metrics"] = dict(unavailable)
        return MetricsPayload(values=values, details=details, raw_evidence=tuple(evidence))
