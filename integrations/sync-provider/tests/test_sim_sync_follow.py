"""L1 multi-process simulation tests for `mobility.sync-follow@v1` (N=2, 5, 10, midcut, cancel)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    WorkerProcess,
    chain_neighbors,
    ring_neighbors,
    spawn_sync_worker,
    wait_all_terminal,
)
from roboguide_sync.schemas import OP_SYNC_FOLLOW, SYNC_TRACK_SCHEMA


def _sync_exec_payload(
    *,
    attempt_id: str,
    group_id: str,
    role_id: str,
    track: dict[str, Any],
    neighbors: list[dict[str, Any]],
    sync_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a test `mobility.sync-follow@v1` HTTP invocation payload."""
    params = {
        "context_id": f"ctx-{group_id}",
        "context_role_id": role_id,
        "track_spec_json": json.dumps(track),
        "neighbors_json": json.dumps(neighbors),
    }
    if sync_params is not None:
        params["sync_params_json"] = json.dumps(sync_params)
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
                "operation": OP_SYNC_FOLLOW,
                "objective": "execute synchronized trajectory",
                "parameters": params,
            },
        },
    }


def _arm_peers(
    workers: list[WorkerProcess],
    group_id: str,
    topo_fn: Callable[[list[WorkerProcess], int], list[dict[str, Any]]],
) -> None:
    """Arm PeerChannel sessions across all test workers using `topo_fn`."""
    for i, w in enumerate(workers):
        w.arm_peer_session(
            {
                "group_id": group_id,
                "context_id": f"ctx-{group_id}",
                "context_role_id": w.node_id,
                "channel_instance_id": f"ch-{group_id}-{w.node_id}",
                "neighbors": topo_fn(workers, i),
            }
        )


def test_2robot_abreast_roundtrip_with_skew_and_turn_barrier(tmp_path: Path) -> None:
    """Verify 2-robot roundtrip with startup skew, speed bias, and barrier turn."""
    workers = [
        spawn_sync_worker(
            tmp_path, "r1", x0=0.0, y0=0.0, yaw0=0.0, speed_scale=1.08, start_delay_s=0.0
        ),
        spawn_sync_worker(
            tmp_path, "r2", x0=0.0, y0=1.1, yaw0=0.0, speed_scale=0.86, start_delay_s=0.6
        ),
    ]
    try:
        group_id = "g-2robot-rt"
        _arm_peers(workers, group_id, ring_neighbors)
        roles = ["r1", "r2"]
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            y = i * 1.1
            track = {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.34,
                "v_max": 0.45,
                "group_roles": roles,
                "segments": [
                    {"kind": "line", "start": [0.0, y], "end": [0.85, y], "label": "outbound"},
                    {
                        "kind": "turn_in_place",
                        "pose": [0.85, y],
                        "start_yaw": 0.0,
                        "delta_yaw": math.pi,
                        "equiv_length_m": 0.50,
                        "barrier_before": True,
                        "label": "turn",
                    },
                    {
                        "kind": "line",
                        "start": [0.85, y],
                        "end": [0.0, y],
                        "barrier_before": True,
                        "label": "inbound",
                    },
                ],
            }
            st, resp = w.submit_execution(
                _sync_exec_payload(
                    attempt_id=f"att-2r-{w.node_id}",
                    group_id=group_id,
                    role_id=w.node_id,
                    track=track,
                    neighbors=ring_neighbors(workers, i),
                    sync_params={"k_sync": 1.2, "turn_min_wz": 0.90, "done_drain_s": 0.5},
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        results = wait_all_terminal(handles, timeout_s=30.0)
        for nid, res in results.items():
            assert res["state"] == "SUCCEEDED", f"{nid}: {res}"
            fp = res["metrics"]["final_pose"]
            assert fp is not None and abs(fp["x"]) < 0.20, f"{nid} pose={fp}"
            assert res["metrics"]["rms_dgamma"] < 0.08

        # TURN barrier release timestamps stay tightly synchronized across robots
        turn_ts = [
            results[r]["metrics"]["barrier_timestamps_ms"].get("release_SEG_1_turn_wall_ms", 0)
            for r in roles
        ]
        assert all(turn_ts) and max(turn_ts) - min(turn_ts) < 400

        # PeerChannels auto-silenced upon SUCCEEDED
        assert all(w.peer_channels() == [] for w in workers)
    finally:
        for w in workers:
            w.stop()


@pytest.mark.parametrize(("n_robots", "topo_name"), [(5, "ring"), (10, "chain")])
def test_multi_robot_sparse_consensus_completion(
    tmp_path: Path, n_robots: int, topo_name: str
) -> None:
    """Verify 5-ring and 10-chain fleets reach synchronized completion."""
    topo_fn = ring_neighbors if topo_name == "ring" else chain_neighbors
    workers = [
        spawn_sync_worker(
            tmp_path,
            f"robot-{i:02d}",
            x0=0.0,
            y0=i * 1.1,
            yaw0=0.0,
            speed_scale=0.88 + (i % 5) * 0.05,
            start_delay_s=(i % 4) * 0.18,
        )
        for i in range(n_robots)
    ]
    try:
        group_id = f"g-{n_robots}-{topo_name}"
        _arm_peers(workers, group_id, topo_fn)
        roles = [w.node_id for w in workers]
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            y = i * 1.1
            track = {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.35,
                "v_max": 0.48,
                "group_roles": roles,
                "segments": [
                    {"kind": "line", "start": [0.0, y], "end": [1.3, y], "label": "march"}
                ],
            }
            st, resp = w.submit_execution(
                _sync_exec_payload(
                    attempt_id=f"att-{n_robots}-{w.node_id}",
                    group_id=group_id,
                    role_id=w.node_id,
                    track=track,
                    neighbors=topo_fn(workers, i),
                    sync_params={"k_sync": 1.1, "barrier_timeout_s": 25.0, "done_drain_s": 0.5},
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        results = wait_all_terminal(handles, timeout_s=35.0)
        for nid, res in results.items():
            assert res["state"] == "SUCCEEDED", f"{nid}: {res}"
            fp = res["metrics"]["final_pose"]
            assert fp is not None and fp["x"] >= 1.15, f"{nid} pose={fp}"
            msep = res["metrics"]["min_sep_m"]
            assert msep is None or msep >= 0.75

        assert all(w.peer_channels() == [] for w in workers)
    finally:
        for w in workers:
            w.stop()


def test_5robot_ring_midcut_freezes_group_at_same_cross_section(tmp_path: Path) -> None:
    """Verify a mid-run link cut freezes all 5 ring robots at a tight cross-section."""
    workers = [
        spawn_sync_worker(tmp_path, f"cut-{i}", x0=0.0, y0=i * 1.1, yaw0=0.0) for i in range(5)
    ]
    try:
        group_id = "g-5-midcut"
        _arm_peers(workers, group_id, ring_neighbors)
        roles = [w.node_id for w in workers]
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            y = i * 1.1
            track = {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.32,
                "v_max": 0.44,
                "group_roles": roles,
                "segments": [{"kind": "line", "start": [0.0, y], "end": [2.2, y], "label": "long"}],
            }
            sp: dict[str, object] = {
                "k_sync": 1.0,
                "tier1_hold_ms": 180.0,
                "tier2_timeout_ms": 550.0,
                "lead_wait_m": 0.25,
            }
            if i == 0:
                sp["midcut_gamma"] = 0.35
            st, resp = w.submit_execution(
                _sync_exec_payload(
                    attempt_id=f"att-cut-{w.node_id}",
                    group_id=group_id,
                    role_id=w.node_id,
                    track=track,
                    neighbors=ring_neighbors(workers, i),
                    sync_params=sp,
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        results = wait_all_terminal(handles, timeout_s=25.0)
        for nid, res in results.items():
            assert res["state"] == "FAILED", f"{nid}: {res}"

        cut_ms = results["cut-0"]["metrics"]["cut_wall_ms"]
        assert cut_ms is not None
        gammas = [results[w.node_id]["metrics"]["final_gamma"] for w in workers]
        assert max(gammas) - min(gammas) < 0.15, f"cross-section spread too wide: {gammas}"

        # Remaining 4 workers detect fault quickly and flag PeerChannel unready + safe_state.
        for w in workers[1:]:
            t1_ms = results[w.node_id]["metrics"]["first_tier1_hold_wall_ms"]
            estop_ms = results[w.node_id]["metrics"]["estop_wall_ms"]
            assert t1_ms is not None and (t1_ms - cut_ms) < 500, (
                f"{w.node_id} t1 lag={t1_ms - cut_ms}ms"
            )
            assert estop_ms is not None
            chs = w.peer_channels()
            assert len(chs) == 1 and chs[0]["ready"] is False
            ss = w.safe_state()["observations"]
            assert (
                len(ss) == 1 and ss[0]["safe_state"] is True and ss[0]["terminal_state"] == "FAILED"
            )
    finally:
        for w in workers:
            w.stop()


def test_5robot_ring_http_cancel_floods_estop_and_safe_state(tmp_path: Path) -> None:
    """Verify HTTP cancel on one robot floods ESTOP and emits safe-state records."""
    workers = [
        spawn_sync_worker(tmp_path, f"can-{i}", x0=0.0, y0=i * 1.1, yaw0=0.0) for i in range(5)
    ]
    try:
        group_id = "g-5-cancel"
        _arm_peers(workers, group_id, ring_neighbors)
        roles = [w.node_id for w in workers]
        handles: list[tuple[WorkerProcess, str]] = []
        for i, w in enumerate(workers):
            y = i * 1.1
            track = {
                "schema_version": SYNC_TRACK_SCHEMA,
                "v_ref": 0.30,
                "v_max": 0.42,
                "group_roles": roles,
                "segments": [
                    {"kind": "line", "start": [0.0, y], "end": [3.0, y], "label": "march"}
                ],
            }
            st, resp = w.submit_execution(
                _sync_exec_payload(
                    attempt_id=f"att-can-{w.node_id}",
                    group_id=group_id,
                    role_id=w.node_id,
                    track=track,
                    neighbors=ring_neighbors(workers, i),
                    sync_params={"k_sync": 1.0, "tier1_hold_ms": 400.0, "tier2_timeout_ms": 1500.0},
                )
            )
            assert st == 202, resp
            handles.append((w, resp["handle"]))

        # Wait until all 5 are actively RUNNING, then let them march briefly and cancel node 0
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if all(w.execution_status(h)["state"] == "RUNNING" for w, h in handles):
                break
            time.sleep(0.05)
        time.sleep(0.6)

        cancel_req_ms = int(time.time() * 1000)
        workers[0].cancel_execution(handles[0][1], command_id="cmd-group-abort")

        results = wait_all_terminal(handles, timeout_s=15.0)
        assert results["can-0"]["state"] == "CANCELED"
        obs0 = workers[0].safe_state()["observations"]
        assert (
            len(obs0) == 1
            and obs0[0]["command_id"] == "cmd-group-abort"
            and obs0[0]["safe_state"] is True
        )

        for w in workers[1:]:
            res = results[w.node_id]
            assert res["state"] == "FAILED", res
            assert "peer_estop" in res["detail"]
            assert res["metrics"]["estop_wall_ms"] - cancel_req_ms < 250
            obs = w.safe_state()["observations"]
            assert len(obs) == 1 and obs[0]["safe_state"] is True
    finally:
        for w in workers:
            w.stop()
