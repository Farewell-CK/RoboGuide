"""Run identity, per-run result directories, and result summarization.

Every harness run owns a unique, fully traceable identity: a
:class:`RunManifest` records the experiment, system under test, benchmark
task, episode, seed, LLM configuration, digests, timestamps, command,
environment information, and process outcome. Combined with the per-run
directory (manifest.json, metrics.json, trace.jsonl, stdout.log,
stderr.log), this guarantees that even a failed external run leaves complete
evidence behind.

Run directories are experimental results and are never committed to Git.
Manifest environment overrides are persisted exactly as configured, so
``${VAR}`` credential references stay unresolved placeholders in the manifest
and real secrets only ever exist in the spawned child's environment.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from roboguide_eval.models import RESERVED_RUN_FILE_NAMES, JSONObject, JSONValue

MANIFEST_SCHEMA: Final = "roboguide-eval.run-manifest/v0.1"
MANIFEST_FILE_NAME: Final = "manifest.json"
METRICS_PAYLOAD_FILE_NAME: Final = "metrics.json"
TRACE_FILE_NAME: Final = "trace.jsonl"
STDOUT_FILE_NAME: Final = "stdout.log"
STDERR_FILE_NAME: Final = "stderr.log"
GIT_SHA_ENVIRONMENT_VARIABLE: Final = "ROBOGUIDE_EVAL_GIT_SHA"
# Files the harness itself owns inside a run directory; systems under test
# must write their raw evidence under other names. The canonical definition
# lives in models.py so ExperimentSpec validation uses the same set.
RESERVED_RUN_FILES: Final = RESERVED_RUN_FILE_NAMES


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        A timezone-aware ISO-8601 timestamp with seconds precision.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Return a unique, time-sortable run identifier.

    Returns:
        A ``<UTC timestamp>-<random suffix>`` identifier unique per call.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def current_git_commit(repository_root: Path, environment: Mapping[str, str]) -> str:
    """Return the RoboGuide Git SHA used for provenance in run manifests.

    An explicit ``ROBOGUIDE_EVAL_GIT_SHA`` override wins so that packaged or
    containerized runs can pin provenance without a ``.git`` directory.

    Args:
        repository_root: Directory containing the RoboGuide Git checkout.
        environment: Harness environment consulted for the override.

    Returns:
        The resolved commit SHA, or ``"unknown"`` when Git is unavailable or
        the directory is not a working checkout. Never raises.
    """
    override = environment.get(GIT_SHA_ENVIRONMENT_VARIABLE)
    if override:
        return override
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Describe one run's complete, reproducible identity and outcome.

    ``requested_model`` is the configured alias; ``reported_model`` is
    reserved for the concrete model identifier an LLM API later returns, so
    model-level analysis never has to trust configuration names alone.
    ``environment_overrides`` stores configured values verbatim, keeping
    ``${VAR}`` credential placeholders unresolved.
    """

    experiment_id: str
    system: str
    benchmark: str
    task: str
    episode_id: str
    seed: int
    run_id: str
    config_digest: str
    started_at: str
    ended_at: str
    command: tuple[str, ...]
    working_directory: str | None
    process_status: str
    exit_code: int | None
    timed_out: bool
    failure_reason: str | None
    llm_provider: str
    requested_model: str
    reasoning_effort: str | None
    reasoning_options: Mapping[str, str]
    roboguide_git_sha: str
    system_version: str | None
    dataset_identity: str | None
    dataset_digest: str | None
    reported_model: str | None = None
    environment_overrides: Mapping[str, str] = field(default_factory=dict)
    context: Mapping[str, str] = field(default_factory=dict)
    environment_information: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> JSONObject:
        """Serialize the manifest into its versioned JSON form.

        Returns:
            A JSON object containing every reproducibility field.
        """
        return {
            "schema": MANIFEST_SCHEMA,
            "experiment_id": self.experiment_id,
            "system": self.system,
            "benchmark": self.benchmark,
            "task": self.task,
            "episode_id": self.episode_id,
            "seed": self.seed,
            "run_id": self.run_id,
            "config_digest": self.config_digest,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "process_status": self.process_status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "failure_reason": self.failure_reason,
            "llm": {
                "provider": self.llm_provider,
                "requested_model": self.requested_model,
                "reported_model": self.reported_model,
                "reasoning_effort": self.reasoning_effort,
                "reasoning_options": dict(sorted(self.reasoning_options.items())),
            },
            "provenance": {
                "roboguide_git_sha": self.roboguide_git_sha,
                "system_version": self.system_version,
                "dataset_identity": self.dataset_identity,
                "dataset_digest": self.dataset_digest,
            },
            "environment_overrides": dict(sorted(self.environment_overrides.items())),
            "context": dict(sorted(self.context.items())),
            "environment_information": dict(sorted(self.environment_information.items())),
        }


def harness_environment_information(conda_environment: str | None) -> dict[str, str]:
    """Collect harness-side environment facts recorded with every manifest.

    Args:
        conda_environment: Conda environment name used by the external
            system, when one is configured.

    Returns:
        A mapping with the Python version, platform, and Conda environment.
    """
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "conda_environment": conda_environment or "",
    }


def create_run_directory(results_root: Path, experiment_id: str, run_id: str) -> Path:
    """Create and return the result directory for one run.

    Args:
        results_root: Root directory holding all experiment results.
        experiment_id: Experiment identifier used as the parent directory.
        run_id: Unique run identifier used as the run directory name.

    Returns:
        The created run directory path.
    """
    run_directory = results_root / experiment_id / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    return run_directory


class TraceWriter:
    """Append structured, ordered events to a run's ``trace.jsonl``.

    Each line is one JSON object with a monotonic sequence number, UTC
    timestamp, event name, and detail mapping, giving every run an
    inspectable event trace as required by repository testing guidelines.
    """

    def __init__(self, path: Path) -> None:
        """Create a trace writer bound to one run's trace file.

        Args:
            path: Target ``trace.jsonl`` path inside the run directory.
        """
        self._path = path
        self._sequence = 0

    def append(self, event: str, detail: Mapping[str, JSONValue]) -> None:
        """Append one event to the trace file.

        Args:
            event: Short event name, for example ``run_started``.
            detail: JSON-compatible event payload.
        """
        self._sequence += 1
        record = {
            "seq": self._sequence,
            "time": utc_now_iso(),
            "event": event,
            "detail": dict(detail),
        }
        with self._path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")


class RunArtifactWriter:
    """Own the file layout of one run directory.

    The writer creates the directory up front and exposes the stdout/stderr
    log paths before any process starts, so a failed spawn still leaves the
    expected files behind. Manifest, metrics, and trace writes are explicit
    and idempotent.
    """

    def __init__(self, run_directory: Path) -> None:
        """Prepare a run directory for artifact writes.

        Args:
            run_directory: Directory that receives this run's artifacts.
        """
        self._run_directory = run_directory
        run_directory.mkdir(parents=True, exist_ok=True)
        self._trace = TraceWriter(run_directory / TRACE_FILE_NAME)

    @property
    def path(self) -> Path:
        """Return the run directory path.

        Returns:
            The directory holding this run's artifacts.
        """
        return self._run_directory

    @property
    def stdout_path(self) -> Path:
        """Return the persisted stdout log path.

        Returns:
            Path of ``stdout.log`` inside the run directory.
        """
        return self._run_directory / STDOUT_FILE_NAME

    @property
    def stderr_path(self) -> Path:
        """Return the persisted stderr log path.

        Returns:
            Path of ``stderr.log`` inside the run directory.
        """
        return self._run_directory / STDERR_FILE_NAME

    @property
    def trace(self) -> TraceWriter:
        """Return the run's trace writer.

        Returns:
            The :class:`TraceWriter` bound to this run directory.
        """
        return self._trace

    def write_manifest(self, manifest: RunManifest) -> None:
        """Persist the run manifest.

        Args:
            manifest: The manifest to serialize into ``manifest.json``.
        """
        self._write_json(MANIFEST_FILE_NAME, manifest.to_json())

    def write_metrics(self, metrics_json: JSONObject) -> None:
        """Persist the canonical metrics payload document.

        Args:
            metrics_json: Serialized :class:`~roboguide_eval.metrics.MetricsPayload`.
        """
        self._write_json(METRICS_PAYLOAD_FILE_NAME, metrics_json)

    def _write_json(self, file_name: str, payload: JSONObject) -> None:
        """Write one JSON document into the run directory.

        Args:
            file_name: Target file name inside the run directory.
            payload: JSON object to serialize.
        """
        target = self._run_directory / file_name
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _text_field(document: JSONObject, key: str) -> str | None:
    """Read an optional string field from a decoded JSON document.

    Args:
        document: Decoded manifest/summary document.
        key: Field to read.

    Returns:
        The string value, or ``None`` when absent or not a string.
    """
    value = document.get(key)
    return value if isinstance(value, str) else None


def _number_field(document: JSONObject, key: str) -> float | int | None:
    """Read an optional numeric field from a decoded JSON document.

    Args:
        document: Decoded summary document.
        key: Field to read.

    Returns:
        The numeric value, or ``None`` when absent or not numeric.
    """
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def summarize_results(results_root: Path) -> JSONObject:
    """Summarize every run directory below a results root.

    Args:
        results_root: Either a single run directory containing
            ``manifest.json`` or a parent directory whose subtree contains
            run directories.

    Returns:
        A JSON object with one entry per run plus aggregate counts
        (total/completed/failed, success rate over runs reporting the
        canonical ``success`` metric, and total wall time).
    """
    manifest_paths = sorted(results_root.rglob(MANIFEST_FILE_NAME)) if results_root.exists() else []
    runs: list[JSONObject] = []
    success_known = 0
    success_count = 0
    total_wall_time = 0.0
    for manifest_path in manifest_paths:
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        run_directory = manifest_path.parent
        metrics_summary: JSONObject = {}
        metrics_path = run_directory / METRICS_PAYLOAD_FILE_NAME
        if metrics_path.is_file():
            try:
                metrics_document = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metrics_document = {}
            if isinstance(metrics_document, dict):
                values = metrics_document.get("values")
                if isinstance(values, dict):
                    metrics_summary = {"values": values}
                    success = values.get("success")
                    if isinstance(success, bool):
                        success_known += 1
                        success_count += int(success)
                    wall_time = values.get("wall_time")
                    if isinstance(wall_time, int | float) and not isinstance(wall_time, bool):
                        total_wall_time += float(wall_time)
        process_status = _text_field(document, "process_status") or "unknown"
        exit_code = _number_field(document, "exit_code")
        failed = process_status != "completed" or exit_code != 0
        runs.append(
            {
                "experiment_id": _text_field(document, "experiment_id"),
                "system": _text_field(document, "system"),
                "run_id": _text_field(document, "run_id"),
                "episode_id": _text_field(document, "episode_id"),
                "seed": _number_field(document, "seed"),
                "process_status": process_status,
                "exit_code": exit_code,
                "failure_reason": _text_field(document, "failure_reason"),
                "run_directory": str(run_directory),
                "metrics": metrics_summary,
                "failed": failed,
            }
        )
    completed = sum(1 for run in runs if not run["failed"])
    runs_payload: list[JSONValue] = list(runs)
    return {
        "results_root": str(results_root),
        "runs": runs_payload,
        "aggregate": {
            "total": len(runs),
            "completed": completed,
            "failed": len(runs) - completed,
            "success_metric_known": success_known,
            "success_count": success_count,
            "success_rate": (success_count / success_known) if success_known else None,
            "total_wall_time": total_wall_time,
        },
    }
