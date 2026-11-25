"""L0 tests for consensus law, 5-node gossip barriers, ESTOP flooding, and clock gating."""

from __future__ import annotations

import time

import pytest
from roboguide_sync.clock_sync import (
    ClockSyncError,
    FixedClockProbe,
    parse_chronyc_csv,
    parse_chronyc_human,
    parse_ptp_pmc,
    verify_clock_offset,
)
from roboguide_sync.schemas import NeighborSpec
from roboguide_sync.sync_engine import compute_consensus_gamma_dot
from roboguide_sync.transport import PeerTransport, UdpHub


def test_consensus_control_law_lag_lead_and_wait_clamp() -> None:
    """Verify distributed consensus speedup, slowdown, and lead-wait clamping."""
    # Neighbor ahead -> speed up
    g_dot, dgamma = compute_consensus_gamma_dot(
        0.20,
        [{"role_id": "r2", "gamma": 0.30, "weight": 1.0, "phase_id": "FOLLOW"}],
        gamma_ref=0.05,
        gamma_max=0.10,
        k_sync=0.80,
        lead_wait_gamma=0.10,
    )
    assert dgamma > 0.0
    assert g_dot == pytest.approx(0.10)  # clipped at gamma_max

    # Neighbor slightly behind within wait threshold -> slow down smoothly
    g_dot_slow, dgamma_neg = compute_consensus_gamma_dot(
        0.30,
        [{"role_id": "r2", "gamma": 0.25, "weight": 1.0, "phase_id": "FOLLOW"}],
        gamma_ref=0.06,
        gamma_max=0.10,
        k_sync=0.80,
        lead_wait_gamma=0.10,
    )
    assert dgamma_neg == pytest.approx(-0.05)
    assert g_dot_slow == pytest.approx(0.02)

    # Leading beyond lead_wait_gamma while neighbor active -> hold at zero
    g_dot_wait, _ = compute_consensus_gamma_dot(
        0.50,
        [{"role_id": "r2", "gamma": 0.35, "weight": 1.0, "phase_id": "FOLLOW"}],
        gamma_ref=0.06,
        gamma_max=0.10,
        k_sync=0.80,
        lead_wait_gamma=0.10,
    )
    assert g_dot_wait == 0.0


def test_sparse_5node_chain_transitive_barrier_and_sub50ms_estop_flood() -> None:
    """Verify transitive barrier release and sub-50 ms ESTOP flooding across 5 hops."""
    hubs = [UdpHub(bind_host="127.0.0.1", bind_port=0) for _ in range(5)]
    roles = [f"r{i}" for i in range(5)]
    transports: list[PeerTransport] = []
    try:
        for i in range(5):
            nbrs = []
            if i > 0:
                nbrs.append(
                    NeighborSpec(
                        role_id=roles[i - 1],
                        host="127.0.0.1",
                        port=hubs[i - 1].local_port,
                        weight=1.0,
                    )
                )
            if i < 4:
                nbrs.append(
                    NeighborSpec(
                        role_id=roles[i + 1],
                        host="127.0.0.1",
                        port=hubs[i + 1].local_port,
                        weight=1.0,
                    )
                )
            tr = PeerTransport(
                hubs[i],
                group_id="chain-test",
                epoch=1,
                run_id="run-chain",
                node_id=roles[i],
                role_id=roles[i],
                neighbors=tuple(nbrs),
                expected_barrier_roles=tuple(roles),
            )
            transports.append(tr)

        # Even without direct edge r0 <-> r4, transitive gossip releases all 5 nodes
        deadline = time.monotonic() + 3.0
        for tr in transports:
            tr.mark_local_barrier_ready("START", 0.0)
        while time.monotonic() < deadline:
            for tr in transports:
                if not tr.is_barrier_released("START"):
                    tr.mark_local_barrier_ready("START", 0.0)
            if all(tr.is_barrier_released("START") for tr in transports):
                break
            time.sleep(0.02)
        assert all(tr.is_barrier_released("START") for tr in transports)

        # Node 0 triggers ESTOP; node 4 at far end of chain receives it < 50 ms
        t0 = time.monotonic()
        transports[0].broadcast_estop("test_fault", gamma=0.5, ttl=16)
        assert transports[4].estop_event.wait(timeout=0.05) is True
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        assert elapsed_ms < 50.0
    finally:
        for tr in transports:
            tr.close()
        for h in hubs:
            h.close()


def test_clock_sync_parsers_and_fail_closed_gate() -> None:
    """Verify 14-column chronyc CSV, human chronyc, PTP pmc parsing, and fail-closed gating."""
    csv_ok = (
        "C0A80101,192.168.1.1,2,1700000000.0,0.000412,0.000200,0.000100,"
        "1.0e-5,0.1,0.2,0.001,0.002,64.0,Normal"
    )
    st_csv = parse_chronyc_csv(csv_ok)
    assert st_csv is not None and st_csv.synchronized is True
    assert st_csv.offset_ms == pytest.approx(0.412)

    # When Last offset exceeds System time, conservative max-abs offset is reported
    csv_last = (
        "C0A80101,192.168.1.1,2,1700000000.0,0.000100,-0.003500,0.000100,"
        "1.0e-5,0.1,0.2,0.001,0.002,64.0,Normal"
    )
    st_last = parse_chronyc_csv(csv_last)
    assert st_last is not None and st_last.synchronized is True
    assert st_last.offset_ms == pytest.approx(-3.5)

    # Column 14 (index 13) "Not synchronised" MUST fail closed even when stratum/ref look valid
    csv_unsync_leap = (
        "C0A80101,192.168.1.1,2,1700000000.0,0.000000,0.000000,0.0,"
        "0.0,0.0,0.0,0.0,0.0,100.0,Not synchronised"
    )
    st_leap = parse_chronyc_csv(csv_unsync_leap)
    assert st_leap is not None and st_leap.synchronized is False

    csv_unsync = (
        "00000000,,16,1700000000.0,0.000000,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,Not synchronised"
    )
    st_bad = parse_chronyc_csv(csv_unsync)
    assert st_bad is not None and st_bad.synchronized is False

    human = """
    Reference ID    : C0A80101 (ntp.local)
    Stratum         : 3
    Last offset     : -0.001250 seconds
    Leap status     : Normal
    """
    st_h = parse_chronyc_human(human)
    assert st_h is not None and st_h.synchronized is True
    assert st_h.offset_ms == pytest.approx(-1.25)

    pmc_out = """
    sending: GET TIME_STATUS_NP
        00e04c.fffe.123456-1 seq 0 RESPONSE MANAGEMENT TIME_STATUS_NP
            master_offset              -245000
            gmPresent                  true
    """
    st_p = parse_ptp_pmc(pmc_out)
    assert st_p is not None and st_p.synchronized is True
    assert st_p.offset_ms == pytest.approx(-0.245)

    verify_clock_offset(FixedClockProbe(offset_ms=4.5, synchronized=True), 20.0)
    with pytest.raises(ClockSyncError):
        verify_clock_offset(FixedClockProbe(offset_ms=25.0, synchronized=True), 20.0)
    with pytest.raises(ClockSyncError):
        verify_clock_offset(FixedClockProbe(offset_ms=0.0, synchronized=False), 20.0)


def test_udp_hub_survives_malformed_ttl_and_garbage_datagrams() -> None:
    """Verify UdpHub ignores garbage/malformed datagrams and continues delivering."""
    import json
    import socket

    from roboguide_sync.schemas import SYNC_PHASE_SCHEMA

    h1, h2 = UdpHub(bind_host="127.0.0.1", bind_port=0), UdpHub(bind_host="127.0.0.1", bind_port=0)
    t1 = PeerTransport(
        h1,
        group_id="g-malformed",
        epoch=1,
        run_id="r1",
        node_id="n1",
        role_id="r1",
        neighbors=(NeighborSpec("r2", "127.0.0.1", h2.local_port, 1.0),),
        expected_barrier_roles=("r1", "r2"),
    )
    t2 = PeerTransport(
        h2,
        group_id="g-malformed",
        epoch=1,
        run_id="r2",
        node_id="n2",
        role_id="r2",
        neighbors=(NeighborSpec("r1", "127.0.0.1", h1.local_port, 1.0),),
        expected_barrier_roles=("r1", "r2"),
    )
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Send garbage bytes and corrupt hold_ttl / estop_ttl packets directly to h2
        raw_sock.sendto(b"\xff\xfe not-json", ("127.0.0.1", h2.local_port))
        bad_pkt = json.dumps(
            {
                "schema": SYNC_PHASE_SCHEMA,
                "group_id": "g-malformed",
                "epoch": 1,
                "run_id": "r1",
                "node_id": "n1",
                "role_id": "r1",
                "seq": 1,
                "phase_id": "FOLLOW",
                "hold_ttl": {"not": "an-int"},
                "estop_ttl": "bad-str",
                "wall_ms": 123,
            }
        ).encode("utf-8")
        raw_sock.sendto(bad_pkt, ("127.0.0.1", h2.local_port))
        time.sleep(0.05)

        # RX loop must stay alive and deliver subsequent valid packets normally
        t1.send_state(phase_id="FOLLOW", gamma=0.5, gamma_dot=0.1)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            snap = t2.get_neighbor_snapshot()
            if snap["peers"]["r1"]["gamma"] == pytest.approx(0.5):
                break
            time.sleep(0.02)
        assert t2.get_neighbor_snapshot()["peers"]["r1"]["gamma"] == pytest.approx(0.5)
    finally:
        raw_sock.close()
        t1.close()
        t2.close()
        h1.close()
        h2.close()


def test_peer_whitelist_sent_ts_bound_and_unready_reap() -> None:
    """Verify non-neighbor rejection, bounded `_sent_ts`, and unready session reaping."""
    from roboguide_sync.peer_manager import PeerChannelManager

    h1, h2 = UdpHub(bind_host="127.0.0.1", bind_port=0), UdpHub(bind_host="127.0.0.1", bind_port=0)
    t1 = PeerTransport(
        h1,
        group_id="g-wl",
        epoch=1,
        run_id="r1",
        node_id="n1",
        role_id="r1",
        neighbors=(NeighborSpec("r2", "127.0.0.1", h2.local_port, 1.0),),
        expected_barrier_roles=("r1", "r2"),
    )
    t2 = PeerTransport(
        h2,
        group_id="g-wl",
        epoch=1,
        run_id="r2",
        node_id="n2",
        role_id="r2",
        neighbors=(NeighborSpec("r1", "127.0.0.1", h1.local_port, 1.0),),
        expected_barrier_roles=("r1", "r2"),
    )
    t_rogue = PeerTransport(
        h1,
        group_id="g-wl",
        epoch=1,
        run_id="rogue",
        node_id="n3",
        role_id="rogue",
        neighbors=(NeighborSpec("r2", "127.0.0.1", h2.local_port, 1.0),),
        expected_barrier_roles=("r1", "r2"),
    )
    pm = PeerChannelManager(h1, "n1", unready_ttl_s=5.0)
    try:
        # Non-neighbor packets are dropped before touching barrier or ESTOP state
        t_rogue.broadcast_estop("spoof", gamma=0.5)
        time.sleep(0.05)
        assert not t2.estop_event.is_set()

        # _sent_ts map remains bounded even with zero echo replies
        for i in range(300):
            t1.send_state(phase_id="FOLLOW", gamma=min(1.0, i / 300.0), gamma_dot=0.1)
        assert len(t1._sent_ts) <= 257

        # PeerChannelManager closes hb_transport on mark_unready and reaps after TTL
        pm.arm_session(
            group_id="g-wl",
            context_id="ctx",
            context_role_id="r1",
            channel_instance_id="ch-1",
            neighbors=(NeighborSpec("r2", "127.0.0.1", h2.local_port, 1.0),),
        )
        pm.mark_unready("g-wl", "ctx", "r1", "fenced")
        snap = pm.snapshot_channels()
        assert len(snap["channels"]) == 1 and snap["channels"][0]["ready"] is False
        assert pm.reap_expired_unready(now_mono=time.monotonic() + 10.0) == 1
        assert pm.snapshot_channels()["channels"] == []
    finally:
        pm.close()
        t_rogue.close()
        t1.close()
        t2.close()
        h1.close()
        h2.close()
