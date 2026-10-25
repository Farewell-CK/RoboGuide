"""Measure explicit default Controller transports with real sockets and zero registered Nodes."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from check_identity_processes import client, metrics_text, port, request
from check_listener_capacity_processes import http, wait_closed
from check_replica_content_processes import digest, save
from check_state_verifier_processes import ROOT, wait_for


def run(output: Path, binary: Path, original: Path) -> dict[str, Any]:
    """Check actual defaults, excess admission and measured health latency on one owned process."""
    source = json.loads((original / "result.json").read_bytes())
    assert source["passed"] and source["embodied_nodes"] == 0
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = [binary, Path(__file__), original / "operator.json", original / "artifact.json"]
    files.extend(sorted((original / "pki").glob("*.crt")))
    identity = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identity)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment["TOKIO_WORKER_THREADS"] = "1"
    for plane in ("NODE", "OPERATOR", "ARTIFACT"):
        for key, name in (
            ("SERVER_CERT", "server.crt"),
            ("SERVER_KEY", "server.key"),
            ("CLIENT_CA", "ca.crt"),
        ):
            environment[f"ROBOGUIDE_{plane}_MTLS_{key}"] = str(original / "pki" / name)
        if plane != "NODE":
            environment[f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP"] = str(
                original / (plane.lower() + ".json")
            )
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
        {
            "schema": "roboguide.node-identity-registry/v0.1",
            "registry_id": "actual-default-empty-registry",
            "records": {},
        }
    )
    numbers = {name: port() for name in ("node", "operator", "artifact")}
    origins = {name: f"https://127.0.0.1:{number}" for name, number in numbers.items()}
    credentials = client(original / "pki", "admin")
    declaration = {
        "schema": "roboguide.listener-performance-acceptance/v0.1",
        "tokio_workers": 1,
        "explicit_listener_limit_overrides": {},
        "expected_defaults": dict.fromkeys(numbers, 128),
        "sequential_health_requests": 128,
        "parallel_workers": 16,
        "parallel_health_requests": 128,
        "p99_limit_seconds": 2,
        "devices": 0,
        "nodes": 0,
        "models": 0,
        "machine": platform.machine(),
        "kernel": platform.release(),
        "logical_cpus": os.cpu_count(),
        "initial_load_average": os.getloadavg(),
    }
    save(output / "declaration.json", declaration)
    arguments = [
        str(binary),
        f"127.0.0.1:{numbers['node']}",
        str(output / "controller.sqlite3"),
        f"127.0.0.1:{numbers['operator']}",
        f"127.0.0.1:{numbers['artifact']}",
        str(output / "artifacts"),
    ]
    result: dict[str, Any] = {
        "passed": False,
        "declaration": declaration,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    with (output / "controller.log").open("wb") as log:
        process = subprocess.Popen(arguments, env=environment, cwd=ROOT, stdout=log, stderr=log)
    result["actual_pid"] = process.pid
    try:
        wait_for(
            lambda: request(origins["operator"], credentials, "/healthz")[0] == 200,
            "actual default single-worker Controller",
            20,
        )
        metrics = metrics_text(origins["operator"], credentials)
        for name in numbers:
            assert f"roboguide_{name}_connections_limit 128" in metrics
        result["actual_default_capacity"] = {}
        for name, number in numbers.items():
            held = []
            try:
                held = [
                    socket.create_connection(("127.0.0.1", number), timeout=2) for _ in range(128)
                ]
                with socket.create_connection(("127.0.0.1", number), timeout=2) as excess:
                    closed = wait_closed(excess, 1)
                peer = "artifact" if name == "operator" else "operator"
                assert http(origins[peer], credentials, "/healthz")[0] == 200
                result["actual_default_capacity"][name] = {
                    "actual_held_sockets": len(held),
                    "excess_close": closed,
                    "independent_responsive_listener": peer,
                }
            finally:
                for stream in held:
                    stream.close()
        wait_for(
            lambda: request(origins["operator"], credentials, "/healthz")[0] == 200,
            "actual default permits returned",
            5,
        )

        def measured(_index: int) -> dict[str, Any]:
            """Record one real authenticated health round trip without any business mutation."""
            started = time.monotonic()
            status, _body = http(origins["operator"], credentials, "/healthz")
            return {"http_status": status, "elapsed_seconds": time.monotonic() - started}

        sequential = [measured(index) for index in range(128)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as workers:
            parallel = list(workers.map(measured, range(128)))
        result["actual_round_trips"] = {"sequential": sequential, "parallel": parallel}
        result["actual_latency"] = {}
        for name, rows in result["actual_round_trips"].items():
            assert len(rows) == 128 and all(row["http_status"] == 200 for row in rows)
            values = sorted(row["elapsed_seconds"] for row in rows)
            p99 = values[math.ceil(len(values) * 0.99) - 1]
            result["actual_latency"][name] = {
                "successful": len(rows),
                "attempted": len(rows),
                "p99_seconds": p99,
                "maximum_seconds": values[-1],
            }
            assert p99 <= declaration["p99_limit_seconds"]
        (output / "final-metrics.prom").write_text(metrics_text(origins["operator"], credentials))
        assert identity == {str(path): digest(path) for path in files}
        result["passed"] = True
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                result["forced_cleanup"] = True
                result["passed"] = False
        result["actual_wait"] = process.returncode
        result["passed"] = result["passed"] and process.returncode in {0, -signal.SIGINT}
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require an explicit frozen executable and original real TLS-capacity acceptance profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "controller", "configuration_evidence"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    if not run(
        args.output.absolute(),
        args.controller.resolve(strict=True),
        args.configuration_evidence.resolve(strict=True),
    )["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
