"""Sparse 20 Hz UDP sync transport with gossip barriers and ESTOP flooding."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from .schemas import SYNC_PHASE_SCHEMA, NeighborSpec, SchemaError, validate_sync_phase_packet


class UdpHub:
    """Single shared non-blocking UDP multiplexer owned by one robot runtime node."""

    def __init__(self, bind_host: str = "0.0.0.0", bind_port: int = 18201) -> None:
        """Bind UDP socket and start background demultiplexing loop."""
        self.bind_host = bind_host
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind_host, bind_port))
        self.sock.settimeout(0.05)
        self.local_port = int(self.sock.getsockname()[1])
        self._lock = threading.Lock()
        self._handlers: dict[
            tuple[str, str], Callable[[dict[str, Any], tuple[str, int], int], None]
        ] = {}
        self._closed = threading.Event()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="sync-udp-rx", daemon=True)
        self._rx_thread.start()

    def register(
        self,
        channel_key: tuple[str, str],
        callback: Callable[[dict[str, Any], tuple[str, int], int], None],
    ) -> None:
        """Register packet callback for `channel_key`."""
        with self._lock:
            self._handlers[channel_key] = callback

    def unregister(self, channel_key: tuple[str, str]) -> None:
        """Remove packet callback for `channel_key`."""
        with self._lock:
            self._handlers.pop(channel_key, None)

    def sendto(self, payload: bytes, addr: tuple[str, int]) -> bool:
        """Send one raw UDP datagram, returning False on socket error."""
        if self._closed.is_set():
            return False
        try:
            self.sock.sendto(payload, addr)
            return True
        except OSError:
            return False

    def _rx_loop(self) -> None:
        """Receive, validate, and dispatch incoming UDP sync packets."""
        while not self._closed.is_set():
            try:
                raw, addr = self.sock.recvfrom(8192)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                msg = validate_sync_phase_packet(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, SchemaError, ValueError, TypeError):
                continue
            with self._lock:
                cbs = [cb for (gid, _tok), cb in self._handlers.items() if gid == msg["group_id"]]
            for cb in cbs:
                try:
                    cb(msg, addr, len(raw))
                except Exception:
                    pass

    def close(self) -> None:
        """Stop RX thread and close the shared UDP socket."""
        self._closed.set()
        try:
            self.sock.close()
        except OSError:
            pass
        if self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)


class PeerTransport:
    """Per-execution / per-session neighbor state, gossip barriers, and ESTOP flooding."""

    def __init__(
        self,
        hub_or_port: UdpHub | int,
        *,
        group_id: str,
        epoch: int,
        run_id: str,
        node_id: str,
        role_id: str,
        neighbors: tuple[NeighborSpec, ...],
        expected_barrier_roles: tuple[str, ...],
        bind_host: str = "127.0.0.1",
        on_estop: Callable[[str, str], None] | None = None,
        accept_idle_heartbeat: bool = False,
    ) -> None:
        """Initialize peer state tables and register with UDP hub."""
        if isinstance(hub_or_port, UdpHub):
            self._hub = hub_or_port
            self._owns_hub = False
        else:
            self._hub = UdpHub(bind_host=bind_host, bind_port=int(hub_or_port))
            self._owns_hub = True

        self.group_id = group_id
        self.epoch = epoch
        self.run_id = run_id
        self.node_id = node_id
        self.role_id = role_id
        self.neighbors = neighbors
        self._neighbor_map = {n.role_id: n for n in neighbors}
        self._on_estop = on_estop
        self._accept_idle_heartbeat = bool(accept_idle_heartbeat)
        self._key = (group_id, f"{run_id}:{role_id}:{id(self)}")

        # Roles required to pass an N-party barrier: group_roles if given, else direct neighbors.
        roles_set = (
            set(expected_barrier_roles)
            if expected_barrier_roles
            else {role_id, *(n.role_id for n in neighbors)}
        )
        roles_set.add(role_id)
        self.expected_barrier_roles = frozenset(roles_set)

        self._lock = threading.Lock()
        self.tx_suppressed = threading.Event()
        self.estop_event = threading.Event()
        self._estop_info: tuple[str, str] | None = None  # (origin, reason)
        self._forwarded_estops: set[tuple[str, str]] = set()
        self._propagated_hold_until: float = 0.0
        self._last_hold_fwd_mono: float = 0.0
        self._last_gamma: float = 0.0

        self._tx_seq = 0
        self._tx_packets = 0
        self._rx_packets = 0
        self._tx_bytes = 0
        self._rx_bytes = 0
        self._sent_ts: dict[int, float] = {}

        # Per direct neighbor state
        self._peer_state: dict[str, dict[str, Any]] = {
            n.role_id: {
                "rx_mono": None,
                "seq": None,
                "gamma": None,
                "gamma_dot": 0.0,
                "phase_id": None,
                "pose_hint": None,
                "rtt_ms": None,
                "tier1_hold": False,
                "estop": False,
            }
            for n in neighbors
        }

        # Transitive barrier knowledge: stage -> set[role_id]
        self._barrier_seen: dict[str, set[str]] = {}
        self._barrier_released: set[str] = set()

        self._hub.register(self._key, self._on_packet)

    @property
    def local_port(self) -> int:
        """Return bound UDP port number."""
        return self._hub.local_port

    def close(self) -> None:
        """Unregister from hub and close owned UDP sockets."""
        self._hub.unregister(self._key)
        if self._owns_hub:
            self._hub.close()

    def suppress_tx(self) -> None:
        """Stop all outgoing UDP packets immediately (controlled fault injection)."""
        self.tx_suppressed.set()

    def _on_packet(self, msg: dict[str, Any], _addr: tuple[str, int], raw_len: int) -> None:
        """Handle one validated UDP phase packet from a configured neighbor."""
        if msg["epoch"] != self.epoch or msg["role_id"] == self.role_id:
            return
        if not self._accept_idle_heartbeat and msg["phase_id"] == "IDLE_HEARTBEAT":
            return
        sender = msg["role_id"]
        if sender not in self._neighbor_map:
            return
        now_m = time.monotonic()

        fwd_estop: tuple[str, str, int] | None = None
        fwd_released_stage: str | None = None
        fwd_hold_ttl: int = 0

        with self._lock:
            self._rx_packets += 1
            self._rx_bytes += raw_len
            ps = self._peer_state.get(sender)
            if ps is not None:
                ps["rx_mono"] = now_m
                ps["seq"] = msg["seq"]
                ps["gamma"] = msg["gamma"]
                ps["gamma_dot"] = msg["gamma_dot"]
                ps["phase_id"] = msg["phase_id"]
                ps["pose_hint"] = msg["pose_hint"]
                ps["tier1_hold"] = msg["tier1_hold"]
                ps["estop"] = msg["estop"]
                echo = msg["echo_seq"]
                if isinstance(echo, int) and echo in self._sent_ts:
                    ps["rtt_ms"] = (now_m - self._sent_ts[echo]) * 1000.0
                    cutoff = echo - 128
                    for k in [kk for kk in self._sent_ts if kk < cutoff]:
                        del self._sent_ts[k]

            bstage = msg["barrier_stage"]
            if bstage and msg["barrier_state"] in ("READY", "RELEASED"):
                s = self._barrier_seen.setdefault(bstage, set())
                for r in msg["barrier_seen"]:
                    s.add(r)
                s.add(sender)
                if (
                    msg["barrier_state"] == "READY"
                    and bstage in self._barrier_released
                    and not msg["barrier_released_stage"]
                ):
                    fwd_released_stage = bstage

            rstage = msg["barrier_released_stage"]
            if rstage and rstage not in self._barrier_released:
                self._barrier_released.add(rstage)
                self._barrier_seen.setdefault(rstage, set()).update(self.expected_barrier_roles)
                fwd_released_stage = rstage

            if msg["tier1_hold"] and msg["hold_ttl"] > 0:
                self._propagated_hold_until = max(self._propagated_hold_until, now_m + 0.25)
                if msg["hold_ttl"] > 1 and now_m - self._last_hold_fwd_mono >= 0.04:
                    self._last_hold_fwd_mono = now_m
                    fwd_hold_ttl = msg["hold_ttl"] - 1

            if msg["estop"]:
                origin = msg["estop_origin"] or sender
                reason = msg["estop_reason"] or "peer_estop"
                key = (origin, reason)
                if self._estop_info is None:
                    self._estop_info = (origin, reason)
                self.estop_event.set()
                if key not in self._forwarded_estops and msg["estop_ttl"] > 1:
                    self._forwarded_estops.add(key)
                    fwd_estop = (origin, reason, msg["estop_ttl"] - 1)
            local_gamma = self._last_gamma

        if fwd_estop is not None:
            origin, reason, ttl = fwd_estop
            if self._on_estop is not None:
                try:
                    self._on_estop(origin, reason)
                except Exception:
                    pass
            self._flood_estop(origin=origin, reason=reason, ttl=ttl, exclude_role=sender)
        elif msg["estop"] and self._on_estop is not None:
            try:
                self._on_estop(msg["estop_origin"] or sender, msg["estop_reason"] or "peer_estop")
            except Exception:
                pass

        if fwd_released_stage is not None:
            self.send_state(
                phase_id="BARRIER",
                gamma=local_gamma,
                gamma_dot=0.0,
                barrier_stage=fwd_released_stage,
                barrier_state="RELEASED",
                barrier_released_stage=fwd_released_stage,
                burst=2,
            )

        if fwd_hold_ttl > 0:
            self.send_state(
                phase_id="FOLLOW",
                gamma=local_gamma,
                gamma_dot=0.0,
                tier1_hold=True,
                hold_ttl=fwd_hold_ttl,
                exclude_role=sender,
            )

    def send_state(
        self,
        *,
        phase_id: str,
        gamma: float,
        gamma_dot: float,
        barrier_stage: str = "",
        barrier_state: str = "NONE",
        barrier_released_stage: str = "",
        tier1_hold: bool = False,
        hold_ttl: int = 0,
        estop: bool = False,
        estop_reason: str = "",
        estop_origin: str = "",
        estop_ttl: int = 0,
        pose_hint: list[float] | None = None,
        burst: int = 1,
        exclude_role: str | None = None,
    ) -> None:
        """Broadcast one `roboguide.sync-phase/v1` packet to configured direct neighbors."""
        if self.tx_suppressed.is_set() or not self.neighbors:
            return
        with self._lock:
            if exclude_role is None and not tier1_hold and not estop:
                self._last_gamma = max(0.0, min(1.0, float(gamma)))
            seen_list = (
                sorted(self._barrier_seen.get(barrier_stage, {self.role_id}))
                if barrier_stage
                else []
            )
            now_m = time.monotonic()
            wall_ms = int(time.time() * 1000)

        for n in self.neighbors:
            if exclude_role and n.role_id == exclude_role:
                continue
            with self._lock:
                self._tx_seq += 1
                seq = self._tx_seq
                self._sent_ts[seq] = now_m
                if len(self._sent_ts) > 256:
                    cutoff = seq - 256
                    for k in [kk for kk in self._sent_ts if kk < cutoff]:
                        del self._sent_ts[k]
                echo_seq = self._peer_state[n.role_id]["seq"]
            pkt = {
                "schema": SYNC_PHASE_SCHEMA,
                "group_id": self.group_id,
                "epoch": self.epoch,
                "run_id": self.run_id,
                "node_id": self.node_id,
                "role_id": self.role_id,
                "seq": seq,
                "echo_seq": echo_seq,
                "phase_id": phase_id,
                "gamma": round(float(gamma), 5),
                "gamma_dot": round(float(gamma_dot), 5),
                "barrier_stage": barrier_stage,
                "barrier_state": barrier_state,
                "barrier_seen": seen_list,
                "barrier_released_stage": barrier_released_stage,
                "tier1_hold": bool(tier1_hold),
                "hold_ttl": int(hold_ttl),
                "estop": bool(estop),
                "estop_reason": estop_reason,
                "estop_origin": estop_origin,
                "estop_ttl": int(estop_ttl),
                "pose_hint": pose_hint,
                "wall_ms": wall_ms,
            }
            raw = json.dumps(pkt, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            for _ in range(max(1, burst)):
                if self._hub.sendto(raw, (n.host, n.port)):
                    with self._lock:
                        self._tx_packets += 1
                        self._tx_bytes += len(raw)

    def mark_local_barrier_ready(self, stage: str, gamma: float) -> bool:
        """Record self ready at `stage`; returns True if ALL expected group roles are now seen."""
        release_now = False
        with self._lock:
            s = self._barrier_seen.setdefault(stage, set())
            s.add(self.role_id)
            if stage in self._barrier_released:
                return True
            if self.expected_barrier_roles.issubset(s):
                self._barrier_released.add(stage)
                release_now = True

        if release_now:
            self.send_state(
                phase_id="BARRIER",
                gamma=gamma,
                gamma_dot=0.0,
                barrier_stage=stage,
                barrier_state="RELEASED",
                barrier_released_stage=stage,
                burst=3,
            )
            return True
        self.send_state(
            phase_id="BARRIER",
            gamma=gamma,
            gamma_dot=0.0,
            barrier_stage=stage,
            barrier_state="READY",
        )
        return False

    def is_barrier_released(self, stage: str) -> bool:
        """Return whether `stage` has been released across the group."""
        with self._lock:
            return stage in self._barrier_released

    def _flood_estop(
        self, *, origin: str, reason: str, ttl: int, exclude_role: str | None = None
    ) -> None:
        """Propagate ESTOP packets across remaining neighbors with bounded TTL."""
        self.send_state(
            phase_id="ESTOP",
            gamma=0.0,
            gamma_dot=0.0,
            estop=True,
            estop_reason=reason,
            estop_origin=origin,
            estop_ttl=ttl,
            burst=3,
            exclude_role=exclude_role,
        )

    def broadcast_estop(self, reason: str, gamma: float = 0.0, ttl: int = 16) -> None:
        """Trip local ESTOP immediately and flood across the neighbor graph (< 50 ms)."""
        with self._lock:
            if self._estop_info is None:
                self._estop_info = (self.role_id, reason)
            self._forwarded_estops.add((self.role_id, reason))
            self.estop_event.set()
        self.send_state(
            phase_id="ESTOP",
            gamma=gamma,
            gamma_dot=0.0,
            estop=True,
            estop_reason=reason,
            estop_origin=self.role_id,
            estop_ttl=ttl,
            burst=3,
        )

    def get_neighbor_snapshot(self, now_m: float | None = None) -> dict[str, Any]:
        """Return consistent snapshot of neighbor progress, ages, ESTOP, and propagated hold."""
        if now_m is None:
            now_m = time.monotonic()
        with self._lock:
            peers: dict[str, dict[str, Any]] = {}
            max_age_ms: float | None = 0.0 if self.neighbors else 0.0
            any_seen = False
            all_seen = True
            for role, ps in self._peer_state.items():
                age_ms = (now_m - ps["rx_mono"]) * 1000.0 if ps["rx_mono"] is not None else None
                if age_ms is not None:
                    any_seen = True
                    if max_age_ms is not None:
                        max_age_ms = max(max_age_ms, age_ms)
                else:
                    all_seen = False
                    max_age_ms = None
                peers[role] = {
                    "age_ms": age_ms,
                    "gamma": ps["gamma"],
                    "gamma_dot": ps["gamma_dot"],
                    "phase_id": ps["phase_id"],
                    "pose_hint": ps["pose_hint"],
                    "rtt_ms": ps["rtt_ms"],
                    "tier1_hold": ps["tier1_hold"],
                    "estop": ps["estop"],
                    "weight": self._neighbor_map[role].weight,
                }
            return {
                "peers": peers,
                "any_seen": any_seen,
                "all_seen": all_seen,
                "max_age_ms": max_age_ms,
                "estop_info": self._estop_info,
                "propagated_hold": now_m < self._propagated_hold_until,
                "stats": {
                    "tx_packets": self._tx_packets,
                    "rx_packets": self._rx_packets,
                    "tx_bytes": self._tx_bytes,
                    "rx_bytes": self._rx_bytes,
                },
            }
