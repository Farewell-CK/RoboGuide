"""Original host-monotonic CPU budgets, separate from audit wall time and remote clocks."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import digest, integer
from .storage import read_json


def clock_domain() -> str:
    """Bind Linux CLOCK_MONOTONIC to the actual boot without claiming hardware identity."""
    descriptor = os.open("/proc/sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.read(descriptor, 128).decode("ascii").strip()
    finally:
        os.close(descriptor)
    if value != str(uuid.UUID(value)):
        raise ValueError("original Linux monotonic clock domain is unavailable")
    return digest(f"linux-clock-monotonic-boot/v0.1:{value}".encode())


def freeze(timeout_s: int) -> dict[str, Any]:
    """Freeze one original local clock/deadline before source acquisition and native effects."""
    admitted = time.monotonic_ns()
    return {
        "clock_id": clock_domain(),
        "admitted_monotonic_ns": admitted,
        "deadline_monotonic_ns": admitted + timeout_s * 1_000_000_000,
    }


def remaining(intent: dict[str, Any], now_ms: int, timeout_s: int) -> float:
    """Bound work by both original clocks; rollback, restart or another host never renews it."""
    start = integer(
        intent.get("admitted_monotonic_ns"), "original monotonic admission", 0, 2**63 - 1
    )
    deadline = integer(
        intent.get("deadline_monotonic_ns"), "original monotonic deadline", 0, 2**63 - 1
    )
    wall_deadline = integer(intent.get("deadline_ms"), "original wall deadline", 0, 2**63 - 1)
    if intent.get("clock_id") != clock_domain() or deadline - start != timeout_s * 1_000_000_000:
        raise ValueError("original host-monotonic budget is unavailable; observation only")
    return max(
        0.0,
        min(
            (deadline - time.monotonic_ns()) / 1_000_000_000,
            (wall_deadline - now_ms) / 1000,
        ),
    )


def completion(directory: Path) -> dict[str, Any]:
    """Bind actual computation completion to its original host clock before sealing output proof."""
    intent = read_json(directory / "intent.json")
    domain = clock_domain()
    if intent.get("clock_id") != domain:
        raise ValueError("computed result lost its original host clock domain")
    return {"clock_id": domain, "computed_monotonic_ns": time.monotonic_ns()}


def completed_late(intent: dict[str, Any], computed: dict[str, Any]) -> bool | None:
    """Check immutable original timing after reboot without comparing the new host's clock."""
    if "clock_id" not in intent:
        if "clock_id" in computed or "computed_monotonic_ns" in computed:
            raise ValueError("historical timing cannot contain new original-clock proof")
        return None
    original = integer(
        intent.get("admitted_monotonic_ns"), "original monotonic admission", 0, 2**63 - 1
    )
    deadline = integer(
        intent.get("deadline_monotonic_ns"), "original monotonic deadline", 0, 2**63 - 1
    )
    finished = integer(
        computed.get("computed_monotonic_ns"), "computed monotonic time", 0, 2**63 - 1
    )
    if (
        computed.get("clock_id") != intent["clock_id"]
        or not original <= finished
        or deadline <= original
    ):
        raise ValueError("computed original monotonic timing is missing or contradictory")
    return bool(finished > deadline)
