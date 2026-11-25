"""L1 multi-process simulation tests for `choreography.play@v1`."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from helpers import WorkerProcess, spawn_sync_worker, wait_all_terminal
from roboguide_sync.schemas import CHOREOGRAPHY_SCHEMA, OP_CHOREOGRAPHY_PLAY, canonical_json


def _choreo_payload(
    *,
    attempt_id: str,
    group_id: str,
    role_id: str,
    y_lane: float,
    t0_utc_ms: int,
    max_clock_offset_ms: float = 20.0,
) -> dict[str, Any]:
    """Build a test `choreography.play@v1` HTTP invocation payload."""
    keyframes = [
        {"t_s": 0.0, "x": 0.0, "y": y_lane, "yaw": 0.0, "action": "stand"},
        {"t_s": 1.6, "x": 0.48, "y": y_lane, "yaw": 0.0, "action": "march"},
        {"t_s": 2.6, "x": 0.48, "y": y_lane, "yaw": 0.8, "action": "salute"},
    ]
    digest = hashlib.sha256(canonical_json(keyframes).encode("utf-8")).hexdigest()
    spec = {
        "schema_version": CHOREOGRAPHY_SCHEMA,
        "pack_id": "test-show-v1",
        "pack_sha256": digest,
        "v_max": 0.40,
        "lidar_stop_m": 0.55,
        "fault_policy": "isolate-local-stop",
        "keyframes": keyframes,
    }
    return {
        "attempt_id": attempt_id,
        "execution_generation": 1,
        "budget_ms": 60000,
        "invocation": {
            "mission_id": group_id,
            "task_id": f"task-{role_id}",
            "group_id": group_id,
            "role_id": role_id,
            "intent": {
                "operation": OP_CHOREOGRAPHY_PLAY,
                "objective": "synchronized T0 formation show",
                "parameters": {
                    "t0_utc_ms": t0_utc_ms,
                    "max_clock_offset_ms": max_clock_offset_ms,
                    "choreography_spec_json": json.dumps(spec),
                },
            },
        },
    }


def test_10robot_choreography_shared_t0_playback(tmp_path: Path) -> None:
    """Verify 10 simulated robots start synchronously at shared `t0_utc_ms`."""
    workers = [
        spawn_sync_worker(
            tmp_path,
            f"choreo-{i:02d}",
            x0=0.0,
            y0=i * 1.0,
            yaw0=0.0,
            clock_mode="mock",
            mock_clock_offset_ms=2.0,
        )
        for i in range(10)
    ]
    try:
        t0_utc_ms = int(time.time() * 1000) + 1200
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            st, resp = w.submit_execution(
                _choreo_payload(
                    attempt_id=f"att-ch10-{w.node_id}",
                    group_id="g-show-10",
                    role_id=w.node_id,
                    y_lane=i * 1.0,
                    t0_utc_ms=t0_utc_ms,
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        results = wait_all_terminal(handles, timeout_s=20.0)
        start_times: list[int] = []
        for nid, res in results.items():
            assert res["state"] == "SUCCEEDED", f"{nid}: {res}"
            m = res["metrics"]
            start_times.append(int(m["actual_start_utc_ms"]))
            assert m["max_track_err_m"] < 0.15, f"{nid} err={m['max_track_err_m']}"

        assert max(start_times) - min(start_times) < 80, (
            f"T0 spread={max(start_times) - min(start_times)}ms"
        )
    finally:
        for w in workers:
            w.stop()


def test_choreography_fail_closed_clock_offset_gate(tmp_path: Path) -> None:
    """Verify choreography rejects execution fail-closed when clock offset exceeds gate."""
    worker = spawn_sync_worker(
        tmp_path,
        "bad-clock",
        x0=0.0,
        y0=0.0,
        yaw0=0.0,
        clock_mode="mock",
        mock_clock_offset_ms=42.0,
    )
    try:
        st_r, ready = worker.readiness("/choreography")
        assert st_r == 200
        assert ready["state"] == "UNAVAILABLE" and ready["ready"] is False

        t0_utc_ms = int(time.time() * 1000) + 800
        st, resp = worker.submit_execution(
            _choreo_payload(
                attempt_id="att-badclk",
                group_id="g-badclk",
                role_id="bad-clock",
                y_lane=0.0,
                t0_utc_ms=t0_utc_ms,
                max_clock_offset_ms=20.0,
            )
        )
        assert st == 202
        results = wait_all_terminal([(worker, resp["handle"])], timeout_s=10.0)
        res = results["bad-clock"]
        assert res["state"] == "FAILED"
        assert "clock_gate_rejected" in res["detail"]

        obs = worker.safe_state()["observations"]
        assert (
            len(obs) == 1 and obs[0]["safe_state"] is True and obs[0]["terminal_state"] == "FAILED"
        )
    finally:
        worker.stop()


def test_10robot_choreography_single_robot_fault_isolation(tmp_path: Path) -> None:
    """Verify a fault in 1 performer halts only itself while 9 peers complete."""
    workers = [
        spawn_sync_worker(
            tmp_path,
            f"iso-{i:02d}",
            x0=0.0,
            y0=i * 1.0,
            yaw0=0.0,
            clock_mode="mock",
            mock_clock_offset_ms=1.5,
            fault_at_s=(0.45 if i == 4 else None),
        )
        for i in range(10)
    ]
    try:
        t0_utc_ms = int(time.time() * 1000) + 1200
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            st, resp = w.submit_execution(
                _choreo_payload(
                    attempt_id=f"att-iso-{w.node_id}",
                    group_id="g-iso-10",
                    role_id=w.node_id,
                    y_lane=i * 1.0,
                    t0_utc_ms=t0_utc_ms,
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        results = wait_all_terminal(handles, timeout_s=20.0)
        fault_res = results["iso-04"]
        assert fault_res["state"] == "FAILED"
        assert "isolated_local_fault" in fault_res["detail"]
        obs4 = workers[4].safe_state()["observations"]
        assert (
            len(obs4) == 1
            and obs4[0]["safe_state"] is True
            and obs4[0]["terminal_state"] == "FAILED"
        )

        for i, w in enumerate(workers):
            if i == 4:
                continue
            assert results[w.node_id]["state"] == "SUCCEEDED", results[w.node_id]
    finally:
        for w in workers:
            w.stop()
