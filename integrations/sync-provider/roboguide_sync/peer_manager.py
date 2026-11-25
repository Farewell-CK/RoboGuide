"""Mode-4 PeerChannel session hosting and terminal silencing (`/v1/peer-channels`)."""

from __future__ import annotations

import threading
import time
from typing import Any

from .schemas import NeighborSpec
from .transport import PeerTransport, UdpHub

DEFAULT_PROFILE_ID = "roboguide-udp-sync-20hz-v1"
DEFAULT_MESSAGE_SCHEMA = "roboguide.sync-phase/v1"


class PeerChannelManager:
    """Hosts pre-dispatch and in-flight PeerChannel readiness without external daemons or SSH."""

    def __init__(self, hub: UdpHub, node_id: str, *, unready_ttl_s: float = 60.0) -> None:
        """Start background idle heartbeat thread for armed PeerChannel sessions."""
        self._hub = hub
        self._node_id = node_id
        self._unready_ttl_s = float(unready_ttl_s)
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._stop_ev = threading.Event()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="peer-channel-hb", daemon=True
        )
        self._hb_thread.start()

    def close(self) -> None:
        """Stop heartbeat loop and close all PeerChannel transports."""
        self._stop_ev.set()
        if self._hb_thread.is_alive():
            self._hb_thread.join(timeout=1.0)
        with self._lock:
            for s in list(self._sessions.values()):
                tr: PeerTransport | None = s.get("hb_transport")
                if tr is not None:
                    tr.close()
            self._sessions.clear()

    def arm_session(
        self,
        *,
        group_id: str,
        context_id: str,
        context_role_id: str,
        channel_instance_id: str,
        profile_id: str = DEFAULT_PROFILE_ID,
        message_schema: str = DEFAULT_MESSAGE_SCHEMA,
        neighbors: tuple[NeighborSpec, ...] = (),
        require_peer_rx: bool = False,
        peer_timeout_ms: float = 2500.0,
    ) -> dict[str, Any]:
        """Arm (or refresh) a logical PeerChannel endpoint observed via `GET /v1/peer-channels`."""
        key = (group_id, context_id, context_role_id)
        with self._lock:
            old = self._sessions.get(key)
            if old and old.get("hb_transport") is not None:
                old["hb_transport"].close()

            hb_tr: PeerTransport | None = None
            if neighbors:
                hb_tr = PeerTransport(
                    self._hub,
                    group_id=group_id,
                    epoch=1,
                    run_id=f"pc:{channel_instance_id}",
                    node_id=self._node_id,
                    role_id=context_role_id,
                    neighbors=neighbors,
                    expected_barrier_roles=(),
                    accept_idle_heartbeat=True,
                )
            sess = {
                "group_id": group_id,
                "context_id": context_id,
                "context_role_id": context_role_id,
                "channel_instance_id": channel_instance_id,
                "profile_id": profile_id,
                "message_schema": message_schema,
                "neighbors": neighbors,
                "require_peer_rx": bool(require_peer_rx),
                "peer_timeout_ms": float(peer_timeout_ms),
                "forced_unready": False,
                "unready_mono": None,
                "reason": "armed",
                "peer_ever_seen": False,
                "hb_transport": hb_tr,
                "active_sync": False,
                "sync_peer_seen": False,
                "sync_max_age_ms": None,
            }
            self._sessions[key] = sess
            return self._format_session_locked(sess)

    def ensure_runtime_session(
        self,
        *,
        group_id: str,
        context_id: str,
        context_role_id: str,
        channel_instance_id: str,
        profile_id: str = DEFAULT_PROFILE_ID,
        message_schema: str = DEFAULT_MESSAGE_SCHEMA,
    ) -> None:
        """Ensure an active sync execution has a channel record so fences propagate cleanly."""
        key = (group_id, context_id, context_role_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                sess = {
                    "group_id": group_id,
                    "context_id": context_id,
                    "context_role_id": context_role_id,
                    "channel_instance_id": channel_instance_id,
                    "profile_id": profile_id,
                    "message_schema": message_schema,
                    "neighbors": (),
                    "require_peer_rx": False,
                    "peer_timeout_ms": 2500.0,
                    "forced_unready": False,
                    "unready_mono": None,
                    "reason": "active_sync",
                    "peer_ever_seen": True,
                    "hb_transport": None,
                    "active_sync": True,
                    "sync_peer_seen": True,
                    "sync_max_age_ms": 0.0,
                }
                self._sessions[key] = sess
            else:
                sess["active_sync"] = True
                sess["forced_unready"] = False
                sess["unready_mono"] = None
                sess["reason"] = "active_sync"
                sess["channel_instance_id"] = channel_instance_id

    def mark_unready(
        self, group_id: str, context_id: str, context_role_id: str, reason: str
    ) -> None:
        """Trip local readiness to `False` immediately (drives Controller `Ready -> Fenced`)."""
        key = (group_id, context_id, context_role_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is not None:
                sess["forced_unready"] = True
                sess["unready_mono"] = time.monotonic()
                sess["reason"] = reason
                tr: PeerTransport | None = sess.get("hb_transport")
                if tr is not None:
                    tr.close()
                    sess["hb_transport"] = None

    def reap_expired_unready(self, now_mono: float | None = None) -> int:
        """Evict unready sessions whose TTL elapsed after observers reported `Fenced`."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        removed = 0
        with self._lock:
            for k, sess in list(self._sessions.items()):
                um = sess.get("unready_mono")
                if (
                    sess.get("forced_unready")
                    and um is not None
                    and (now - um) >= self._unready_ttl_s
                ):
                    tr: PeerTransport | None = sess.pop("hb_transport", None)
                    if tr is not None:
                        tr.close()
                    del self._sessions[k]
                    removed += 1
        return removed

    def update_sync_health(
        self,
        group_id: str,
        context_id: str,
        context_role_id: str,
        *,
        all_seen: bool,
        max_age_ms: float | None,
    ) -> None:
        """Update cached in-flight peer reception metrics for a session."""
        key = (group_id, context_id, context_role_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is not None:
                sess["sync_peer_seen"] = all_seen
                sess["sync_max_age_ms"] = max_age_ms
                if all_seen:
                    sess["peer_ever_seen"] = True

    def silence_on_terminal(self, group_id: str, context_id: str, context_role_id: str) -> bool:
        """Remove session once local execution completes/terminates (ADR-0027 authority rule)."""
        key = (group_id, context_id, context_role_id)
        with self._lock:
            sess = self._sessions.pop(key, None)
            if sess is None:
                return False
            tr: PeerTransport | None = sess.get("hb_transport")
            if tr is not None:
                tr.close()
            return True

    def _compute_ready_locked(self, sess: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate local PeerChannel readiness and reason under lock."""
        if sess["forced_unready"]:
            return False, sess["reason"]
        if sess["active_sync"]:
            return True, "sync_active"
        tr: PeerTransport | None = sess.get("hb_transport")
        if tr is None or not sess["neighbors"]:
            return True, "local_endpoint_ready"
        snap = tr.get_neighbor_snapshot()
        max_age = snap["max_age_ms"]
        if snap["all_seen"]:
            sess["peer_ever_seen"] = True
        if snap["all_seen"] and max_age is not None and max_age <= sess["peer_timeout_ms"]:
            return True, "peer_link_active"
        if sess["peer_ever_seen"]:
            return False, "peer_heartbeat_lost"
        if sess["require_peer_rx"]:
            return False, "awaiting_peer_heartbeat"
        return True, "local_endpoint_ready_awaiting_peer"

    def _format_session_locked(self, sess: dict[str, Any]) -> dict[str, Any]:
        """Serialize one PeerChannel session record under lock."""
        ready, reason = self._compute_ready_locked(sess)
        return {
            "group_id": sess["group_id"],
            "context_id": sess["context_id"],
            "context_role_id": sess["context_role_id"],
            "channel_instance_id": sess["channel_instance_id"],
            "profile_id": sess["profile_id"],
            "message_schema": sess["message_schema"],
            "ready": ready,
            "reason": reason,
        }

    def snapshot_channels(self) -> dict[str, Any]:
        """Return `channels` payload consumed directly by `node-config/v0.14` observer."""
        with self._lock:
            channels = [self._format_session_locked(s) for s in self._sessions.values()]
        return {"channels": channels}

    def _heartbeat_loop(self) -> None:
        """Broadcast idle heartbeats and reap expired unready sessions."""
        while not self._stop_ev.is_set():
            self.reap_expired_unready()
            with self._lock:
                transports = [
                    (s["hb_transport"], s["forced_unready"])
                    for s in self._sessions.values()
                    if s.get("hb_transport") is not None and not s["active_sync"]
                ]
            for tr, forced_unready in transports:
                if tr is not None and not forced_unready:
                    tr.send_state(phase_id="IDLE_HEARTBEAT", gamma=0.0, gamma_dot=0.0)
            time.sleep(0.10)
