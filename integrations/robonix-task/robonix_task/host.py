"""Report host availability separately from robot observations without any actuator interface."""

from __future__ import annotations

import copy
import math
import os
import platform
import socket
import time
from pathlib import Path
from typing import Any


def _text(path: Path) -> str | None:
    """Read one bounded local kernel observation, preserving missing data as unknown."""
    try:
        with path.open("rb") as stream:
            return stream.read(512).decode("utf-8", errors="replace").strip("\x00\r\n ") or None
    except OSError:
        return None


def inspect_host(
    node_id: str,
    observations: dict[str, Any],
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
) -> dict[str, Any]:
    """Return fresh host facts while retaining unavailable robot sensors and uncertain power."""
    if (
        not node_id
        or len(node_id) > 128
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for c in node_id
        )
    ):
        raise ValueError("explicit path-safe deployment Node identity required")
    uptime = _text(proc_root / "uptime")
    try:
        seconds = float(uptime.split()[0]) if uptime else None
        if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
            seconds = None
    except ValueError:
        seconds = None
    interfaces: list[dict[str, Any]] = []
    network = sys_root / "class/net"
    if network.is_dir():
        for path in sorted(network.iterdir())[:32]:
            if path.name != "lo":
                carrier = _text(path / "carrier")
                interfaces.append(
                    {
                        "name": path.name,
                        "operstate": _text(path / "operstate"),
                        "carrier": carrier == "1" if carrier in {"0", "1"} else None,
                    }
                )
    return {
        "schema_version": "roboguide.host-inspection/v0.1",
        "node_id": node_id,
        "observed_at_ns": time.time_ns(),
        "health_state": "ONLINE",
        "readiness_state": "READY",
        "execution_state": "SUCCEEDED",
        "detail": "host inspection completed; robot execution readiness is separate",
        "host": {
            "hostname": socket.gethostname(),
            "architecture": platform.machine(),
            "hardware_model": _text(proc_root / "device-tree/model"),
            "boot_id": _text(proc_root / "sys/kernel/random/boot_id"),
            "uptime_seconds": seconds,
            "cpu_count": os.cpu_count(),
            "interfaces": interfaces,
        },
        "robot_observation": {
            key: copy.deepcopy(observations.get(key))
            for key in (
                "observed_at_ns",
                "atlas",
                "mapping",
                "sensors",
                "navigation_readiness",
                "chassis",
            )
        },
        "chassis_power": "not_observed",
        "motion_authorized": False,
    }
