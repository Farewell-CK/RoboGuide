"""L0 tests for HTTP workflow routes, SQLite recovery, PeerChannels, and deploy templates."""

from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from helpers import SYNC_PROVIDER_ROOT, http_json
from roboguide_sync.adapters import SimAdapter
from roboguide_sync.clock_sync import FixedClockProbe
from roboguide_sync.http_server import SyncService
from roboguide_sync.journal import SyncJournal
from roboguide_sync.schemas import (
    CHOREOGRAPHY_SCHEMA,
    OP_CHOREOGRAPHY_PLAY,
    OP_SYNC_FOLLOW,
    SAFE_STATE_OBSERVATION_SCHEMA,
    SYNC_ENDPOINT_SCHEMA,
    SYNC_TRACK_SCHEMA,
    parse_choreography_spec,
    parse_neighbors,
    parse_track_spec,
)
from roboguide_sync.transport import UdpHub

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _sample_sync_request(attempt_id: str, *, end_x: float = 1.0) -> dict[str, Any]:
    """Build a sample `mobility.sync-follow@v1` invocation payload."""
    track = {
        "schema_version": SYNC_TRACK_SCHEMA,
        "v_ref": 0.25,
        "v_max": 0.35,
        "segments": [{"kind": "line", "start": [0.0, 0.0], "end": [end_x, 0.0]}],
    }
    return {
        "attempt_id": attempt_id,
        "execution_generation": 1,
        "budget_ms": 30000,
        "invocation": {
            "mission_id": "m-http",
            "task_id": "t-1",
            "group_id": "g-http",
            "role_id": "r1",
            "intent": {
                "operation": OP_SYNC_FOLLOW,
                "objective": "walk short line",
                "parameters": {
                    "track_spec_json": json.dumps(track),
                    "neighbors_json": "[]",
                    "sync_params_json": json.dumps({"skip_align": True, "done_drain_s": 0.2}),
                },
            },
        },
    }


def test_http_routes_idempotency_conflict_and_peer_channel_lifecycle(tmp_path: Path) -> None:
    """Verify HTTP routes, idempotency/conflict handling, PeerChannels, and cancel safety."""
    db_path = tmp_path / "http.db"
    svc = SyncService(
        adapter=SimAdapter(x0=0.0, y0=0.0, yaw0=0.0),
        journal=SyncJournal(db_path),
        hub=UdpHub(bind_host="127.0.0.1", bind_port=0),
        node_id="node-http-test",
        advertise_ip="127.0.0.1",
        clock_probe=FixedClockProbe(offset_ms=35.0, synchronized=True),
        max_clock_offset_ms=20.0,
        http_host="127.0.0.1",
        http_port=0,
    )
    svc.start()
    try:
        base = f"http://127.0.0.1:{svc.port}"

        st, h = http_json("GET", f"{base}/v1/sync/health")
        assert st == 200 and h["state"] == "ONLINE" and h["udp_port"] == svc.udp_port

        st, r_sync = http_json("GET", f"{base}/v1/sync/readiness/sync-follow")
        assert st == 200 and r_sync["state"] == "READY" and r_sync["ready"] is True

        # Clock offset 35ms > 20ms gate makes choreography UNAVAILABLE without dropping HTTP 200
        st, r_ch = http_json("GET", f"{base}/v1/sync/readiness/choreography")
        assert st == 200 and r_ch["state"] == "UNAVAILABLE" and r_ch["ready"] is False

        st, ep = http_json("GET", f"{base}/v1/sync/endpoint")
        assert st == 200 and ep["schema"] == SYNC_ENDPOINT_SCHEMA
        assert ep["peer_endpoint"] == f"127.0.0.1:{svc.udp_port}"

        # Pre-dispatch PeerChannel session arm & delete
        st, _ = http_json(
            "POST",
            f"{base}/v1/peer-channels/sessions",
            {
                "group_id": "g-pc",
                "context_id": "ctx-pc",
                "context_role_id": "r1",
                "channel_instance_id": "ch-1",
            },
        )
        assert st == 200
        st, pc = http_json("GET", f"{base}/v1/peer-channels")
        assert st == 200 and len(pc["channels"]) == 1 and pc["channels"][0]["ready"] is True

        st, _ = http_json(
            "DELETE",
            f"{base}/v1/peer-channels/sessions",
            {"group_id": "g-pc", "context_id": "ctx-pc", "context_role_id": "r1"},
        )
        assert st == 200
        st, pc2 = http_json("GET", f"{base}/v1/peer-channels")
        assert st == 200 and pc2["channels"] == []

        # Bad payload rejected 400 (including malformed track segments missing required keys)
        st, _ = http_json("POST", f"{base}/v1/sync/executions", {"attempt_id": "bad"})
        assert st == 400
        bad_track_req = _sample_sync_request("att-bad-seg")
        bad_track_req["invocation"]["intent"]["parameters"]["track_spec_json"] = json.dumps(
            {"schema_version": SYNC_TRACK_SCHEMA, "segments": [{"kind": "line"}]}
        )
        st, err_body = http_json("POST", f"{base}/v1/sync/executions", bad_track_req)
        assert st == 400 and err_body["error"] == "bad_request"

        # Verify idempotent replay (200), mismatched digest (409), concurrent busy (409).
        req_a = _sample_sync_request("att-busy", end_x=2.0)
        st, res1 = http_json("POST", f"{base}/v1/sync/executions", req_a)
        assert st == 202
        handle = res1["handle"]

        st, res2 = http_json("POST", f"{base}/v1/sync/executions", req_a)
        assert st == 200 and res2["handle"] == handle

        req_a_mutated = _sample_sync_request("att-busy", end_x=2.5)
        st, _ = http_json("POST", f"{base}/v1/sync/executions", req_a_mutated)
        assert st == 409

        st, _ = http_json(
            "POST", f"{base}/v1/sync/executions", _sample_sync_request("att-other", end_x=0.5)
        )
        assert st == 409

        # Cancel active run and confirm CANCELED safe-state observation format
        st, _ = http_json(
            "POST",
            f"{base}/v1/sync/executions/cancel",
            {"handle": handle, "command_id": "cmd-test-1"},
        )
        assert st == 200
        deadline = time.monotonic() + 3.0
        cur_state = ""
        while time.monotonic() < deadline:
            st, s = http_json("POST", f"{base}/v1/sync/executions/status", {"handle": handle})
            assert st == 200
            cur_state = s["state"]
            if cur_state == "CANCELED":
                break
            time.sleep(0.05)
        assert cur_state == "CANCELED"

        st, ss = http_json("GET", f"{base}/v1/sync/executions/safe-state")
        assert st == 200
        obs = ss["observations"]
        assert len(obs) == 1
        assert obs[0] == {
            "execution_id": "att-busy",
            "execution_generation": 1,
            "command_id": "cmd-test-1",
            "safe_state": True,
            "assurance": "device-observation",
        }
    finally:
        svc.close()


def test_journal_crash_recovery_marks_interrupted_rows_unknown(tmp_path: Path) -> None:
    """Verify interrupted runs recover as UNKNOWN while FAILED observations persist."""
    db_path = tmp_path / "crash.db"
    j1 = SyncJournal(db_path)
    r_run, _ = j1.admit(
        attempt_id="att-running",
        execution_generation=3,
        operation=OP_SYNC_FOLLOW,
        canonical_body={"x": 1},
    )
    j1.mark_running(r_run["handle"], "in flight")
    r_fail, _ = j1.admit(
        attempt_id="att-failed",
        execution_generation=4,
        operation=OP_CHOREOGRAPHY_PLAY,
        canonical_body={"x": 2},
    )
    j1.mark_terminal(r_fail["handle"], "FAILED", "lidar blocked", safe_state_confirmed=True)
    j1.close()

    # Reopen simulates crash/restart -> RUNNING becomes UNKNOWN; FAILED remains observable.
    j2 = SyncJournal(db_path)
    try:
        recovered = j2.get(r_run["handle"])
        assert recovered["state"] == "UNKNOWN"
        assert recovered["safe_state_confirmed"] is False
        assert "not replayed" in recovered["detail"]

        ss = j2.safe_state_observations()
        assert len(ss["observations"]) == 1
        assert ss["observations"][0] == {
            "execution_id": "att-failed",
            "execution_generation": 4,
            "command_id": None,
            "terminal_state": "FAILED",
            "safe_state": True,
            "assurance": "device-observation",
        }
    finally:
        j2.close()


def test_deploy_templates_match_roboguide_schemas() -> None:
    """Validate TOML/JSON deploy templates against RoboGuide schema contracts."""
    deploy_dir = SYNC_PROVIDER_ROOT / "deploy"

    # 1. Validate node.sync-addon.toml via Python tomllib
    node_toml = tomllib.loads((deploy_dir / "node.sync-addon.toml").read_text(encoding="utf-8"))
    assert len(node_toml["local_systems"]) == 1
    ls = node_toml["local_systems"][0]
    safety = json.loads(ls["metadata"]["roboguide.execution-safety"])
    assert safety["schema"] == "roboguide.execution-safety/v0.2"
    safety_ops = {
        f"{o['operation']['namespace']}.{o['operation']['name']}@{o['operation']['version']}": o
        for o in safety["operations"]
    }
    assert set(safety_ops) == {OP_SYNC_FOLLOW, OP_CHOREOGRAPHY_PLAY}
    assert all(o["safe_state_export_id"] == "sync-safe-state" for o in safety_ops.values())
    assert all(o["stop_deadline_ms"] >= 6000 for o in safety_ops.values())

    export_schemas = {e["id"]: e["payload_schema"] for e in node_toml["state_exports"]}
    assert export_schemas["sync-safe-state"] == SAFE_STATE_OBSERVATION_SCHEMA
    assert export_schemas["sync-peer-endpoint"] == SYNC_ENDPOINT_SCHEMA
    assert node_toml["peer_channel_observers"][0]["channels_pointer"] == "/channels"
    assert node_toml["resources"][0]["allocation_mode"] == "exclusive"
    assert all(op["local_locks"] == ["dog1-pilot-task"] for op in node_toml["operations"])

    # 2. Validate sync-capability-catalog.json
    cat = json.loads((deploy_dir / "sync-capability-catalog.json").read_text(encoding="utf-8"))
    assert cat["schema_version"] == "roboguide.capability-catalog/v0.3"
    op_names = {
        f"{o['operation']['namespace']}.{o['operation']['name']}@{o['operation']['version']}"
        for o in cat["operations"]
    }
    assert op_names == {OP_SYNC_FOLLOW, OP_CHOREOGRAPHY_PLAY}

    # 3. Validate example-sync-follow-mission.json against v0.10 constraints
    m_sync = json.loads(
        (deploy_dir / "example-sync-follow-mission.json").read_text(encoding="utf-8")
    )
    assert m_sync["schema_version"] == "roboguide.mission-plan/v0.10"
    ctx_s = m_sync["contexts"][0]
    assert ctx_s["coupling_mode"] == "tightly-coupled-cooperation"
    assert ID_RE.match(ctx_s["peer_channel"]["profile_id"])
    assert len(m_sync["tasks"]) == 3
    for t in m_sync["tasks"]:
        role = t["roles"][0]
        for res in role["requirements"]["resources"]:
            assert res["kind"] == "space" and "resource_class" not in res
        params = role["execution_intent"]["parameters"]
        assert all(isinstance(v, (bool, int, float, str)) for v in params.values())
        parse_track_spec(params["track_spec_json"])
        parse_neighbors(params["neighbors_json"])

    # 4. Validate single-performer fault-isolated example-choreography-mission.json
    m_ch = json.loads(
        (deploy_dir / "example-choreography-mission.json").read_text(encoding="utf-8")
    )
    assert m_ch["schema_version"] == "roboguide.mission-plan/v0.10"
    assert m_ch["contexts"][0]["coupling_mode"] == "independent"
    assert m_ch["contexts"][0]["relations"] == []
    assert m_ch["contexts"][0]["executor_constraints"] == []
    assert len(m_ch["mission"]["actors"]) == 1
    assert m_ch["mission"]["actors"][0]["physical_entity"] == "entity-robot-01"
    assert len(m_ch["tasks"]) == 1
    for t in m_ch["tasks"]:
        params = t["roles"][0]["execution_intent"]["parameters"]
        assert all(isinstance(v, (bool, int, float, str)) for v in params.values())
        spec = parse_choreography_spec(params["choreography_spec_json"])
        assert spec.schema_version == CHOREOGRAPHY_SCHEMA
        assert hashlib.sha256(params["choreography_spec_json"].encode("utf-8")).hexdigest()
