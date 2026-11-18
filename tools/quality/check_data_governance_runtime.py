"""Measure isolated Controller restart, two-Node soak and 100-Node software workloads.

Only explicit local binaries and newly created private output directories are used. All
Nodes are real processes using Node Protocol/mTLS; their host observations are synthetic.
No model, robot, SSH, existing deployment process or database is contacted or altered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_identity_processes import openssl, pki

ROOT = Path(__file__).resolve().parents[2]
DAY_SECONDS = 86_400
RSS_LIMIT_BYTES = 512 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CGROUP_MEMORY_BYTES = 4 * 1024 * 1024 * 1024


def validate_cgroup_limits(memory_max: str, swap_max: str, cpu_max: str) -> dict[str, Any]:
    """Require a finite whole-run budget rather than relying on independent child limits."""
    quota, period = cpu_max.split()
    if (
        memory_max == "max"
        or not 0 < int(memory_max) <= MAX_CGROUP_MEMORY_BYTES
        or swap_max != "0"
        or quota == "max"
        or int(period) <= 0
        or not 0 < int(quota) / int(period) <= 2
    ):
        raise ValueError("whole runtime cgroup needs <=4 GiB, zero swap and <=2 CPU cores")
    return {
        "memory_max_bytes": int(memory_max),
        "memory_swap_max_bytes": 0,
        "cpu_quota_cores": int(quota) / int(period),
    }


def cgroup_limits() -> tuple[Path, dict[str, Any]]:
    """Verify the actual owner scope inherited by every fixture and child before launching."""
    rows = [
        line[3:]
        for line in Path("/proc/self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    ]
    if len(rows) != 1:
        raise ValueError("the runtime owner requires a unified cgroup v2 scope")
    base = Path("/sys/fs/cgroup").resolve(strict=True)
    path = (base / rows[0].lstrip("/")).resolve(strict=True)
    path.relative_to(base)
    values = validate_cgroup_limits(
        *(
            (path / name).read_text().strip()
            for name in ("memory.max", "memory.swap.max", "cpu.max")
        )
    )
    values["path"] = str(path)
    values["memory_events"] = {
        line.split()[0]: int(line.split()[1])
        for line in (path / "memory.events").read_text().splitlines()
    }
    return path, values


def check_disk_capacity(
    output: Path, *, minimum_free_bytes: int, maximum_output_bytes: int
) -> dict[str, int]:
    """Stop before the private SQL, logs and archive can fill a shared system filesystem."""
    total = 0
    for directory, _, filenames in os.walk(output):
        for filename in filenames:
            try:
                total += (Path(directory) / filename).stat().st_size
            except FileNotFoundError:
                continue  # SQLite may retire an owned WAL between directory listing and stat.
    free = shutil.disk_usage(output).free
    if free < minimum_free_bytes or total > maximum_output_bytes:
        raise RuntimeError("isolated runtime disk budget exhausted; original evidence retained")
    return {"free_bytes": free, "output_bytes": total}


def fresh_node_receipts(state: dict[str, Any]) -> dict[str, int]:
    """Count exact source/channel identities rather than arbitrary same-name State objects."""
    receipts: dict[str, int] = {}
    now = state.get("observed_at_ms")
    if type(now) is not int or now < 0:
        return receipts
    for item in state.get("records", []):
        identity = item.get("object", {}).get("object_id")
        envelope = item.get("value", {})
        received = envelope.get("received_at_ms")
        ttl = envelope.get("valid_for_ms")
        if (
            not isinstance(identity, str)
            or item.get("object")
            != {"class": "node", "object_type": "host-status", "object_id": identity}
            or item.get("source") != f"node:{identity}/rg-host"
            or item.get("channel_id") != "host-state"
            or item.get("stale") is not False
            or item.get("semantic") != "reported"
            or envelope.get("payload_schema") != "roboguide.host-inspection/v0.1"
            or not envelope.get("source_epoch")
            or type(envelope.get("sequence")) is not int
            or envelope["sequence"] <= 0
            or type(received) is not int
            or type(ttl) is not int
            or not 0 <= now - received < ttl
            or envelope.get("value", {}).get("node_id") != identity
        ):
            continue
        if identity in receipts:
            raise ValueError("ambiguous duplicate original Node observation")
        receipts[identity] = received
    return receipts


def file_digest(path: Path) -> str:
    """Hash exact selected artifact bytes without loading a large executable into memory."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically publish one bounded retained result beside the streaming measurements."""
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def percentile(values: list[float], fraction: float) -> float | None:
    """Use nearest-rank empirical quantiles and return unavailable for an empty sample."""
    if not values:
        return None
    if not 0 < fraction <= 1:
        raise ValueError("quantile must be within (0,1]")
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def proc_sample(pid: int) -> dict[str, Any]:
    """Read actual Linux CPU, RSS and start identity for one owned process."""
    directory = Path("/proc") / str(pid)
    text = (directory / "stat").read_text()
    fields = text[text.rindex(")") + 2 :].split()
    ticks = os.sysconf("SC_CLK_TCK")
    page_size = os.sysconf("SC_PAGE_SIZE")
    status = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in (directory / "status").read_text().splitlines()
        if ":" in line
    }
    high_water = status.get("VmHWM", "").split()
    if len(high_water) != 2 or high_water[1] != "kB":
        raise ValueError("original Controller RSS high-water measurement unavailable")
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "cpu_seconds": (int(fields[11]) + int(fields[12])) / ticks,
        "rss_bytes": int(fields[21]) * page_size,
        "rss_high_water_bytes": int(high_water[0]) * 1024,
        "sample_monotonic_ns": time.monotonic_ns(),
    }


def cpu_percent(previous: dict[str, Any], current: dict[str, Any]) -> float:
    """Measure single-core CPU consumption; fail on PID reuse or a regressed clock."""
    elapsed = (current["sample_monotonic_ns"] - previous["sample_monotonic_ns"]) / 1e9
    cpu = current["cpu_seconds"] - previous["cpu_seconds"]
    if previous["start_ticks"] != current["start_ticks"] or elapsed <= 0 or cpu < 0:
        raise ValueError("owned process identity or monotonic clock changed")
    return float(cpu * 100 / elapsed)


def evaluate_runtime(
    *,
    mode: str,
    elapsed_seconds: float,
    requested_seconds: float,
    warmup_seconds: float,
    peak_rss_bytes: int,
    baseline_rss_bytes: float | None,
    final_rss_bytes: float | None,
    cpu_average_percent: float | None,
    api_p95_ms: float | None,
    api_errors: int,
    node_errors: int,
) -> dict[str, Any]:
    """Apply original user thresholds without treating a short smoke as 24-hour acceptance."""
    full_duration = (
        mode == "soak" and requested_seconds >= DAY_SECONDS and elapsed_seconds >= DAY_SECONDS
    )
    growth = (
        None
        if not baseline_rss_bytes or final_rss_bytes is None
        else final_rss_bytes / baseline_rss_bytes - 1
    )
    conditions: dict[str, bool | None] = {
        "controller_rss_le_512_mib": peak_rss_bytes <= RSS_LIMIT_BYTES,
        "controller_cpu_mean_le_one_core_10_percent": (
            None if cpu_average_percent is None else cpu_average_percent <= 10
        ),
        "control_api_p95_le_200_ms": None if api_p95_ms is None else api_p95_ms <= 200,
        "all_nodes_remained_fresh": node_errors == 0,
        "all_api_observations_succeeded": api_errors == 0,
        "postwarmup_rss_growth_le_5_percent": (
            None
            if not full_duration
            or baseline_rss_bytes is None
            or final_rss_bytes is None
            or baseline_rss_bytes <= 0
            or warmup_seconds <= 0
            else final_rss_bytes <= baseline_rss_bytes * 1.05
        ),
        "actual_24_hour_duration": full_duration if mode == "soak" else None,
    }
    if mode == "stress":
        # The 10% CPU/512 MiB thresholds describe the two-Node workload, not the separate
        # 100-Node load. Retain those measurements without rebranding stress as this gate.
        accepted = api_errors == 0 and node_errors == 0
    elif mode == "soak":
        accepted = full_duration and all(value is True for value in conditions.values())
    else:
        accepted = False
    return {
        "accepted": accepted,
        "criteria": conditions,
        "rss_growth_fraction": growth,
        "complete_24_hour_acceptance": accepted and full_duration,
    }


@dataclass
class OwnedProcess:
    """Retain an exact subprocess and Linux start identity for scoped cleanup."""

    process: subprocess.Popen[bytes]
    name: str
    original: dict[str, Any]

    def stop(self) -> None:
        """Stop only this original process group, escalating only after a bounded wait."""
        if self.process.poll() is not None:
            return
        actual = proc_sample(self.process.pid)
        if (
            actual["start_ticks"] != self.original["start_ticks"]
            or os.getpgid(self.process.pid) != self.process.pid
        ):
            raise RuntimeError("owned child identity changed; cleanup refused")
        os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)


class RuntimeHarness:
    """Own a single private Controller, real software Nodes and bounded observation source."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        """Freeze binaries, workload and host provenance before starting any process."""
        self.args = arguments
        self.output = arguments.output.resolve()
        self.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.cgroup_path, self.cgroup = cgroup_limits()
        self.initial_disk = check_disk_capacity(
            self.output,
            minimum_free_bytes=arguments.minimum_free_mib * 1024 * 1024,
            maximum_output_bytes=arguments.maximum_output_mib * 1024 * 1024,
        )
        self._maintenance_check()
        self.controller_binary = arguments.controller.resolve(strict=True)
        self.node_binary = arguments.node.resolve(strict=True)
        self.count = 100 if arguments.mode == "stress" else 2
        self.ids = [f"software-{index:03d}" for index in range(self.count)]
        self.processes: list[OwnedProcess] = []
        self.controller: OwnedProcess | None = None
        self.fixture: ThreadingHTTPServer | None = None
        self.fixture_thread: threading.Thread | None = None
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.allowed_cpus = sorted(os.sched_getaffinity(0))[: arguments.cpu_cores]
        if not self.allowed_cpus:
            raise ValueError("no CPUs available to the isolated runtime workload")
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "ANTHROPIC_", "AZURE_"))
            and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
        }
        self.environment.update(TOKIO_WORKER_THREADS="2", NO_PROXY="127.0.0.1,localhost")
        self.certificates = self.output / "pki"
        self.digests = pki(self.certificates)
        # Existing short gates use one-day credentials. This owned CA and its server/admin
        # leaves must outlive a real 24-hour soak rather than failing at the expiry boundary.
        openssl(
            self.certificates,
            "req",
            "-x509",
            "-key",
            "ca.key",
            "-out",
            "ca.crt",
            "-days",
            "3",
            "-subj",
            "/CN=offline-runtime-ca",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
        for leaf in ("server", "admin"):
            openssl(
                self.certificates,
                "x509",
                "-req",
                "-in",
                leaf + ".csr",
                "-CA",
                "ca.crt",
                "-CAkey",
                "ca.key",
                "-CAcreateserial",
                "-out",
                leaf + ".crt",
                "-days",
                "3",
                "-extfile",
                "extensions",
            )
            der = ssl.PEM_cert_to_DER_cert((self.certificates / (leaf + ".crt")).read_text())
            self.digests[leaf] = hashlib.sha256(der).hexdigest()
        self.node_digests = {identity: self._leaf(identity) for identity in self.ids}
        self.admin = ssl.create_default_context(cafile=str(self.certificates / "ca.crt"))
        self.admin.load_cert_chain(self.certificates / "admin.crt", self.certificates / "admin.key")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=self.admin)
        )
        self.grpc, self.http, self.artifact = self._ports()
        self.origin = f"https://127.0.0.1:{self.http}"
        roles = self.output / "operator-roles.json"
        roles.write_text(
            json.dumps(
                {"sha256:" + self.digests["admin"]: {"role": "recovery-admin", "tenant_ids": ["*"]}}
            )
        )
        self.controller_env = {
            **self.environment,
            "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "3",
            "ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS": str(arguments.heartbeat_ms),
            "ROBOGUIDE_NODE_LEASE_DURATION_MS": str(arguments.heartbeat_ms * 5),
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(self.certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(self.certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(self.certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(self.node_digests),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(self.certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(self.certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(self.certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(roles),
        }
        source_paths = [
            Path(__file__),
            ROOT / "tools/quality/check_identity_processes.py",
            ROOT / "integrations/robonix-task/deploy/host-node.fragment.toml",
        ]
        self.sources = {str(path): file_digest(path) for path in source_paths}
        self.binaries = {
            str(path): file_digest(path) for path in (self.controller_binary, self.node_binary)
        }
        self.metadata: dict[str, Any] = {
            "schema_version": "roboguide.data-governance-runtime/v0.1",
            "mode": arguments.mode,
            "pid": os.getpid(),
            "boot_id": self.boot,
            "host": {
                "system": platform.platform(),
                "python": platform.python_version(),
                "cpu_affinity": self.allowed_cpus,
                "available_cpus": len(os.sched_getaffinity(0)),
                "memory_limit_bytes": arguments.address_space_mib * 1024 * 1024,
                "whole_run_cgroup": self.cgroup,
                "initial_disk": self.initial_disk,
                "maintenance_controller_unit": arguments.maintenance_controller_unit,
            },
            "binary_sha256": self.binaries,
            "source_sha256": self.sources,
            "workload": {
                "real_node_processes": self.count,
                "heartbeat_ms": arguments.heartbeat_ms,
                "lease_ms": arguments.heartbeat_ms * 5,
                "state_interval_ms": arguments.state_interval_ms,
                "state_ttl_ms": arguments.state_interval_ms * 5,
                "repeated_payload_bytes": arguments.payload_bytes,
                "state_semantic": "reported",
                "synthetic_source": True,
                "model_inference": False,
                "hardware_contacted": False,
                "history_seed": "fresh-private-sql",
                "real_mission_history_preservation_tested": False,
            },
            "duration_seconds": arguments.duration_seconds,
            "warmup_seconds": arguments.warmup_seconds,
            "status": "prepared",
            "started_unix_ms": time.time_ns() // 1_000_000,
        }
        write_json(self.output / "run.json", self.metadata)

    def _maintenance_check(self) -> None:
        """Refuse a benchmark while a declared production Controller unit is still active."""
        unit = self.args.maintenance_controller_unit
        if unit is None:
            return
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", unit):
            raise ValueError("maintenance Controller must be an explicit systemd service unit")
        result = subprocess.run(
            ["systemctl", "--user", "show", "--property=ActiveState", "--value", unit],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.stdout.strip() not in {"inactive", "failed"}:
            raise RuntimeError("declared production Controller is active; benchmark refused")

    def _guard(self) -> dict[str, Any]:
        """Recheck disk, original whole-run scope and production exclusion during the run."""
        self._maintenance_check()
        path, current = cgroup_limits()
        if path != self.cgroup_path or any(
            current[key] != self.cgroup[key]
            for key in ("memory_max_bytes", "memory_swap_max_bytes", "cpu_quota_cores")
        ):
            raise RuntimeError("original whole-run runtime cgroup limits changed")
        if current["memory_events"].get("oom_kill", 0) > self.cgroup["memory_events"].get(
            "oom_kill", 0
        ):
            raise RuntimeError("runtime cgroup experienced an OOM kill; acceptance refused")
        disk = check_disk_capacity(
            self.output,
            minimum_free_bytes=self.args.emergency_free_mib * 1024 * 1024,
            maximum_output_bytes=self.args.maximum_output_mib * 1024 * 1024,
        )
        cgroup_usage: dict[str, Any] = {
            "memory_current_bytes": int((path / "memory.current").read_text()),
            "memory_events": current["memory_events"],
            "cpu_stat": {
                line.split()[0]: int(line.split()[1])
                for line in (path / "cpu.stat").read_text().splitlines()
            },
        }
        if (path / "memory.peak").is_file():
            cgroup_usage["memory_peak_bytes"] = int((path / "memory.peak").read_text())
        return {**disk, "whole_run_cgroup_usage": cgroup_usage}

    def _ports(self) -> tuple[int, int, int]:
        """Reserve distinct kernel-assigned listeners until all three choices are fixed."""
        sockets = [socket.socket() for _ in range(3)]
        try:
            for listener in sockets:
                listener.bind(("127.0.0.1", 0))
            return tuple(int(listener.getsockname()[1]) for listener in sockets)  # type: ignore[return-value]
        finally:
            for listener in sockets:
                listener.close()

    def _leaf(self, identity: str) -> str:
        """Issue a unique offline client leaf for each real software Node identity."""
        openssl(
            self.certificates,
            "req",
            "-new",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            identity + ".key",
            "-out",
            identity + ".csr",
            "-subj",
            "/CN=" + identity,
        )
        openssl(
            self.certificates,
            "x509",
            "-req",
            "-in",
            identity + ".csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            identity + ".crt",
            "-days",
            "3",
            "-extfile",
            "extensions",
        )
        der = ssl.PEM_cert_to_DER_cert((self.certificates / (identity + ".crt")).read_text())
        return hashlib.sha256(der).hexdigest()

    def _launch(
        self, binary: Path, arguments: list[str], name: str, environment: dict[str, str]
    ) -> OwnedProcess:
        """Apply hard CPU/address-space limits and affinity to this owned process group."""
        cpu_budget = max(180, math.ceil(self.args.duration_seconds * 2 + 300))
        command = [
            "taskset",
            "-c",
            ",".join(map(str, self.allowed_cpus)),
            "prlimit",
            f"--as={self.args.address_space_mib * 1024 * 1024}",
            f"--cpu={cpu_budget}",
            "--",
            str(binary),
            *arguments,
        ]
        with (self.output / (name + ".log")).open("xb") as log:
            process = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdout=log, stderr=log, start_new_session=True
            )
        try:
            owned = OwnedProcess(process, name, proc_sample(process.pid))
        except BaseException:
            # Popen is the original parent handle; no guessed or enumerated PID is signalled.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
            raise
        self.processes.append(owned)
        self._pid_manifest()
        return owned

    def _pid_manifest(self) -> None:
        """Retain original PID/start identities for an inspectable bounded runtime handoff."""
        write_json(
            self.output / "owned-processes.json",
            {
                "owner_pid": os.getpid(),
                "boot_id": self.boot,
                "processes": [
                    {
                        "name": item.name,
                        "pid": item.process.pid,
                        "start_ticks": item.original["start_ticks"],
                        "returncode": item.process.poll(),
                    }
                    for item in self.processes
                ],
            },
        )

    def _json(self, path: str) -> tuple[dict[str, Any], float]:
        """Measure one fresh proxy-disabled authenticated Controller read without retry."""
        request = urllib.request.Request(self.origin + path, headers={"Accept": "application/json"})
        started = time.monotonic_ns()
        with self.opener.open(request, timeout=self.args.api_timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Controller observation unavailable or exceeds its response bound")
        elapsed = (time.monotonic_ns() - started) / 1e6
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Controller JSON observation is not an object")
        return value, elapsed

    def _alive(self) -> None:
        """Fail immediately when a required owned process exited, preserving its original log."""
        exited = [
            item.name
            for item in self.processes
            if item.process.poll() is not None and not item.name.startswith("controller-")
        ]
        if self.controller is not None and self.controller.process.poll() is not None:
            exited.append(self.controller.name)
        if exited:
            raise RuntimeError("required owned process exited: " + ",".join(exited))

    def start_controller(self, index: int) -> dict[str, Any]:
        """Measure real process-start to readiness, separating the first migration/warm start."""
        self._guard()
        started = time.monotonic_ns()
        self.controller = self._launch(
            self.controller_binary,
            [
                f"127.0.0.1:{self.grpc}",
                str(self.output / "controller.sqlite3"),
                f"127.0.0.1:{self.http}",
                f"127.0.0.1:{self.artifact}",
                str(self.output / "artifacts"),
            ],
            f"controller-{index:02d}",
            self.controller_env,
        )
        deadline = time.monotonic() + 60
        failures = 0
        while time.monotonic() < deadline:
            self._alive()
            try:
                document, _ = self._json("/readyz")
                if document.get("ready") is True:
                    return {
                        "restart_index": index,
                        "readiness_seconds": (time.monotonic_ns() - started) / 1e9,
                        "transient_probes": failures,
                        "pid": self.controller.process.pid,
                        "sample": proc_sample(self.controller.process.pid),
                    }
            except (OSError, ValueError):
                failures += 1
            time.sleep(0.01)
        raise TimeoutError("owned Controller readiness exceeded 60-second observation bound")

    def start_nodes(self) -> None:
        """Start independent Nodes with repeated payload and original freshness rules."""
        payload = "r" * self.args.payload_bytes
        ids = set(self.ids)

        class Source(BaseHTTPRequestHandler):
            """Expose only synthetic bounded host observation GETs; there is no action route."""

            def log_message(self, _format: str, *_args: Any) -> None:
                """Suppress repetitive fixture logs while actual State remains in Controller SQL."""

            def do_GET(self) -> None:
                """Return stable content through each original independent source acquisition."""
                identity = self.path.removeprefix("/host/")
                if identity not in ids:
                    self.send_error(404)
                    return
                raw = json.dumps(
                    {
                        "node_id": identity,
                        "health_state": "ONLINE",
                        "readiness_state": "READY",
                        "execution_state": "SUCCEEDED",
                        "detail": "synthetic readonly workload",
                        "stable_payload": payload,
                    },
                    separators=(",", ":"),
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        class SourceServer(ThreadingHTTPServer):
            """Accept hundred-Node sampling bursts without the default five-connection backlog."""

            request_queue_size = 256
            daemon_threads = True

        self.fixture = SourceServer(("127.0.0.1", 0), Source)
        self.fixture_thread = threading.Thread(
            target=self.fixture.serve_forever, name="software-state-source", daemon=True
        )
        self.fixture_thread.start()
        fragment = (ROOT / "integrations/robonix-task/deploy/host-node.fragment.toml").read_text()
        for identity in self.ids:
            self._guard()
            text = (
                fragment.replace("NODE_ID", identity)
                .replace("127.0.0.1:18112", f"127.0.0.1:{self.fixture.server_port}")
                .replace("/v1/host/status", "/host/" + identity)
            )
            text = text.replace(
                "interval_ms = 2000", f"interval_ms = {self.args.state_interval_ms}"
            ).replace("valid_for_ms = 10000", f"valid_for_ms = {self.args.state_interval_ms * 5}")
            path = self.output / (identity + ".toml")
            path.write_text(
                'schema="roboguide.node-config/v0.14"\n'
                + f'node_id="{identity}"\nserver_endpoint="https://127.0.0.1:{self.grpc}"\n'
                + f"state_directory={json.dumps(str(self.output / (identity + '-state')))}\n"
                + "reconnect_delay_ms=100\n"
                + text
            )
            env = {
                **self.environment,
                "TOKIO_WORKER_THREADS": "1",
                "ROBOGUIDE_NODE_MTLS_CLIENT_CERT": str(self.certificates / (identity + ".crt")),
                "ROBOGUIDE_NODE_MTLS_CLIENT_KEY": str(self.certificates / (identity + ".key")),
                "ROBOGUIDE_NODE_MTLS_SERVER_CA": str(self.certificates / "ca.crt"),
                "ROBOGUIDE_NODE_MTLS_DOMAIN": "localhost",
            }
            self._launch(self.node_binary, [str(path)], identity, env)
            self.metadata.setdefault("node_configuration_sha256", {})[identity] = file_digest(path)
        deadline = time.monotonic() + max(30, self.count)
        while time.monotonic() < deadline:
            self._alive()
            if self.node_health()["all_fresh"]:
                write_json(self.output / "run.json", self.metadata)
                return
            time.sleep(0.1)
        raise TimeoutError("not all actual Node processes enrolled and published fresh State")

    def node_health(self) -> dict[str, Any]:
        """Verify actual inventory and exact fresh reported State from every real Node process."""
        inventory, inventory_ms = self._json("/v1/inventory")
        state, state_ms = self._json("/v1/state/records?object_class=node&object_type=host-status")
        reachable = {
            item.get("node_id")
            for item in inventory.get("nodes", [])
            if item.get("liveness") == "Reachable"
        }
        receipts = fresh_node_receipts(state)
        fresh = set(receipts)
        return {
            "reachable": len(reachable),
            "fresh": len(fresh),
            "all_fresh": set(self.ids) <= reachable and set(self.ids) <= fresh,
            "inventory_ms": inventory_ms,
            "state_ms": state_ms,
            "received_at_ms": receipts,
        }

    def startup(self) -> dict[str, Any]:
        """Restart only the Controller thirty times while the same two Nodes and SQL remain."""
        initial = self.start_controller(0)
        self.start_nodes()
        rows = []
        for index in range(1, 31):
            assert self.controller is not None
            previous_receipts = self.node_health()["received_at_ms"]
            self.controller.stop()
            result = self.start_controller(index)
            at_readiness = self.node_health()["received_at_ms"]
            deadline = time.monotonic() + 15
            while True:
                health = self.node_health()
                if health["all_fresh"] and all(
                    health["received_at_ms"].get(identity, 0)
                    > max(previous_receipts.get(identity, 0), at_readiness.get(identity, 0))
                    for identity in self.ids
                ):
                    break
                self._alive()
                if time.monotonic() >= deadline:
                    raise TimeoutError("original Nodes did not reconnect after Controller restart")
                time.sleep(0.05)
            result["node_reconnect_checked"] = True
            rows.append(result)
            with (self.output / "restarts.jsonl").open("a") as stream:
                stream.write(json.dumps(result) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        readiness = percentile([row["readiness_seconds"] for row in rows], 0.95)
        return {
            "mode": "startup",
            "first_initialization_not_in_quantile": initial,
            "normal_restarts": len(rows),
            "readiness_p95_seconds": readiness,
            "accepted": len(rows) == 30 and readiness is not None and readiness <= 3,
            "records": rows,
        }

    def runtime(self) -> dict[str, Any]:
        """Stream actual CPU/RSS/API/freshness observations for the explicit wall-clock duration."""
        initial = self.start_controller(0)
        self.start_nodes()
        assert self.controller is not None
        previous = proc_sample(self.controller.process.pid)
        started = time.monotonic()
        deadline = started + self.args.duration_seconds
        samples = 0
        rss: list[tuple[float, int]] = []
        rss_high_water = int(previous["rss_high_water_bytes"])
        cpu_time = 0.0
        measured_time = 0.0
        api_latencies: list[float] = []
        api_errors = node_errors = 0
        with (self.output / "metrics.jsonl").open("a", buffering=1) as metrics:
            while time.monotonic() < deadline:
                self._alive()
                disk = self._guard()
                before = time.monotonic()
                current = proc_sample(self.controller.process.pid)
                rss_high_water = max(rss_high_water, int(current["rss_high_water_bytes"]))
                cpu = cpu_percent(previous, current)
                delta = (current["sample_monotonic_ns"] - previous["sample_monotonic_ns"]) / 1e9
                cpu_time += current["cpu_seconds"] - previous["cpu_seconds"]
                measured_time += delta
                previous = current
                elapsed = before - started
                rss.append((elapsed, current["rss_bytes"]))
                observation: dict[str, Any] = {
                    "elapsed_seconds": elapsed,
                    "wall_time_ms": time.time_ns() // 1_000_000,
                    "controller": current,
                    "single_core_cpu_percent": cpu,
                    "disk": disk,
                }
                try:
                    health = self.node_health()
                    observation["nodes"] = health
                    api_latencies.extend([health["inventory_ms"], health["state_ms"]])
                    if not health["all_fresh"]:
                        node_errors += 1
                except (OSError, ValueError) as error:
                    api_errors += 1
                    observation["api_error"] = type(error).__name__
                observation["database_bytes"] = sum(
                    path.stat().st_size
                    for path in self.output.glob("controller.sqlite3*")
                    if path.is_file()
                )
                metrics.write(json.dumps(observation, allow_nan=False) + "\n")
                metrics.flush()
                if samples % 12 == 0:
                    os.fsync(metrics.fileno())
                    write_json(
                        self.output / "progress.json",
                        {
                            "status": "running",
                            "elapsed_seconds": elapsed,
                            "requested_seconds": self.args.duration_seconds,
                            "observations": samples + 1,
                            "api_errors": api_errors,
                            "node_errors": node_errors,
                            "latest": observation,
                        },
                    )
                samples += 1
                remaining = min(
                    self.args.sample_seconds - (time.monotonic() - before),
                    deadline - time.monotonic(),
                )
                if remaining > 0:
                    time.sleep(remaining)
        elapsed = time.monotonic() - started
        final_cpu_sample = proc_sample(self.controller.process.pid)
        cpu_time += final_cpu_sample["cpu_seconds"] - previous["cpu_seconds"]
        measured_time += (
            final_cpu_sample["sample_monotonic_ns"] - previous["sample_monotonic_ns"]
        ) / 1e9
        rss_high_water = max(rss_high_water, int(final_cpu_sample["rss_high_water_bytes"]))
        baseline = [
            value
            for stamp, value in rss
            if self.args.warmup_seconds
            <= stamp
            < self.args.warmup_seconds + self.args.baseline_window_seconds
        ]
        final = [
            value
            for stamp, value in rss
            if stamp >= max(self.args.warmup_seconds, elapsed - self.args.baseline_window_seconds)
        ]
        average_cpu = None if measured_time <= 0 else cpu_time * 100 / measured_time
        peak_rss = max(rss_high_water, max((value for _, value in rss), default=0))
        baseline_rss = sum(baseline) / len(baseline) if baseline else None
        final_rss = sum(final) / len(final) if final else None
        api_p95 = percentile(api_latencies, 0.95)
        verdict = evaluate_runtime(
            mode=self.args.mode,
            elapsed_seconds=elapsed,
            requested_seconds=self.args.duration_seconds,
            warmup_seconds=self.args.warmup_seconds,
            peak_rss_bytes=peak_rss,
            baseline_rss_bytes=baseline_rss,
            final_rss_bytes=final_rss,
            cpu_average_percent=average_cpu,
            api_p95_ms=api_p95,
            api_errors=api_errors,
            node_errors=node_errors,
        )
        return {
            "mode": self.args.mode,
            "first_initialization": initial,
            "elapsed_seconds": elapsed,
            "observations": samples,
            "real_node_processes": self.count,
            "controller_peak_rss_bytes": peak_rss,
            "postwarmup_baseline_mean_rss_bytes": baseline_rss,
            "final_window_mean_rss_bytes": final_rss,
            "controller_cpu_mean_one_core_percent": average_cpu,
            "control_api_p95_ms": api_p95,
            "api_errors": api_errors,
            "node_errors": node_errors,
            **verdict,
        }

    def close(self) -> None:
        """Stop owned groups in reverse order and preserve all evidence on failure."""
        failures = []
        for process in reversed(self.processes):
            try:
                process.stop()
            except (OSError, RuntimeError) as error:
                failures.append(f"{process.name}:{type(error).__name__}")
        if self.fixture is not None:
            self.fixture.shutdown()
            self.fixture.server_close()
        if self.fixture_thread is not None:
            self.fixture_thread.join(timeout=5)
        self._pid_manifest()
        if failures:
            raise RuntimeError("owned process cleanup incomplete: " + ",".join(failures))

    def run(self) -> dict[str, Any]:
        """Preserve actual result and source identities regardless of acceptance outcome."""
        self.metadata["status"] = "running"
        write_json(self.output / "run.json", self.metadata)
        try:
            result = self.startup() if self.args.mode == "startup" else self.runtime()
            result["final_resource_guard"] = self._guard()
            if self.binaries != {
                path: file_digest(Path(path)) for path in self.binaries
            } or self.sources != {path: file_digest(Path(path)) for path in self.sources}:
                raise RuntimeError("frozen source or executable changed during measurement")
            result.update(
                schema_version="roboguide.data-governance-runtime-result/v0.1",
                binary_sha256=self.binaries,
                source_sha256=self.sources,
                hardware_contacted=False,
            )
            write_json(self.output / "result.json", result)
            self.metadata["status"] = "completed"
            return result
        except BaseException as error:
            self.metadata.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            self.close()
            self.metadata["finished_unix_ms"] = time.time_ns() // 1_000_000
            write_json(self.output / "run.json", self.metadata)


def parser() -> argparse.ArgumentParser:
    """Require explicit binaries and retain the original fixed acceptance thresholds."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("startup", "soak", "stress"))
    result.add_argument("--controller", type=Path, required=True)
    result.add_argument("--node", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--duration-seconds", type=float)
    result.add_argument("--warmup-seconds", type=float, default=3600)
    result.add_argument("--baseline-window-seconds", type=float, default=300)
    result.add_argument("--sample-seconds", type=float, default=5)
    result.add_argument("--heartbeat-ms", type=int, default=1000)
    result.add_argument("--state-interval-ms", type=int, default=2000)
    result.add_argument("--payload-bytes", type=int, default=4096)
    result.add_argument("--api-timeout-seconds", type=float, default=2)
    result.add_argument("--address-space-mib", type=int, default=2048)
    result.add_argument("--cpu-cores", type=int, default=2)
    result.add_argument("--minimum-free-mib", type=int, default=2048)
    result.add_argument("--emergency-free-mib", type=int, default=512)
    result.add_argument("--maximum-output-mib", type=int, default=2048)
    result.add_argument("--maintenance-controller-unit")
    return result


def main() -> None:
    """Run only on explicit Linux private roots and return failure when acceptance is incomplete."""
    arguments = parser().parse_args()
    if platform.system() != "Linux":
        raise SystemExit("run this isolated /proc workload inside Linux/WSL")
    if arguments.duration_seconds is None:
        arguments.duration_seconds = DAY_SECONDS if arguments.mode == "soak" else 300
    numeric = [
        arguments.duration_seconds,
        arguments.warmup_seconds,
        arguments.baseline_window_seconds,
        arguments.sample_seconds,
        arguments.api_timeout_seconds,
    ]
    if (
        any(not math.isfinite(value) for value in numeric)
        or arguments.duration_seconds <= 0
        or arguments.warmup_seconds < 0
        or not 0 < arguments.baseline_window_seconds
        or not 0.05 <= arguments.sample_seconds <= 60
        or not 0 < arguments.api_timeout_seconds <= 5
    ):
        raise SystemExit("invalid explicit workload timing")
    if (
        not 100 <= arguments.heartbeat_ms <= 10000
        or not 100 <= arguments.state_interval_ms <= 10000
        or not 1 <= arguments.payload_bytes <= 65536
        or not 512 <= arguments.address_space_mib <= 4096
        or not 1 <= arguments.cpu_cores <= 4
        or not 512 <= arguments.minimum_free_mib <= 16384
        or not 256 <= arguments.emergency_free_mib <= arguments.minimum_free_mib
        or not 512 <= arguments.maximum_output_mib <= 4096
        or (100 if arguments.mode == "stress" else 2) * (arguments.payload_bytes + 4096)
        > MAX_RESPONSE_BYTES
    ):
        raise SystemExit("workload exceeds the isolated sampling/resource bounds")

    def interrupted(_number: int, _frame: Any) -> None:
        """Let the owner release only its exact groups when the operator stops this run."""
        raise KeyboardInterrupt("runtime harness interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    result = RuntimeHarness(arguments).run()
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"records", "source_sha256", "binary_sha256"}
            },
            allow_nan=False,
        )
    )
    if not result["accepted"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
