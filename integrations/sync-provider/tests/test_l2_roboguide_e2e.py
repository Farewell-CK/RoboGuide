"""L2 e2e tests wiring real `integration-server` + `roboguide-node` + `roboguide_sync`."""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from helpers import SYNC_PROVIDER_ROOT, WorkerProcess, http_json, ring_neighbors, spawn_sync_worker
from roboguide_sync.schemas import CHOREOGRAPHY_SCHEMA, SYNC_TRACK_SCHEMA

REPO_ROOT = SYNC_PROVIDER_ROOT.parents[1]
TENANT_HEADERS = {"x-roboguide-tenant": "local"}


def _free_tcp_port() -> int:
    """Return an available ephemeral loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _find_roboguide_binaries() -> tuple[Path, Path]:
    """Locate Linux `integration-server` and `roboguide-node` binaries or skip cleanly on Win32."""
    if sys.platform == "win32":
        pytest.skip(
            "RoboGuide Rust controller/node binaries require Linux/WSL2 (std::os::unix::fs)"
        )

    search_dirs: list[Path] = []
    env_dir = os.environ.get("ROBOGUIDE_BIN_DIR", "").strip()
    if env_dir:
        search_dirs.append(Path(env_dir))
    search_dirs.extend(
        [
            REPO_ROOT / "target" / "debug",
            REPO_ROOT / "target" / "release",
            Path("/home/aios/.cache/rg-sync-l2-target/debug"),
        ]
    )
    for d in search_dirs:
        srv = d / "integration-server"
        node = d / "roboguide-node"
        if (
            srv.is_file()
            and os.access(srv, os.X_OK)
            and node.is_file()
            and os.access(node, os.X_OK)
        ):
            return srv, node
    pytest.skip(
        f"integration-server / roboguide-node binaries not built in {[str(p) for p in search_dirs]}"
    )


def _render_node_config(
    tmp_path: Path,
    *,
    node_id: str,
    grpc_port: int,
    sync_http_port: int,
    lock_name: str,
) -> Path:
    """Render a `roboguide.node-config/v0.14` file from `deploy/node.sync-addon.toml`."""
    addon_path = SYNC_PROVIDER_ROOT / "deploy" / "node.sync-addon.toml"
    addon_src = addon_path.read_text(encoding="utf-8")
    addon = (
        addon_src.replace("http://127.0.0.1:18210", f"http://127.0.0.1:{sync_http_port}")
        .replace('object_id = "robot-1"', f'object_id = "{node_id}"')
        .replace("robot-1-sync-chassis-slot", f"{node_id}-sync-chassis-slot")
        .replace('local_locks = ["dog1-pilot-task"]', f'local_locks = ["{lock_name}"]')
    )
    state_dir = tmp_path / "node-state" / node_id
    state_dir.mkdir(parents=True, exist_ok=True)
    header = "\n".join(
        [
            'schema = "roboguide.node-config/v0.14"',
            f'node_id = "{node_id}"',
            f'server_endpoint = "http://127.0.0.1:{grpc_port}"',
            f'state_directory = "{state_dir.as_posix()}"',
            "reconnect_delay_ms = 250",
            "",
        ]
    )
    cfg_path = tmp_path / f"{node_id}.toml"
    cfg_path.write_text(header + addon, encoding="utf-8")
    return cfg_path


class LiveRoboGuideStack:
    """Manages one real `integration-server`, N `roboguide-node`s, and N sync workers."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        srv_bin: Path,
        node_bin: Path,
        robot_specs: list[dict[str, Any]],
    ) -> None:
        """Configure ports and process handles for a temporary L2 cluster."""
        self.tmp_path = tmp_path
        self.srv_bin = srv_bin
        self.node_bin = node_bin
        self.robot_specs = robot_specs

        self.grpc_port = _free_tcp_port()
        self.http_port = _free_tcp_port()
        self.artifact_port = _free_tcp_port()
        self.controller_url = f"http://127.0.0.1:{self.http_port}"

        self.workers: list[WorkerProcess] = []
        self.node_configs: list[Path] = []
        self._node_procs: list[subprocess.Popen[str]] = []
        self._srv_proc: subprocess.Popen[str] | None = None
        self._log_files: list[Any] = []
        self.peer_endpoints: dict[str, str] = {}

    def start(self) -> None:
        """Launch Controller, `roboguide_sync` workers, and Nodes until all are Online."""
        ctrl_db = self.tmp_path / "controller.sqlite3"
        art_dir = self.tmp_path / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)

        registry_path = self.tmp_path / "physical-entity-registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema": "roboguide.physical-entity-registry/v0.1",
                    "registry_id": "l2-sync-test-registry",
                    "revision": 1,
                    "routing_profile": "one-routable-entity-per-node",
                    "entities": [
                        {
                            "entity_id": f"entity-{spec['node_id']}",
                            "node_id": spec["node_id"],
                        }
                        for spec in self.robot_specs
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        srv_log = (self.tmp_path / "integration-server.log").open("w", encoding="utf-8")
        self._log_files.append(srv_log)
        self._srv_proc = subprocess.Popen(
            [
                str(self.srv_bin),
                f"127.0.0.1:{self.grpc_port}",
                str(ctrl_db),
                f"127.0.0.1:{self.http_port}",
                f"127.0.0.1:{self.artifact_port}",
                str(art_dir),
                "",
                str(registry_path),
            ],
            cwd=str(self.tmp_path),
            stdout=srv_log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Wait until controller HTTP API responds
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._srv_proc.poll() is not None:
                srv_log.flush()
                raise RuntimeError(
                    "integration-server exited early:\n"
                    + (self.tmp_path / "integration-server.log").read_text(encoding="utf-8")
                )
            try:
                st, _ = http_json(
                    "GET", f"{self.controller_url}/v1/inventory", headers=TENANT_HEADERS
                )
                if st == 200:
                    break
            except Exception:
                pass
            time.sleep(0.08)
        else:
            raise TimeoutError("integration-server HTTP did not become ready")

        # Start sync-provider workers + roboguide-node daemons
        for spec in self.robot_specs:
            wp = spawn_sync_worker(
                self.tmp_path,
                spec["node_id"],
                x0=float(spec.get("x0", 0.0)),
                y0=float(spec.get("y0", 0.0)),
                yaw0=float(spec.get("yaw0", 0.0)),
                speed_scale=float(spec.get("speed_scale", 1.0)),
                start_delay_s=float(spec.get("start_delay_s", 0.0)),
                fault_at_s=spec.get("fault_at_s"),
                clock_mode="mock",
                mock_clock_offset_ms=float(spec.get("mock_clock_offset_ms", 1.5)),
            )
            self.workers.append(wp)

            cfg = _render_node_config(
                self.tmp_path,
                node_id=spec["node_id"],
                grpc_port=self.grpc_port,
                sync_http_port=wp.http_port,
                lock_name=f"{spec['node_id']}-pilot-task",
            )
            self.node_configs.append(cfg)

            nlog = (self.tmp_path / f"{spec['node_id']}.node.log").open("w", encoding="utf-8")
            self._log_files.append(nlog)
            np = subprocess.Popen(
                [str(self.node_bin), str(cfg)],
                cwd=str(self.tmp_path),
                stdout=nlog,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._node_procs.append(np)

        # Wait for all nodes to register Online and export their UDP endpoints to controller.
        expected_nodes = {spec["node_id"] for spec in self.robot_specs}
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            st_i, inv = http_json(
                "GET", f"{self.controller_url}/v1/inventory", headers=TENANT_HEADERS
            )
            online_ready = set()
            if st_i == 200:
                for n in inv.get("nodes", []):
                    contracts = set(n.get("contracts", []))
                    if (
                        n.get("liveness") == "Reachable"
                        and n.get("reported_health") == "Online"
                        and {"mobility.sync-follow@v1", "choreography.play@v1"} <= contracts
                    ):
                        online_ready.add(n["node_id"])

            st_r, rec_payload = http_json(
                "GET", f"{self.controller_url}/v1/state/records", headers=TENANT_HEADERS
            )
            discovered: dict[str, str] = {}
            if st_r == 200:
                for rec in rec_payload.get("records", []):
                    if (
                        rec.get("channel_id") == "sync-peer-endpoint"
                        and rec.get("object", {}).get("object_type") == "sync-endpoint"
                        and not rec.get("stale")
                    ):
                        nid = str(rec["object"]["object_id"])
                        val = rec.get("value", {}).get("value", {})
                        ep = val.get("peer_endpoint")
                        if nid in expected_nodes and isinstance(ep, str) and ":" in ep:
                            discovered[nid] = ep

            if online_ready == expected_nodes and set(discovered) == expected_nodes:
                self.peer_endpoints = discovered
                return
            time.sleep(0.15)

        raise TimeoutError(
            f"Nodes did not complete registration + sync-endpoint export: "
            f"online_ready={online_ready}, discovered={discovered}, expected={expected_nodes}"
        )

    def ctrl_get(self, path: str) -> tuple[int, Any]:
        """Issue authenticated GET request to the live Controller HTTP API."""
        return http_json(
            "GET", f"{self.controller_url}{path}", headers=TENANT_HEADERS, timeout=10.0
        )

    def ctrl_post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        """Issue authenticated POST request to the live Controller HTTP API."""
        return http_json(
            "POST", f"{self.controller_url}{path}", body, headers=TENANT_HEADERS, timeout=10.0
        )

    def close(self) -> None:
        """Terminate all Node, worker, and Controller subprocesses cleanly."""
        for p in self._node_procs:
            if p.poll() is None:
                p.terminate()
        for p in self._node_procs:
            if p.poll() is None:
                try:
                    p.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2.0)
        for w in self.workers:
            w.stop()
        if self._srv_proc is not None and self._srv_proc.poll() is None:
            self._srv_proc.terminate()
            try:
                self._srv_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._srv_proc.kill()
                self._srv_proc.wait(timeout=2.0)
        for f in self._log_files:
            try:
                f.close()
            except Exception:
                pass


def _build_l2_sync_follow_plan(
    *,
    mission_id: str,
    robot_ids: list[str],
    peer_endpoints: dict[str, str],
    midcut_first_robot: float | None = None,
    sync_params_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a N-robot `roboguide.mission-plan/v0.10` payload for `mobility.sync-follow@v1`."""
    ctx_id = "sync-l2-context"
    n = len(robot_ids)
    relations = [
        {
            "id": f"sync-link-0-{i}",
            "kind": "requires-active",
            "source": {"task_id": f"task-{robot_ids[0]}", "role_id": f"role-{robot_ids[0]}"},
            "target": {"task_id": f"task-{robot_ids[i]}", "role_id": f"role-{robot_ids[i]}"},
        }
        for i in range(1, n)
    ]

    tasks: list[dict[str, Any]] = []
    for i, rid in enumerate(robot_ids):
        y = i * 1.1
        nbr_idxs = sorted({(i - 1) % n, (i + 1) % n} - {i})
        neighbors = [
            {"role_id": robot_ids[j], "endpoint": peer_endpoints[robot_ids[j]], "weight": 1.0}
            for j in nbr_idxs
        ]
        track = {
            "schema_version": SYNC_TRACK_SCHEMA,
            "v_ref": 0.34,
            "v_max": 0.45,
            "group_roles": robot_ids,
            "segments": [
                {"kind": "line", "start": [0.0, y], "end": [0.90, y], "label": "outbound"},
                {
                    "kind": "turn_in_place",
                    "pose": [0.90, y],
                    "start_yaw": 0.0,
                    "delta_yaw": math.pi,
                    "equiv_length_m": 0.50,
                    "barrier_before": True,
                    "label": "turn",
                },
                {
                    "kind": "line",
                    "start": [0.90, y],
                    "end": [0.0, y],
                    "barrier_before": True,
                    "label": "inbound",
                },
            ],
        }
        sp = dict(sync_params_override or {"k_sync": 1.2, "turn_min_wz": 0.90, "done_drain_s": 0.5})
        if i == 0:
            sp.setdefault("done_hold_s", 1.2)
            if midcut_first_robot is not None:
                sp["midcut_gamma"] = midcut_first_robot

        tasks.append(
            {
                "id": f"task-{rid}",
                "description": f"Synchronized march lane y={y:.1f} for {rid}.",
                "context_id": ctx_id,
                "depends_on": [],
                "coupling_mode": "tightly-coupled-cooperation",
                "roles": [
                    {
                        "id": f"role-{rid}",
                        "context_role": rid,
                        "resource_scope": "task",
                        "requirements": {
                            "capabilities": [
                                {
                                    "contract": {
                                        "namespace": "mobility",
                                        "name": "sync-follow",
                                        "version": "v1",
                                    },
                                    "constraints": [],
                                }
                            ],
                            "resources": [{"kind": "space", "units": 1}],
                        },
                        "execution_intent": {
                            "operation": {
                                "namespace": "mobility",
                                "name": "sync-follow",
                                "version": "v1",
                            },
                            "objective": f"Synchronized abreast roundtrip for {rid}.",
                            "parameters": {
                                "context_id": ctx_id,
                                "context_role_id": rid,
                                "track_spec_json": json.dumps(track),
                                "neighbors_json": json.dumps(neighbors),
                                "sync_params_json": json.dumps(sp),
                            },
                        },
                    }
                ],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": 90000,
                },
                "satisfaction": {
                    "expected_effect": f"{rid} completes synchronized trajectory safely.",
                    "basis": "execution-report",
                    "verifier": None,
                },
            }
        )

    return {
        "schema_version": "roboguide.mission-plan/v0.10",
        "mission": {
            "id": mission_id,
            "objective": f"L2 real-controller TightlyCoupledCooperation test ({mission_id}).",
            "actors": [{"id": rid, "physical_entity": f"entity-{rid}"} for rid in robot_ids],
        },
        "contexts": [
            {
                "id": ctx_id,
                "roles": [{"id": rid, "actor": rid} for rid in robot_ids],
                "relations": relations,
                "executor_constraints": (
                    [{"kind": "distinct-physical-entities", "context_roles": list(robot_ids)}]
                    if len(robot_ids) >= 2
                    else []
                ),
                "coupling_mode": "tightly-coupled-cooperation",
                "shared_view": {
                    "bindings": [
                        {"context_role_id": rid, "field": "execution"} for rid in robot_ids
                    ],
                    "include_freshness": True,
                },
                "peer_channel": {
                    "profile_id": "roboguide-udp-sync-20hz-v1",
                    "message_schema": "roboguide.sync-phase/v1",
                },
            }
        ],
        "tasks": tasks,
    }


def _build_l2_choreography_plan(
    *,
    mission_id: str,
    robot_ids: list[str],
    t0_utc_ms: int,
    lane_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Build an `Independent` choreography MissionPlan payload sharing `t0_utc_ms`."""
    ctx_id = "choreo-l2-context"
    tasks: list[dict[str, Any]] = []
    for pos, rid in enumerate(robot_ids):
        i = lane_indices[pos] if lane_indices is not None else pos
        y = i * 1.0
        spec = {
            "schema_version": CHOREOGRAPHY_SCHEMA,
            "v_max": 0.40,
            "fault_policy": "isolate-local-stop",
            "keyframes": [
                {"t_s": 0.0, "x": 0.0, "y": y, "yaw": 0.0, "action": "ready"},
                {"t_s": 1.6, "x": 0.48, "y": y, "yaw": 0.0, "action": "march"},
                {"t_s": 2.4, "x": 0.48, "y": y, "yaw": 0.6, "action": "pose"},
            ],
        }
        tasks.append(
            {
                "id": f"task-{rid}",
                "description": f"Choreography T0 playback lane {i} for {rid}.",
                "context_id": ctx_id,
                "depends_on": [],
                "coupling_mode": "independent",
                "roles": [
                    {
                        "id": f"role-{rid}",
                        "context_role": rid,
                        "resource_scope": "task",
                        "requirements": {
                            "capabilities": [
                                {
                                    "contract": {
                                        "namespace": "choreography",
                                        "name": "play",
                                        "version": "v1",
                                    },
                                    "constraints": [],
                                }
                            ],
                            "resources": [{"kind": "space", "units": 1}],
                        },
                        "execution_intent": {
                            "operation": {
                                "namespace": "choreography",
                                "name": "play",
                                "version": "v1",
                            },
                            "objective": f"Execute synchronized show at T0 for {rid}.",
                            "parameters": {
                                "t0_utc_ms": t0_utc_ms,
                                "max_clock_offset_ms": 20.0,
                                "choreography_spec_json": json.dumps(spec),
                            },
                        },
                    }
                ],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": 60000,
                },
                "satisfaction": {
                    "expected_effect": f"{rid} completes show cleanly.",
                    "basis": "execution-report",
                    "verifier": None,
                },
            }
        )

    return {
        "schema_version": "roboguide.mission-plan/v0.10",
        "mission": {
            "id": mission_id,
            "objective": f"L2 real-controller Independent choreography test ({mission_id}).",
            "actors": [{"id": rid, "physical_entity": f"entity-{rid}"} for rid in robot_ids],
        },
        "contexts": [
            {
                "id": ctx_id,
                "roles": [{"id": rid, "actor": rid} for rid in robot_ids],
                "relations": [],
                "executor_constraints": (
                    [{"kind": "distinct-physical-entities", "context_roles": list(robot_ids)}]
                    if len(robot_ids) >= 2
                    else []
                ),
                "coupling_mode": "independent",
            }
        ],
        "tasks": tasks,
    }


def test_l2_node_cli_validates_sync_addon_and_example_missions(tmp_path: Path) -> None:
    """Validate `node.sync-addon.toml` and example MissionPlans via real `roboguide-node` CLI."""
    _srv_bin, node_bin = _find_roboguide_binaries()
    node_cfgs = [
        _render_node_config(
            tmp_path,
            node_id=f"robot-{i}",
            grpc_port=25051,
            sync_http_port=18210 + i,
            lock_name=f"dog{i}-pilot-task",
        )
        for i in (1, 2, 3)
    ]

    # 1. Exact node.sync-addon.toml conforms to roboguide.node-config/v0.14 serde parser + compiler
    p_cfg = subprocess.run(
        [str(node_bin), "--validate", str(node_cfgs[0])], capture_output=True, text=True
    )
    assert p_cfg.returncode == 0, (
        f"roboguide-node --validate failed:\n{p_cfg.stderr}\n{p_cfg.stdout}"
    )
    compiled = json.loads(p_cfg.stdout)
    assert compiled["node_id"] == "robot-1"

    # 2. Example sync-follow mission passes roboguide-node plan preflight cleanly
    plan_copy = tmp_path / "example-sync-follow-mission.json"
    shutil.copy2(SYNC_PROVIDER_ROOT / "deploy" / "example-sync-follow-mission.json", plan_copy)
    cmd = [
        str(node_bin),
        "--validate",
        str(node_cfgs[0]),
        "--plan",
        str(plan_copy),
        "--node-config",
        str(node_cfgs[1]),
        "--node-config",
        str(node_cfgs[2]),
    ]
    p_plan = subprocess.run(cmd, capture_output=True, text=True)
    assert p_plan.returncode == 0, f"plan preflight failed:\n{p_plan.stderr}\n{p_plan.stdout}"
    assessment = json.loads(p_plan.stdout)
    assert assessment["decision"] == "not_blocked", assessment

    # 3. Example single-performer choreography mission passes roboguide-node plan preflight cleanly
    ch_cfg = _render_node_config(
        tmp_path,
        node_id="robot-01",
        grpc_port=25051,
        sync_http_port=18221,
        lock_name="dog01-pilot-task",
    )
    ch_plan_copy = tmp_path / "example-choreography-mission.json"
    shutil.copy2(SYNC_PROVIDER_ROOT / "deploy" / "example-choreography-mission.json", ch_plan_copy)
    p_ch = subprocess.run(
        [str(node_bin), "--validate", str(ch_cfg), "--plan", str(ch_plan_copy)],
        capture_output=True,
        text=True,
    )
    assert p_ch.returncode == 0, (
        f"choreography plan preflight failed:\n{p_ch.stderr}\n{p_ch.stdout}"
    )
    ch_assess = json.loads(p_ch.stdout)
    assert ch_assess["decision"] == "not_blocked", ch_assess


def test_l2_3robot_tightly_coupled_sync_follow_end_to_end(tmp_path: Path) -> None:
    """Verify full 3-robot TightlyCoupledCooperation Mission roundtrip with real Controller."""
    srv_bin, node_bin = _find_roboguide_binaries()
    robot_ids = ["sim-robot-1", "sim-robot-2", "sim-robot-3"]
    specs = [
        {"node_id": "sim-robot-1", "x0": 0.0, "y0": 0.0, "speed_scale": 1.06, "start_delay_s": 0.0},
        {"node_id": "sim-robot-2", "x0": 0.0, "y0": 1.1, "speed_scale": 0.88, "start_delay_s": 0.5},
        {
            "node_id": "sim-robot-3",
            "x0": 0.0,
            "y0": 2.2,
            "speed_scale": 0.98,
            "start_delay_s": 0.25,
        },
    ]
    stack = LiveRoboGuideStack(tmp_path, srv_bin=srv_bin, node_bin=node_bin, robot_specs=specs)
    stack.start()
    try:
        # Confirm automatic UDP endpoint export discovery through rg-controller
        for w in stack.workers:
            assert stack.peer_endpoints[w.node_id] == w.advertise_endpoint

        mission_id = "l2-sync-3r-roundtrip"
        plan = _build_l2_sync_follow_plan(
            mission_id=mission_id, robot_ids=robot_ids, peer_endpoints=stack.peer_endpoints
        )

        st_v, val = stack.ctrl_post("/v1/missions/validate-plan", plan)
        assert st_v == 200 and val.get("decision") == "not_blocked", val

        st_s, sub = stack.ctrl_post("/v1/missions", plan)
        assert st_s == 202, sub
        group_id = str(sub["group_id"])
        assert group_id == f"group-{mission_id}"

        st_m0, mv0 = stack.ctrl_get(f"/v1/missions/{mission_id}")
        assert st_m0 == 200
        assert mv0["peer_channels"][0]["lifecycle"] == "Planned"

        # Arm shared channel_instance_id on all 3 robots; observer drives Planned -> Ready.
        shared_ch = f"ch:{group_id}:sync-l2-context"
        for i, w in enumerate(stack.workers):
            w.arm_peer_session(
                {
                    "group_id": group_id,
                    "context_id": "sync-l2-context",
                    "context_role_id": w.node_id,
                    "channel_instance_id": shared_ch,
                    "neighbors": ring_neighbors(stack.workers, i),
                }
            )

        seen_lifecycles: list[str] = []
        deadline = time.monotonic() + 35.0
        final_mv: dict[str, Any] = {}
        while time.monotonic() < deadline:
            st_m, mv = stack.ctrl_get(f"/v1/missions/{mission_id}")
            assert st_m == 200, mv
            for pc in mv.get("peer_channels", []):
                lc = pc.get("lifecycle")
                if lc and (not seen_lifecycles or seen_lifecycles[-1] != lc):
                    seen_lifecycles.append(lc)
            final_mv = mv
            if mv.get("status") in ("Completed", "Failed", "Cancelled", "Aborted"):
                break
            time.sleep(0.20)

        assert final_mv.get("status") == "Completed", f"lifecycles={seen_lifecycles}, mv={final_mv}"
        assert "Ready" in seen_lifecycles, f"never observed Ready: {seen_lifecycles}"
        assert all(t["status"] == "Completed" for t in final_mv["tasks"]), final_mv["tasks"]

        # Verify PeerChannels auto-silenced on SUCCEEDED
        deadline_sil = time.monotonic() + 3.0
        while time.monotonic() < deadline_sil:
            if all(w.peer_channels() == [] for w in stack.workers):
                break
            time.sleep(0.05)
        assert all(w.peer_channels() == [] for w in stack.workers)
    finally:
        stack.close()


def test_l2_3robot_midcut_triggers_controller_peer_channel_fenced_and_safe_state(
    tmp_path: Path,
) -> None:
    """Verify mid-run fault drives Controller PeerChannel Fenced and exports safe_state."""
    srv_bin, node_bin = _find_roboguide_binaries()
    robot_ids = ["cut-r1", "cut-r2", "cut-r3"]
    specs = [
        {"node_id": "cut-r1", "x0": 0.0, "y0": 0.0},
        {"node_id": "cut-r2", "x0": 0.0, "y0": 1.1},
        {"node_id": "cut-r3", "x0": 0.0, "y0": 2.2},
    ]
    stack = LiveRoboGuideStack(tmp_path, srv_bin=srv_bin, node_bin=node_bin, robot_specs=specs)
    stack.start()
    try:
        mission_id = "l2-sync-3r-midcut"
        plan = _build_l2_sync_follow_plan(
            mission_id=mission_id,
            robot_ids=robot_ids,
            peer_endpoints=stack.peer_endpoints,
            midcut_first_robot=0.35,
            sync_params_override={
                "k_sync": 1.0,
                "tier1_hold_ms": 200.0,
                "tier2_timeout_ms": 800.0,
                "lead_wait_m": 0.25,
                "turn_min_wz": 0.90,
            },
        )
        st_s, sub = stack.ctrl_post("/v1/missions", plan)
        assert st_s == 202, sub
        group_id = str(sub["group_id"])
        shared_ch = f"ch:{group_id}:sync-l2-context"

        for i, w in enumerate(stack.workers):
            w.arm_peer_session(
                {
                    "group_id": group_id,
                    "context_id": "sync-l2-context",
                    "context_role_id": w.node_id,
                    "channel_instance_id": shared_ch,
                    "neighbors": ring_neighbors(stack.workers, i),
                }
            )

        seen_lifecycles: list[str] = []
        deadline = time.monotonic() + 30.0
        final_mv: dict[str, Any] = {}
        while time.monotonic() < deadline:
            st_m, mv = stack.ctrl_get(f"/v1/missions/{mission_id}")
            assert st_m == 200, mv
            for pc in mv.get("peer_channels", []):
                lc = pc.get("lifecycle")
                if lc and (not seen_lifecycles or seen_lifecycles[-1] != lc):
                    seen_lifecycles.append(lc)
            final_mv = mv
            if mv.get("status") in ("Failed", "Completed", "Cancelled", "Aborted"):
                break
            time.sleep(0.12)

        assert final_mv.get("status") == "Failed", f"lifecycles={seen_lifecycles}, mv={final_mv}"
        assert "Ready" in seen_lifecycles, f"never reached Ready: {seen_lifecycles}"
        assert "Fenced" in seen_lifecycles, (
            f"PeerChannel did not transition Ready->Fenced: {seen_lifecycles}"
        )
        task_map = {t["task_id"]: t["status"] for t in final_mv["tasks"]}
        assert task_map["task-cut-r1"] == "Failed", task_map

        # Wait for siblings to finish SafetyStop cancel and verify tight cross-section stop.
        xs: list[float] = []
        deadline_stop = time.monotonic() + 5.0
        while time.monotonic() < deadline_stop:
            hs = [w.health()[1] for w in stack.workers]
            if all(h.get("active_handle") is None and h.get("pose") is not None for h in hs):
                xs = [float(h["pose"]["x"]) for h in hs]
                break
            time.sleep(0.10)
        assert len(xs) == 3 and (max(xs) - min(xs)) < 0.25, f"cross-section spread too large: {xs}"

        # Wait for node state_export to publish safe-state observation to rg-controller.
        ss_nodes_seen: set[str] = set()
        cut_r1_failed_ss = False
        deadline_ss = time.monotonic() + 5.0
        while time.monotonic() < deadline_ss:
            st_r, recs = stack.ctrl_get("/v1/state/records")
            if st_r == 200:
                for r in recs.get("records", []):
                    if r.get("channel_id") == "sync-safe-state":
                        oid = str(r["object"]["object_id"])
                        obs = r.get("value", {}).get("value", {}).get("observations", [])
                        if any(
                            o.get("safe_state") is True
                            and (o.get("terminal_state") == "FAILED" or bool(o.get("command_id")))
                            for o in obs
                        ):
                            ss_nodes_seen.add(oid)
                        if oid == "cut-r1" and any(
                            o.get("safe_state") is True and o.get("terminal_state") == "FAILED"
                            for o in obs
                        ):
                            cut_r1_failed_ss = True
            if ss_nodes_seen == set(robot_ids) and cut_r1_failed_ss:
                break
            time.sleep(0.15)
        assert ss_nodes_seen == set(robot_ids) and cut_r1_failed_ss, (
            f"safe-state exported for {ss_nodes_seen}, cut_r1_failed_ss={cut_r1_failed_ss}"
        )
    finally:
        stack.close()


def test_l2_5robot_independent_choreography_with_fault_isolation(tmp_path: Path) -> None:
    """Verify 5-robot Independent choreography isolates 1 fault while 4 finish cleanly."""
    srv_bin, node_bin = _find_roboguide_binaries()
    robot_ids = [f"sim-choreo-{i}" for i in range(1, 6)]
    specs = [
        {
            "node_id": rid,
            "x0": 0.0,
            "y0": idx * 1.0,
            "fault_at_s": (0.45 if rid == "sim-choreo-3" else None),
            "mock_clock_offset_ms": 2.0,
        }
        for idx, rid in enumerate(robot_ids)
    ]
    stack = LiveRoboGuideStack(tmp_path, srv_bin=srv_bin, node_bin=node_bin, robot_specs=specs)
    stack.start()
    try:
        # Per-robot Independent Missions share identical T0 so one unit's failure never triggers
        # RoboGuide's single-Mission sibling SafetyStop cancel across healthy performers.
        t0_utc_ms = int(time.time() * 1000) + 2500
        mission_by_robot = {rid: f"l2-choreo-5r-{i}" for i, rid in enumerate(robot_ids, start=1)}
        for idx, rid in enumerate(robot_ids):
            mid = mission_by_robot[rid]
            plan = _build_l2_choreography_plan(
                mission_id=mid, robot_ids=[rid], t0_utc_ms=t0_utc_ms, lane_indices=[idx]
            )
            st_v, val = stack.ctrl_post("/v1/missions/validate-plan", plan)
            assert st_v == 200 and val.get("decision") == "not_blocked", val
            st_s, sub = stack.ctrl_post("/v1/missions", plan)
            assert st_s == 202, sub

        deadline = time.monotonic() + 25.0
        mission_states: dict[str, str] = {}
        while time.monotonic() < deadline:
            for rid, mid in mission_by_robot.items():
                st_m, mv = stack.ctrl_get(f"/v1/missions/{mid}")
                assert st_m == 200, mv
                mission_states[rid] = str(mv.get("status", ""))
            if len(mission_states) == 5 and all(
                s in ("Completed", "Failed", "Cancelled", "Aborted")
                for s in mission_states.values()
            ):
                break
            time.sleep(0.20)

        assert mission_states["sim-choreo-3"] == "Failed", mission_states
        for rid in robot_ids:
            if rid != "sim-choreo-3":
                assert mission_states[rid] == "Completed", (
                    f"fault spread to {rid}: {mission_states}"
                )

        # Confirm shared T0 synchronized start across all 5 performers from local SQLite metrics
        import sqlite3

        start_times: list[int] = []
        for rid in robot_ids:
            db = tmp_path / rid / "sync_state.db"
            rows = sqlite3.connect(db).execute("SELECT metrics_json FROM executions").fetchall()
            assert rows, f"no execution row for {rid}"
            metrics = json.loads(rows[0][0])
            start_times.append(int(metrics["actual_start_utc_ms"]))
        assert max(start_times) - min(start_times) < 80, f"T0 start spread={start_times}"

        # Verify controller received safe-state evidence from the faulted robot
        ss_ok = False
        deadline_ss = time.monotonic() + 5.0
        while time.monotonic() < deadline_ss:
            st_r, recs = stack.ctrl_get("/v1/state/records")
            if st_r == 200:
                for r in recs.get("records", []):
                    if (
                        r.get("channel_id") == "sync-safe-state"
                        and r.get("object", {}).get("object_id") == "sim-choreo-3"
                    ):
                        obs = r.get("value", {}).get("value", {}).get("observations", [])
                        if any(
                            o.get("safe_state") is True and o.get("terminal_state") == "FAILED"
                            for o in obs
                        ):
                            ss_ok = True
                            break
            if ss_ok:
                break
            time.sleep(0.15)
        assert ss_ok is True, "controller did not observe safe_state=true for faulted sim-choreo-3"
    finally:
        stack.close()
