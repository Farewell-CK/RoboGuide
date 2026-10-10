"""Bounded external-process batch supervision, independent of SUT success authority.

Each configured command runs one pair and writes its original arm outcomes. Durable
exclusive receipts prevent outcome retries, including after supervisor restart.
Only owned process sessions are managed; this module never imports simulator code.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from roboguide_eval.e1_attempt import terminate_owned_session
from roboguide_eval.models import JSONObject, JSONValue
from roboguide_eval.process import expand_environment_values

SCHEMA = "roboguide.e1.batch/v0.1"
_SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z")
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|AUTHORIZATION|PASSWORD", re.I)
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


def read_json(path: Path) -> JSONObject:
    """Read a bounded object; malformed or incomplete evidence never means success."""
    if path.stat().st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("batch document exceeds byte budget")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("batch document must be an object")
    return cast(JSONObject, value)


def write_json(path: Path, value: JSONObject) -> None:
    """Atomically replace derived state, preserving immutable receipts and arm evidence."""
    content = json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n"
    if len(content.encode()) > _MAX_DOCUMENT_BYTES:
        raise ValueError("batch document exceeds byte budget")
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_digest(path: Path) -> str:
    """Hash public files incrementally; credentials are never fingerprint inputs."""
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _object(value: JSONValue, label: str) -> JSONObject:
    """Require a mapping at a manifest boundary rather than coercing missing values."""
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    return value


def _strings(value: JSONValue, label: str) -> tuple[str, ...]:
    """Validate public string arrays before they become process arguments."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(label + " must be a string array")
    return tuple(cast(list[str], value))


@dataclass(frozen=True)
class BatchJob:
    """Freeze one pair's command, private work directory, ports and wall budget."""

    job_id: str
    argv: tuple[str, ...]
    cwd: Path
    outcome_path: Path
    environment: Mapping[str, str]
    ports: tuple[int, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class BatchSpec:
    """Carry a public immutable queue and its preregistered capacity policy."""

    jobs: tuple[BatchJob, ...]
    sources: Mapping[Path, str]
    max_workers: int
    gpu_total_mib: float
    gpu_baseline_mib: float
    gpu_fraction: float
    manifest_sha256: str

    @classmethod
    def load(cls, path: Path) -> BatchSpec:
        """Validate identities, port isolation and secret references before any launch."""
        document = read_json(path)
        if document.get("schema_version") != SCHEMA:
            raise ValueError("unsupported batch schema")
        raw_jobs = document.get("jobs")
        if not isinstance(raw_jobs, list) or not 1 <= len(raw_jobs) <= 1000:
            raise ValueError("batch requires 1..1000 jobs")
        jobs = []
        identifiers: set[str] = set()
        ports_seen: set[int] = set()
        outputs: set[Path] = set()
        for raw in raw_jobs:
            row = _object(raw, "job")
            job_id = row.get("job_id")
            if not isinstance(job_id, str) or not _SAFE_ID.fullmatch(job_id):
                raise ValueError("unsafe job identity")
            if job_id in identifiers:
                raise ValueError("duplicate job identity")
            identifiers.add(job_id)
            argv = _strings(row.get("argv"), "argv")
            if not argv:
                raise ValueError("empty job command")
            if any(_SECRET_NAME.search(value) for value in argv if value.startswith("--")):
                raise ValueError("credentials belong in referenced environment, not argv")
            environment: dict[str, str] = {}
            for name, value in _object(row.get("environment", {}), "environment").items():
                if not isinstance(value, str):
                    raise ValueError("environment values must be strings")
                if _SECRET_NAME.search(name) and not re.fullmatch(r"\$\{[A-Z0-9_]+\}", value):
                    raise ValueError("secret values must use environment references")
                environment[name] = value
            raw_ports = row.get("ports", [])
            if not isinstance(raw_ports, list) or not all(
                type(port) is int and 1 <= port <= 65535 for port in raw_ports
            ):
                raise ValueError("invalid job ports")
            ports = tuple(cast(list[int], raw_ports))
            if len(set(ports)) != len(ports) or ports_seen.intersection(ports):
                raise ValueError("jobs require disjoint ports")
            ports_seen.update(ports)
            cwd_value, output_value = row.get("cwd"), row.get("outcome_path")
            if not isinstance(cwd_value, str) or not isinstance(output_value, str):
                raise ValueError("cwd and outcome_path must be absolute paths")
            cwd, output = Path(cwd_value), Path(output_value)
            if not cwd.is_absolute() or not output.is_absolute() or output in outputs:
                raise ValueError("job paths must be absolute and outputs distinct")
            outputs.add(output)
            timeout = row.get("timeout_seconds", 36060)
            if type(timeout) not in (float, int):
                raise ValueError("invalid pair wall budget")
            timeout_number = float(cast(float, timeout))
            if not 0 < timeout_number <= 172800:
                raise ValueError("invalid pair wall budget")
            jobs.append(BatchJob(job_id, argv, cwd, output, environment, ports, timeout_number))
        sources = {}
        for name, value in _object(document.get("source_sha256", {}), "sources").items():
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("invalid source digest")
            source = Path(name)
            if not source.is_absolute() or source.name.endswith(".env"):
                raise ValueError("source paths must be absolute")
            sources[source] = value
        policy = _object(document.get("capacity", {}), "capacity")
        workers = policy.get("max_workers", 1)
        if type(workers) is not int or workers not in (1, 2):
            raise ValueError("capacity supports one or two workers")
        numbers = [
            policy.get(name, default)
            for name, default in (
                ("gpu_total_mib", 0),
                ("gpu_baseline_mib", 0),
                ("gpu_fraction", 0.8),
            )
        ]
        if not all(type(value) in (int, float) for value in numbers):
            raise ValueError("invalid capacity observations")
        total, baseline, fraction = (float(cast(float, value)) for value in numbers)
        if not all(math.isfinite(value) for value in (total, baseline, fraction)) or (
            not 0 < fraction <= 1 or total < 0 or baseline < 0
        ):
            raise ValueError("invalid capacity limits")
        return cls(tuple(jobs), sources, workers, total, baseline, fraction, file_digest(path))

    def verify_sources(self) -> None:
        """Fail before a new pair if any frozen public source or deployment input changed."""
        if any(file_digest(path) != expected for path, expected in self.sources.items()):
            raise ValueError("frozen batch source changed")


def process_identity(pid: int) -> JSONObject | None:
    """Read Linux boot/start identity, treating zombies and reused PIDs as gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {
            "pid": pid,
            "start_ticks": fields[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, IndexError):
        return None


def receipt_is_live(receipt: JSONObject) -> bool:
    """Match the exact worker lifetime; a receipt never grants authority over another PID."""
    pid = receipt.get("pid")
    expected = receipt.get("process_identity")
    if type(pid) is not int or not isinstance(expected, dict):
        return False
    actual = process_identity(pid)
    return actual is not None and actual == expected


def capacity_after_probe(spec: BatchSpec, result: JSONObject) -> int:
    """Admit a second worker only from complete observed first-pair memory evidence."""
    resources = result.get("resource_usage")
    if spec.max_workers != 2 or not isinstance(resources, dict):
        return 1
    if resources.get("both_arms_measured") is not True:
        return 1
    peak = resources.get("peak_job_gpu_mib")
    if type(peak) not in (float, int):
        return 1
    peak_number = float(cast(float, peak))
    if not math.isfinite(peak_number) or peak_number <= 0 or spec.gpu_total_mib <= 0:
        return 1
    return (
        2
        if spec.gpu_baseline_mib + 2 * peak_number <= spec.gpu_fraction * spec.gpu_total_mib
        else 1
    )


def _pump(source: BinaryIO, target: Path, secrets: tuple[bytes, ...]) -> None:
    """Stream bounded logs, retaining enough carry to redact split credential values."""
    carry = b""
    margin = max((len(value) for value in secrets), default=1)
    with target.open("wb") as sink:
        read = getattr(source, "read1", source.read)
        while chunk := read(4096):
            carry += chunk
            for value in secrets:
                carry = carry.replace(value, b"[REDACTED]")
            boundary = max(0, len(carry) - margin)
            sink.write(carry[:boundary])
            carry = carry[boundary:]
        for value in secrets:
            carry = carry.replace(value, b"[REDACTED]")
        sink.write(carry)


def _terminate_owned(child: subprocess.Popen[bytes]) -> None:
    """Allow the driver its inner 120s archive and 60s capture drain before escalation."""
    terminate_owned_session(child, grace_seconds=240)


def _capture_log(
    source: BinaryIO, target: Path, secrets: tuple[bytes, ...], status: JSONObject
) -> None:
    """Expose log archival failure to the worker instead of losing a thread exception."""
    try:
        _pump(source, target, secrets)
        status["closed"] = True
    except Exception as error:
        status["error_type"] = type(error).__name__
        # Drain after disk failure so logging cannot block the SUT process pipe.
        try:
            while source.read(4096):
                pass
        except OSError:
            pass


def run_worker(manifest: Path, output: Path, job_id: str) -> int:
    """Claim once before launching; preserve driver outcomes and never retry an arm."""
    spec = BatchSpec.load(manifest)
    job = next(row for row in spec.jobs if row.job_id == job_id)
    directory = output / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    receipt: JSONObject = {
        "job_id": job_id,
        "manifest_sha256": spec.manifest_sha256,
        "pid": os.getpid(),
        "process_identity": process_identity(os.getpid()),
    }
    try:
        (directory / "claim").mkdir()
    except FileExistsError:
        return 3
    write_json(directory / "receipt.json", receipt)
    child: subprocess.Popen[bytes] | None = None
    threads: list[threading.Thread] = []
    log_statuses: list[JSONObject] = []
    result: JSONObject = {
        "job_id": job_id,
        "infrastructure_failure": True,
        "fatal_failure": False,
        "arms": [],
    }
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(signum: int, frame: object) -> None:
        """Allow owned child archival/cleanup when this worker is externally stopped."""
        raise InterruptedError("batch worker interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        spec.verify_sources()
        if job.outcome_path.exists():
            raise ValueError("refusing existing pair outcome")
        env = os.environ.copy()
        env.update(expand_environment_values(job.environment, os.environ))
        secrets = tuple(
            value.encode() for name, value in env.items() if _SECRET_NAME.search(name) and value
        )
        child = subprocess.Popen(
            job.argv,
            cwd=job.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        write_json(
            directory / "child.json",
            {"process_identity": process_identity(child.pid), "pid": child.pid},
        )
        assert child.stdout is not None and child.stderr is not None
        for source, name in ((child.stdout, "stdout.log"), (child.stderr, "stderr.log")):
            status: JSONObject = {"stream": name, "closed": False}
            log_statuses.append(status)
            thread = threading.Thread(
                target=_capture_log, args=(source, directory / name, secrets, status), daemon=True
            )
            thread.start()
            threads.append(thread)
        try:
            child.wait(timeout=job.timeout_seconds)
        except subprocess.TimeoutExpired:
            result["error_type"] = "pair_wall_timeout"
            _terminate_owned(child)
        if job.outcome_path.is_file():
            observed = read_json(job.outcome_path)
            if observed.get("job_id") != job_id or any(
                type(observed.get(name)) is not bool
                for name in ("infrastructure_failure", "fatal_failure")
            ):
                raise ValueError("invalid driver attribution")
            result = observed
        if child.returncode != 0:
            result["infrastructure_failure"] = True
            result["driver_exit_code"] = child.returncode
        spec.verify_sources()
    except Exception as error:
        result["infrastructure_failure"] = True
        result["error_type"] = type(error).__name__
        if isinstance(error, ValueError) and str(error) == "frozen batch source changed":
            result["fatal_failure"] = True
            result["reason"] = "frozen_source_changed"
    finally:
        if child is not None:
            _terminate_owned(child)
        for thread in threads:
            thread.join(timeout=15)
        if any(thread.is_alive() for thread in threads) or any(
            status.get("closed") is not True for status in log_statuses
        ):
            result["infrastructure_failure"] = True
            result["fatal_failure"] = True
            result["reason"] = "worker_log_capture_unclosed"
        result["worker_log_capture"] = cast(list[JSONValue], log_statuses)
        write_json(directory / "result.json", result)
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


def _job_result(directory: Path, job: BatchJob) -> JSONObject | None:
    """Observe existing receipts/results; loss of a claimed worker remains non-retryable."""
    result = directory / "result.json"
    receipt = directory / "receipt.json"
    if result.is_file():
        try:
            observed = read_json(result)
            if observed.get("job_id") != job.job_id or any(
                type(observed.get(field)) is not bool
                for field in ("infrastructure_failure", "fatal_failure")
            ):
                raise ValueError("invalid result attribution")
            return observed
        except (OSError, ValueError):
            return {
                "job_id": job.job_id,
                "infrastructure_failure": True,
                "fatal_failure": True,
                "arms": [],
                "reason": "invalid_worker_result_preserved",
            }
    if receipt.is_file() and not receipt_is_live(read_json(receipt)):
        interrupted: JSONObject = {
            "job_id": job.job_id,
            "infrastructure_failure": True,
            "fatal_failure": True,
            "arms": [],
            "reason": "claimed_worker_interrupted_no_retry",
        }
        write_json(result, interrupted)
        return interrupted
    claim = directory / "claim"
    if claim.exists() and not receipt.exists() and time.time() - claim.stat().st_mtime > 30:
        interrupted = {
            "job_id": job.job_id,
            "infrastructure_failure": True,
            "fatal_failure": True,
            "arms": [],
            "reason": "ambiguous_claim_without_receipt_no_retry",
        }
        write_json(result, interrupted)
        return interrupted
    return None


def summarize_batch(spec: BatchSpec, results: Mapping[str, JSONObject]) -> JSONObject:
    """Count tri-state outcomes without converting local completion or unavailable to success."""
    arms: dict[str, JSONValue] = {}
    for arm in ("native", "roboguide"):
        labels = ("native", "emos") if arm == "native" else (arm,)
        rows: list[JSONObject] = []
        for result in results.values():
            values = result.get("arms", [])
            if isinstance(values, list):
                rows.extend(
                    row for row in values if isinstance(row, dict) and row.get("arm") in labels
                )
        formal = [
            row
            for row in rows
            if isinstance(row.get("admission"), dict)
            and cast(JSONObject, row["admission"]).get("valid_for_formal_population") is True
        ]
        arms[arm] = {
            "registered": len(spec.jobs),
            "finished": len(rows),
            "official_true": sum(row.get("official_pddl_success") is True for row in rows),
            "official_false": sum(row.get("official_pddl_success") is False for row in rows),
            "official_unavailable": sum(row.get("official_pddl_success") is None for row in rows),
            "formal_admitted": len(formal),
            "formal_successes": sum(row.get("official_pddl_success") is True for row in formal),
        }
    return {
        "registered_pairs": len(spec.jobs),
        "finished_pairs": len(results),
        "infrastructure_pairs": sum(
            row.get("infrastructure_failure") is True for row in results.values()
        ),
        "arms": arms,
        "strict_fairness_claim": False,
    }


def supervise(manifest: Path, output: Path) -> int:
    """Run a durable queue, attaching to live workers after restart without relaunching claims."""
    spec = BatchSpec.load(manifest)
    lock = (output / "supervisor.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 3
    children: dict[str, subprocess.Popen[bytes]] = {}
    pending_since: dict[str, float] = {}
    state_path = output / "status.json"
    results: dict[str, JSONObject] = {}
    try:
        while True:
            active: list[str] = []
            queued = []
            for job in spec.jobs:
                directory = output / "jobs" / job.job_id
                observed = _job_result(directory, job)
                if observed is not None:
                    results[job.job_id] = observed
                elif (directory / "receipt.json").exists():
                    active.append(job.job_id)
                elif job.job_id in pending_since:
                    if time.monotonic() - pending_since[job.job_id] > 30:
                        result: JSONObject = {
                            "job_id": job.job_id,
                            "infrastructure_failure": True,
                            "fatal_failure": True,
                            "arms": [],
                            "reason": "worker_failed_before_receipt",
                        }
                        write_json(directory / "result.json", result)
                        results[job.job_id] = result
                    else:
                        active.append(job.job_id)
                elif (directory / "claim").exists():
                    active.append(job.job_id)
                else:
                    queued.append(job)
            paused = (output / "STOP_REQUESTED.json").exists()
            reason: str | None = "user_requested_pause" if paused else None
            consecutive = 0
            for job in spec.jobs:
                observed = results.get(job.job_id)
                if observed is None:
                    consecutive = 0
                    continue
                if observed.get("fatal_failure") is True:
                    paused, reason = True, "fatal_evidence_or_identity_failure"
                    break
                consecutive = (
                    consecutive + 1 if observed.get("infrastructure_failure") is True else 0
                )
                if consecutive >= 3:
                    paused, reason = True, "three_consecutive_infrastructure_pairs"
                    break
            capacity = capacity_after_probe(spec, results.get(spec.jobs[0].job_id, {}))
            if not paused:
                try:
                    spec.verify_sources()
                except (OSError, ValueError):
                    paused, reason = True, "frozen_source_unavailable_or_changed"
            while queued and len(active) < capacity and not paused:
                job = queued.pop(0)
                directory = output / "jobs" / job.job_id
                directory.mkdir(parents=True, exist_ok=True)
                if job.job_id not in pending_since:
                    # The worker claim also fences a crash between Popen and receipt.
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "roboguide_eval.e1_batch",
                            "worker",
                            str(manifest),
                            str(output),
                            job.job_id,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    children[job.job_id] = child
                    pending_since[job.job_id] = time.monotonic()
                    active.append(job.job_id)
            state = (
                "paused" if paused else "completed" if len(results) == len(spec.jobs) else "running"
            )
            write_json(
                state_path,
                {
                    "state": state,
                    "reason": reason,
                    "workers": capacity,
                    "active": list(active),
                    "manifest_sha256": spec.manifest_sha256,
                    "summary": summarize_batch(spec, results),
                    "supervisor_identity": process_identity(os.getpid()),
                },
            )
            write_json(output / "SUMMARY.json", summarize_batch(spec, results))
            if state == "completed" or (paused and not active):
                return 0 if state == "completed" else 2
            time.sleep(1)
    finally:
        # Detached live workers keep their own receipt and logging; resume only observes them.
        for child in children.values():
            if child.poll() is not None:
                child.wait()
        lock.close()


def batch_command(action: str, output: Path, manifest: Path | None = None) -> JSONObject:
    """Start/resume background supervision, observe status or stop new dispatch only."""
    output = output.resolve()
    if action == "status":
        return read_json(output / "status.json")
    if action == "stop":
        write_json(output / "STOP_REQUESTED.json", {"pause_new_dispatch": True})
        return {"state": "pause_requested", "running_pairs": "allowed to archive and finish"}
    if action == "start":
        if manifest is None:
            raise ValueError("start requires a public manifest")
        spec = BatchSpec.load(manifest)
        spec.verify_sources()
        output.mkdir(parents=True, exist_ok=False)
        # The exact bytes are frozen; do not resolve or persist secret environment references.
        (output / "manifest.json").write_bytes(manifest.read_bytes())
    elif action != "resume":
        raise ValueError("unknown batch action")
    frozen = output / "manifest.json"
    BatchSpec.load(frozen).verify_sources()
    if action == "resume":
        (output / "STOP_REQUESTED.json").unlink(missing_ok=True)
    state_path = output / "status.json"
    if state_path.is_file():
        identity = read_json(state_path).get("supervisor_identity")
        if isinstance(identity, dict) and type(identity.get("pid")) is int:
            if process_identity(cast(int, identity["pid"])) == identity:
                return read_json(state_path)
        if read_json(state_path).get("state") == "completed":
            return read_json(state_path)
    # A restart never clears a fatal result or an exclusive worker receipt.
    (output / "STOP_REQUESTED.json").unlink(missing_ok=True)
    with (output / "supervisor.log").open("ab") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "roboguide_eval.e1_batch",
                "supervise",
                str(frozen),
                str(output),
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return {"state": "supervisor_started", "pid": child.pid, "output": str(output)}


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        raise SystemExit(run_worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
    raise SystemExit(supervise(Path(sys.argv[2]), Path(sys.argv[3])))
