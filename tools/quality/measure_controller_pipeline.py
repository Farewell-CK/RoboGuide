"""Measure the real Controller with isolated loopback synthetic Node Protocol sessions.

This bounded engineering probe starts no simulator, Provider or hardware and is
not E6. It preserves databases, authored smoke logs, diagnostic windows and exact
binary/source identities in a new output directory. Timing is observational.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def save(path: Path, value: Any) -> None:
    """Write nonsecret fixture evidence with actual newlines and stable key ordering."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    """Hash the exact measured binary or input bytes without loading them all at once."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ports() -> list[int]:
    """Find three distinct loopback ports; startup detects any intervening bind race."""
    sockets = [socket.socket() for _ in range(3)]
    try:
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [int(listener.getsockname()[1]) for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()


def await_ready(process: subprocess.Popen[bytes], port: int) -> None:
    """Require the owned process to serve health before admitting synthetic participants."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("owned Controller exited before readiness; inspect its log")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz", timeout=0.5
            ) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    raise TimeoutError("owned Controller readiness exceeded ten seconds")


def diagnostic_totals(path: Path) -> dict[str, Any]:
    """Aggregate global dimensions once, preserving histogram upper bounds and lost evidence."""
    totals: dict[str, Any] = {}
    loss = {"dropped_records_total": 0, "io_errors_total": 0, "omitted_session_dimensions": 0}
    windows = 0
    for file in [Path(str(path) + ".1"), path]:
        if not file.exists():
            continue
        for line in file.read_text(encoding="utf-8").splitlines():
            window = json.loads(line)
            windows += 1
            for key in loss:
                loss[key] = (
                    loss[key] + window[key]
                    if key == "omitted_session_dimensions"
                    else max(loss[key], window[key])
                )
            for metric in window["metrics"]:
                if metric["identity"] is not None:
                    continue
                key = metric["kind"] + "/" + metric["stage"]
                sample = metric["summary"]
                total = totals.setdefault(
                    key, {"samples": 0, "sum_us": 0, "max_us": 0, "buckets": [0] * 65}
                )
                duration = sample["duration_us"]
                total["samples"] += duration["samples"]
                total["sum_us"] += duration["sum"]
                total["max_us"] = max(total["max_us"], duration["max"] or 0)
                total["buckets"] = [
                    a + b for a, b in zip(total["buckets"], duration["log2_buckets"], strict=True)
                ]
                if sample["queue"] is not None:
                    total["latest_queue"] = sample["queue"]
                if sample["value"]["samples"]:
                    total["latest_value"] = sample["value"]["latest"]
    for total in totals.values():
        total["mean_us"] = total["sum_us"] / total["samples"] if total["samples"] else None
        for percentile in [50, 95, 99]:
            threshold = (total["samples"] * percentile + 99) // 100
            count = 0
            total[f"p{percentile}_upper_us"] = None
            for bucket, samples in enumerate(total["buckets"]):
                count += samples
                if threshold > 0 and count >= threshold:
                    total[f"p{percentile}_upper_us"] = (1 << bucket) - 1
                    break
    return {"windows": windows, "loss": loss, "metrics": totals}


def inspect_database(path: Path) -> dict[str, Any]:
    """Read the owned database's actual final authority and fixed fact-profile denominators."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        row = database.execute(
            "SELECT schema, checkpoint_json FROM controller_checkpoint"
        ).fetchone()
        if row is None:
            raise RuntimeError("measured Controller has no committed checkpoint")
        saved = json.loads(row[1])
        missions = json.loads(saved["orchestration_json"])
        counts: dict[str, int] = {}
        for (payload,) in database.execute("SELECT payload_json FROM events ORDER BY sequence"):
            kind = next(iter(json.loads(payload)))
            counts[kind] = counts.get(kind, 0) + 1
    return {
        "schema": row[0],
        "checkpoint_bytes": len(row[1].encode()),
        "checkpoint_sha256": hashlib.sha256(row[1].encode()).hexdigest(),
        "missions": len(missions),
        "completed": sum(mission["lifecycle"] == "Completed" for mission in missions),
        "isolated": sum(mission.get("isolation") is not None for mission in missions),
        "event_counts": counts,
        "database_sha256": digest(path),
    }


def run_probe(
    directory: Path,
    enabled: bool,
    waves: int,
    binaries: dict[str, Path],
    batch_limit: int,
    batch_wait_ms: int,
) -> dict[str, Any]:
    """Run fixed two-session waves and stop only the processes created by this probe."""
    directory.mkdir()
    grpc, http, artifact = ports()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        environment.pop(key, None)
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    environment["ROBOGUIDE_FACT_BATCH_LIMIT"] = str(batch_limit)
    environment["ROBOGUIDE_FACT_BATCH_WAIT_MS"] = str(batch_wait_ms)
    environment["ROBOGUIDE_DIAGNOSTICS_PATH"] = (
        str(directory / "diagnostics.jsonl") if enabled else ""
    )
    database = directory / "controller.sqlite3"
    successes = 0
    failures: list[dict[str, Any]] = []
    durations: list[float] = []
    owned_nodes: list[subprocess.Popen[bytes]] = []
    with (directory / "controller.log").open("wb") as output:
        controller = subprocess.Popen(
            [
                str(binaries["controller"]),
                f"127.0.0.1:{grpc}",
                str(database),
                f"127.0.0.1:{http}",
                f"127.0.0.1:{artifact}",
                str(directory / "artifacts"),
            ],
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            await_ready(controller, http)
            workload_started = time.monotonic()
            for wave in range(waves):
                participants: list[tuple[subprocess.Popen[bytes], int, float]] = []
                for ordinal in range(2):
                    index = wave * 2 + ordinal
                    with (directory / f"node-{index:02}.log").open("wb") as node_log:
                        started = time.monotonic()
                        process = subprocess.Popen(
                            [
                                str(binaries["node"]),
                                "--endpoint",
                                f"http://127.0.0.1:{grpc}",
                                "--node-id",
                                f"pipeline-probe-{index:02}",
                                "--control-endpoint",
                                f"http://127.0.0.1:{http}",
                                "--simulate-execute",
                            ],
                            cwd=ROOT,
                            env=environment,
                            stdout=node_log,
                            stderr=subprocess.STDOUT,
                        )
                        participants.append((process, index, started))
                        owned_nodes.append(process)
                for process, index, started in participants:
                    try:
                        code = process.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait(timeout=5)
                    elapsed = time.monotonic() - started
                    durations.append(elapsed)
                    if code == 0:
                        successes += 1
                    else:
                        failures.append({"node": index, "returncode": code, "elapsed_s": elapsed})
                if controller.poll() is not None:
                    raise RuntimeError("owned Controller failed during the workload")
            elapsed = time.monotonic() - workload_started
            # A complete diagnostic window is needed; exclude this tail from workload time.
            time.sleep(5.2 if enabled else 0.2)
        finally:
            for process in owned_nodes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            controller.terminate()
            try:
                controller.wait(timeout=5)
            except subprocess.TimeoutExpired:
                controller.kill()
                controller.wait(timeout=5)
    result = {
        "batch_limit": batch_limit,
        "batch_wait_ms": batch_wait_ms,
        "diagnostics_enabled": enabled,
        "waves": waves,
        "concurrency": 2,
        "attempted": waves * 2,
        "successful_smokes": successes,
        "failures": failures,
        "workload_elapsed_s": elapsed,
        "observed_process_durations_s": durations,
        "authority": inspect_database(database),
        "diagnostics": diagnostic_totals(directory / "diagnostics.jsonl"),
        "ports": {"grpc": grpc, "http": http, "artifact": artifact},
    }
    save(directory / "result.json", result)
    return result


def main() -> None:
    """Freeze source/binary identity and preserve each repeated run without overwriting evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--waves", type=int, default=8)
    parser.add_argument("--batch-limits", type=int, nargs="+", default=[1, 8])
    parser.add_argument("--batch-wait-ms", type=int, default=2)
    options = parser.parse_args()
    if not 1 <= options.repeats <= 5 or not 1 <= options.waves <= 16:
        parser.error("repeats must be 1..5 and waves must be 1..16")
    if any(not 1 <= value <= 32 for value in options.batch_limits):
        parser.error("batch limits must be 1..32")
    if not 0 <= options.batch_wait_ms <= 5:
        parser.error("batch wait must be 0..5ms")
    directory = options.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    binaries = {
        "controller": ROOT / "target/debug/integration-server",
        "node": ROOT / "target/debug/real-node-smoke",
    }
    manifest = {
        "schema": "roboguide.controller-pipeline-probe/v0.2",
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "tracked_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=ROOT)
        ).hexdigest(),
        "binary_sha256": {key: digest(path) for key, path in binaries.items()},
        "driver_sha256": digest(Path(__file__)),
        "repeats": options.repeats,
        "waves": options.waves,
        "batch_limits": options.batch_limits,
        "batch_wait_ms": options.batch_wait_ms,
        "concurrency": 2,
        "profile": "synthetic-compute-smoke/v0.1",
        "build_profile": "debug",
        "formal_experiment": False,
        "limitations": [
            "not E6",
            "process timing includes OS scheduling",
            "IDs and timestamps vary",
            "diagnostic percentiles are log2 upper bounds",
            "optional bounded coalescing window; two sessions limit opportunity",
        ],
    }
    save(directory / "manifest.json", manifest)
    runs = []
    for repeat in range(options.repeats):
        # Alternate arm order to reduce a fixed first-run bias; inputs and denominator stay fixed.
        limits = options.batch_limits if repeat % 2 == 0 else list(reversed(options.batch_limits))
        for batch_limit in limits:
            for enabled in [False, True] if repeat % 2 == 0 else [True, False]:
                diagnostic_arm = "on" if enabled else "off"
                name = f"repeat-{repeat + 1}-batch-{batch_limit}-diagnostics-{diagnostic_arm}"
                wait_ms = options.batch_wait_ms if batch_limit > 1 else 0
                result = run_probe(
                    directory / name, enabled, options.waves, binaries, batch_limit, wait_ms
                )
                runs.append({"directory": name, **result})
                save(directory / "results.json", runs)
                print(
                    json.dumps(
                        {
                            "run": name,
                            "successful": result["successful_smokes"],
                            "attempted": result["attempted"],
                            "elapsed_s": result["workload_elapsed_s"],
                        }
                    ),
                    flush=True,
                )
    if any(run["failures"] or run["authority"]["completed"] != run["attempted"] for run in runs):
        raise SystemExit(
            "incomplete synthetic runs retained; inspect denominators before comparison"
        )


if __name__ == "__main__":
    main()
