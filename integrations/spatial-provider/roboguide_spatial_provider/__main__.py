"""Durable once-only spatial file jobs with an independently bounded CPU worker."""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import resource
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from . import occupancy, timing
from .contracts import Job, canonical, digest
from .geometry import code_digest, compute
from .occupancy_contracts import OccupancyJob
from .storage import immutable, read_json, read_source, save_json, snapshots, sync_directory


def now_ms() -> int:
    """Read local audit time; other machines' clocks are never compared to it."""
    return time.time_ns() // 1_000_000


def parse_job(request: dict[str, Any]) -> Job | OccupancyJob:
    """Select an explicit dimensional contract before any file effect or native allocation."""
    if request.get("schema_version") == "roboguide.spatial-occupancy-job/v0.1":
        return OccupancyJob.parse(request)
    return Job.parse(request)


def observe(directory: Path) -> dict[str, Any]:
    """Verify original computed proof without a worker launch or budget renewal."""
    intent = read_json(directory / "intent.json")
    result: dict[str, Any] = {
        "schema_version": "roboguide.spatial-file-result/v0.1",
        "job_id": intent["job_id"],
        "request_digest": intent["request_digest"],
        "status": "unknown",
        "reason": "original worker outcome is unavailable",
        "admitted_at_ms": intent["admitted_at_ms"],
        "deadline_ms": intent["deadline_ms"],
    }
    if (directory / "result.json").exists():
        result = read_json(directory / "result.json")
    if not (directory / "computed.json").exists():
        if (directory / "unavailable.json").exists():
            unavailable = read_json(directory / "unavailable.json")
            if unavailable.get("request_digest") != intent["request_digest"]:
                raise ValueError("unavailable evidence differs from original job")
            return {**result, "status": "unavailable", "reason": unavailable["reason"]}
        return result
    computed = read_json(directory / "computed.json")
    if (
        computed.get("request_digest") != intent["request_digest"]
        or computed.get("job_id") != intent["job_id"]
    ):
        raise ValueError("computed result differs from original admitted job")
    manifest = computed.get("manifest", {})
    if manifest.get("algorithm", {}).get("code_digest") != intent["code_digest"]:
        raise ValueError("computed algorithm identity differs from original admission")
    artifact = computed.get("artifact", {})
    if artifact.get("path") != "map.bundle":
        raise ValueError("computed artifact escaped its immutable result location")
    original_request = read_json(directory / "request.json")
    policy = parse_job(original_request).policy
    payload = read_source(directory, "map.bundle", policy.max_output_bytes)
    if digest(payload) != artifact.get("content_digest") or len(payload) != artifact.get(
        "byte_size"
    ):
        raise ValueError("computed output bytes are missing or differ from original proof")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("manifest.json")
        if (
            info.file_size > 1024 * 1024
            or info.compress_type != zipfile.ZIP_STORED
            or json.loads(archive.read("manifest.json")) != manifest
        ):
            raise ValueError("output bundle manifest differs from original computed metadata")
    computed_at = computed.get("computed_at_ms")
    if (
        not isinstance(computed_at, int)
        or isinstance(computed_at, bool)
        or computed_at < intent["admitted_at_ms"]
    ):
        raise ValueError("original computation time is unavailable or contradictory")
    late = timing.completed_late(intent, computed)
    if computed_at > intent["deadline_ms"] or late:
        computed = {**computed, "status": "completed_late"}
    canonical(computed)
    original_outcome = result.get("status")
    return {
        **computed,
        "admitted_at_ms": intent["admitted_at_ms"],
        "deadline_ms": intent["deadline_ms"],
        "original_observation": original_outcome,
        "reconciliation": "readonly-original-computed-proof",
        "timing_assurance": "legacy-wall-budget"
        if late is None
        else "original-host-monotonic-budget",
    }


def run(request: dict[str, Any], source_root: Path, store: Path) -> dict[str, Any]:
    """Admit once, freeze sources and retain the bounded worker outcome."""
    job = parse_job(request)
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    if store.is_symlink():
        raise ValueError("spatial store must not be a symlink")
    lock = os.open(store / ".writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = store / job.job_id
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("spatial job directory must not be a symlink")
        identity = digest(canonical(request))
        if (directory / "intent.json").exists():
            original = read_json(directory / "intent.json")
            if original.get("request_digest") != identity:
                raise ValueError("job identity conflicts with its immutable original request")
            return observe(directory)
        admitted = now_ms()
        save_json(directory / "request.json", request)
        intent = {
            "schema_version": "roboguide.spatial-file-intent/v0.1",
            "job_id": job.job_id,
            "request_digest": identity,
            "code_digest": code_digest(),
            "admitted_at_ms": admitted,
            "deadline_ms": admitted + job.policy.timeout_s * 1000,
            **timing.freeze(job.policy.timeout_s),
        }
        save_json(directory / "intent.json", intent)
        process: subprocess.Popen[bytes] | None = None
        try:
            inputs = (
                snapshots(job, source_root, directory)
                if isinstance(job, Job)
                else occupancy.snapshots(job, source_root, directory)
            )
            if job.parent is not None:
                parent = read_source(source_root, job.parent["path"], job.policy.max_output_bytes)
                if digest(parent) != job.parent["content_digest"]:
                    raise ValueError("parent source digest differs")
                immutable(directory / "parent.bundle", parent)
            save_json(directory / "sources.json", {"inputs": inputs})
            remaining = timing.remaining(intent, now_ms(), job.policy.timeout_s)
            if remaining <= 0:
                raise TimeoutError("source acquisition exhausted the original job budget")
            save_json(
                directory / "started.json", {"request_digest": identity, "started_at_ms": now_ms()}
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "CUDA_VISIBLE_DEVICES": "",
                    "OMP_NUM_THREADS": str(job.policy.threads),
                    "OPENBLAS_NUM_THREADS": str(job.policy.threads),
                    "MKL_NUM_THREADS": str(job.policy.threads),
                }
            )
            with (
                (directory / "worker.stdout").open("xb") as output,
                (directory / "worker.stderr").open("xb") as error,
            ):
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "roboguide_spatial_provider",
                        "--worker",
                        str(directory),
                    ],
                    env=environment,
                    stdout=output,
                    stderr=error,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired as timeout_error:
                    raise TimeoutError(
                        "native spatial worker exhausted its original wall budget"
                    ) from timeout_error
            result = observe(directory)
            if process.returncode != 0 and result["status"] != "completed":
                result["reason"] = (
                    "original native worker exited without a verified complete result"
                )
        except (ValueError, OSError, TimeoutError) as error:
            result = {
                "schema_version": "roboguide.spatial-file-result/v0.1",
                "job_id": job.job_id,
                "request_digest": identity,
                "admitted_at_ms": admitted,
                "deadline_ms": admitted + job.policy.timeout_s * 1000,
                "status": "unknown"
                if (directory / "started.json").exists()
                else "rejected_before_compute",
                "reason": str(error)[:4096],
            }
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        save_json(directory / "result.json", result)
        return observe(directory)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def worker(directory: Path) -> int:
    """Set hard process bounds before native imports and actual computation."""
    request = read_json(directory / "request.json")
    job = parse_job(request)
    intent = read_json(directory / "intent.json")
    if (
        intent.get("request_digest") != digest(canonical(request))
        or intent.get("code_digest") != code_digest()
    ):
        raise ValueError("worker source differs from original admitted authority")
    if (directory / "worker-started.json").exists():
        raise ValueError("original worker identity cannot run again")
    remaining = timing.remaining(intent, now_ms(), job.policy.timeout_s)
    if remaining <= 0:
        raise ValueError("original worker budget expired before native work")
    save_json(
        directory / "worker-started.json",
        {
            "request_digest": intent["request_digest"],
            "pid": os.getpid(),
            "received_at_ms": now_ms(),
        },
    )
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    resource.setrlimit(resource.RLIMIT_CPU, (job.policy.timeout_s, job.policy.timeout_s + 1))
    resource.setrlimit(resource.RLIMIT_AS, (job.policy.memory_mb * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (job.policy.max_output_bytes,) * 2)
    try:
        if isinstance(job, Job):
            compute(directory)
        else:
            occupancy.compute(directory)
        sync_directory(directory)
        return 0
    except Exception as error:
        save_json(
            directory / "unavailable.json",
            {"reason": str(error)[:4096], "request_digest": digest(canonical(request))},
        )
        return 2


def main() -> None:
    """Run or observe file jobs without Node registration or localization authority."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--observe", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker is not None:
        raise SystemExit(worker(args.worker))
    if args.observe is not None:
        print(canonical(observe(args.observe)).decode())
        return
    if args.request is None or args.source_root is None or args.store is None:
        parser.error("run requires --request, --source-root and --store")
    print(canonical(run(read_json(args.request), args.source_root, args.store)).decode())


if __name__ == "__main__":
    main()
