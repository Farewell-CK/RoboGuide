"""Shared HTTP, mock chassis, and subprocess helpers for L0/L1 `roboguide_sync` tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SYNC_PROVIDER_ROOT = Path(__file__).resolve().parents[1]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """Perform a proxy-free loopback HTTP JSON request returning `(status, decoded_json)`."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=req_headers,
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return err.code, payload


@dataclass
class WorkerProcess:
    """Handle to a managed `python -m roboguide_sync` subprocess."""

    node_id: str
    proc: subprocess.Popen[str]
    http_port: int
    udp_port: int
    advertise_endpoint: str

    @property
    def http_url(self) -> str:
        """Return base HTTP URL of the worker."""
        return f"http://127.0.0.1:{self.http_port}"

    def health(self) -> tuple[int, Any]:
        """Query `GET /v1/sync/health`."""
        return http_json("GET", f"{self.http_url}/v1/sync/health")

    def readiness(self, suffix: str = "") -> tuple[int, Any]:
        """Query `GET /v1/sync/readiness` or an operation-specific alias."""
        return http_json("GET", f"{self.http_url}/v1/sync/readiness{suffix}")

    def peer_channels(self) -> list[dict[str, Any]]:
        """Query active PeerChannel list via `GET /v1/peer-channels`."""
        st, body = http_json("GET", f"{self.http_url}/v1/peer-channels")
        assert st == 200, body
        return list(body["channels"])

    def arm_peer_session(self, body: dict[str, Any]) -> dict[str, Any]:
        """Arm a pre-dispatch PeerChannel session over HTTP."""
        st, payload = http_json("POST", f"{self.http_url}/v1/peer-channels/sessions", body)
        assert st == 200 and isinstance(payload, dict), payload
        return dict(payload)

    def submit_execution(self, body: dict[str, Any]) -> tuple[int, Any]:
        """Submit an execution request over HTTP."""
        return http_json("POST", f"{self.http_url}/v1/sync/executions", body)

    def execution_status(self, handle: str) -> dict[str, Any]:
        """Poll execution status for `handle`."""
        st, payload = http_json(
            "POST", f"{self.http_url}/v1/sync/executions/status", {"handle": handle}
        )
        assert st == 200 and isinstance(payload, dict), payload
        return dict(payload)

    def cancel_execution(self, handle: str, *, command_id: str | None = None) -> dict[str, Any]:
        """Cancel an execution for `handle`."""
        req: dict[str, Any] = {"handle": handle}
        if command_id is not None:
            req["command_id"] = command_id
        st, payload = http_json("POST", f"{self.http_url}/v1/sync/executions/cancel", req)
        assert st == 200 and isinstance(payload, dict), payload
        return dict(payload)

    def safe_state(self) -> dict[str, Any]:
        """Fetch confirmed safe-state observations from the worker."""
        st, payload = http_json("GET", f"{self.http_url}/v1/sync/executions/safe-state")
        assert st == 200 and isinstance(payload, dict), payload
        return dict(payload)

    def stop(self) -> None:
        """Terminate worker subprocess cleanly."""
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3.0)


def spawn_sync_worker(
    tmp_path: Path,
    node_id: str,
    *,
    x0: float = 0.0,
    y0: float = 0.0,
    yaw0: float = 0.0,
    speed_scale: float = 1.0,
    start_delay_s: float = 0.0,
    fault_at_s: float | None = None,
    lidar_front_min_m: float = 5.0,
    clock_mode: str = "mock",
    mock_clock_offset_ms: float = 0.0,
    mock_clock_unsynced: bool = False,
) -> WorkerProcess:
    """Spawn one isolated `roboguide_sync` HTTP+UDP worker bound on ephemeral ports."""
    work_dir = tmp_path / node_id
    work_dir.mkdir(parents=True, exist_ok=True)
    ready_file = work_dir / "ready.json"
    db_file = work_dir / "sync_state.db"

    cmd = [
        sys.executable,
        "-m",
        "roboguide_sync",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--udp-bind",
        "127.0.0.1",
        "--udp-port",
        "0",
        "--advertise-ip",
        "127.0.0.1",
        "--node-id",
        node_id,
        "--state-db",
        str(db_file),
        "--ready-file",
        str(ready_file),
        "--adapter",
        "sim",
        "--clock-mode",
        clock_mode,
        "--mock-clock-offset-ms",
        str(mock_clock_offset_ms),
        "--sim-x0",
        str(x0),
        "--sim-y0",
        str(y0),
        "--sim-yaw0",
        str(yaw0),
        "--sim-speed-scale",
        str(speed_scale),
        "--sim-start-delay-s",
        str(start_delay_s),
        "--sim-lidar-front-min-m",
        str(lidar_front_min_m),
        "--log-level",
        "WARNING",
    ]
    if fault_at_s is not None:
        cmd.extend(["--sim-fault-at-s", str(fault_at_s)])
    if mock_clock_unsynced:
        cmd.append("--mock-clock-unsynced")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SYNC_PROVIDER_ROOT)
    proc = subprocess.Popen(
        cmd,
        cwd=str(SYNC_PROVIDER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    deadline = time.monotonic() + 10.0
    info: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=1.0)
            raise RuntimeError(
                f"worker {node_id} exited ({proc.returncode}): out={out!r} err={err!r}"
            )
        if ready_file.exists():
            try:
                info = json.loads(ready_file.read_text(encoding="utf-8"))
                break
            except json.JSONDecodeError:
                pass
        time.sleep(0.04)
    if info is None:
        proc.kill()
        out, err = proc.communicate(timeout=1.0)
        raise TimeoutError(f"worker {node_id} did not write ready_file; stderr={err!r}")

    wp = WorkerProcess(
        node_id=node_id,
        proc=proc,
        http_port=int(info["http_port"]),
        udp_port=int(info["udp_port"]),
        advertise_endpoint=str(info["advertise_endpoint"]),
    )
    while time.monotonic() < deadline:
        try:
            st, body = wp.health()
            if st == 200 and body.get("state") == "ONLINE":
                return wp
        except Exception:
            pass
        time.sleep(0.03)
    wp.stop()
    raise TimeoutError(f"worker {node_id} health check did not become ONLINE")


def ring_neighbors(workers: list[WorkerProcess], index: int) -> list[dict[str, Any]]:
    """Build sparse 2-neighbor ring edges `(i-1, i+1)` (or 1 neighbor when N==2)."""
    n = len(workers)
    idxs = sorted({(index - 1) % n, (index + 1) % n} - {index})
    return [
        {"role_id": workers[j].node_id, "endpoint": workers[j].advertise_endpoint, "weight": 1.0}
        for j in idxs
    ]


def chain_neighbors(workers: list[WorkerProcess], index: int) -> list[dict[str, Any]]:
    """Build sparse line/chain edges `(i-1, i+1)` with boundary degree 1."""
    idxs = [j for j in (index - 1, index + 1) if 0 <= j < len(workers)]
    return [
        {"role_id": workers[j].node_id, "endpoint": workers[j].advertise_endpoint, "weight": 1.0}
        for j in idxs
    ]


def wait_all_terminal(
    targets: list[tuple[WorkerProcess, str]], *, timeout_s: float = 30.0
) -> dict[str, dict[str, Any]]:
    """Poll status until all `(worker, handle)` pairs reach a terminal state."""
    results: dict[str, dict[str, Any]] = {}
    pending = list(targets)
    deadline = time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        next_pending: list[tuple[WorkerProcess, str]] = []
        for wp, handle in pending:
            st = wp.execution_status(handle)
            if st["state"] in ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"):
                results[wp.node_id] = st
            else:
                next_pending.append((wp, handle))
        pending = next_pending
        if pending:
            time.sleep(0.08)
    if pending:
        snap = {wp.node_id: wp.execution_status(h) for wp, h in pending}
        raise TimeoutError(f"executions did not finish within {timeout_s}s: {snap}")
    return results
