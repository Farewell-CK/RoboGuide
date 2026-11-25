"""Chrony/PTP wall-clock offset probing and Fail-Closed choreography gating."""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from typing import Protocol


class ClockSyncError(RuntimeError):
    """Raised when wall-clock offset exceeds safety bounds or clock sync cannot be verified."""


@dataclass(frozen=True)
class ClockStatus:
    """Observed host wall-clock synchronization status."""

    synchronized: bool
    source: str
    offset_ms: float | None
    detail: str


class ClockProbe(Protocol):
    """Protocol for pluggable clock synchronization probes."""

    def read_status(self) -> ClockStatus:
        """Sample local wall-clock sync state and estimated offset (ms)."""
        ...


def parse_chronyc_csv(output: str) -> ClockStatus | None:
    """Parse standard 14-column `chronyc -c tracking` CSV line into a `ClockStatus`."""
    line = output.strip().splitlines()[0].strip() if output.strip() else ""
    if not line:
        return None
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 14:
        return None
    ref_id = parts[0]
    try:
        stratum = int(parts[2])
        system_time_s = float(parts[4])
        last_offset_s = float(parts[5])
    except ValueError:
        return None
    leap_status = parts[13]
    if not (math.isfinite(system_time_s) and math.isfinite(last_offset_s)):
        return None
    eff_offset_s = system_time_s if abs(system_time_s) >= abs(last_offset_s) else last_offset_s
    synced = 0 < stratum < 16 and leap_status.lower() != "not synchronised" and ref_id != "00000000"
    return ClockStatus(
        synchronized=synced,
        source="chronyc",
        offset_ms=round(eff_offset_s * 1000.0, 4),
        detail=f"stratum={stratum}, ref={ref_id}, leap={leap_status}",
    )


def parse_chronyc_human(output: str) -> ClockStatus | None:
    """Parse human-readable `chronyc tracking` output."""
    if not output.strip():
        return None
    leap_unsync = bool(re.search(r"Leap status\s*:\s*Not synchronised", output, re.IGNORECASE))
    m_off = re.search(r"Last offset\s*:\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*seconds", output)
    m_sys = re.search(r"System time\s*:\s*(\d+(?:\.\d+)?)\s*seconds\s*(fast|slow)", output)
    m_str = re.search(r"Stratum\s*:\s*(\d+)", output)
    if m_off is not None:
        offset_s = float(m_off.group(1))
    elif m_sys is not None:
        offset_s = float(m_sys.group(1)) * (1.0 if m_sys.group(2) == "fast" else -1.0)
    else:
        return None
    stratum = int(m_str.group(1)) if m_str else 16
    synced = (not leap_unsync) and (0 < stratum < 16)
    return ClockStatus(
        synchronized=synced,
        source="chronyc",
        offset_ms=round(offset_s * 1000.0, 4),
        detail=f"stratum={stratum}",
    )


def parse_ptp_pmc(output: str) -> ClockStatus | None:
    """Parse `pmc -u -b 0 'GET TIME_STATUS_NP'` output for master_offset (ns)."""
    if not output.strip():
        return None
    m_off = re.search(r"master_offset\s+([+-]?\d+)", output)
    m_gm = re.search(r"gmPresent\s+(true|false)", output, re.IGNORECASE)
    if not m_off:
        return None
    offset_ms = float(int(m_off.group(1))) / 1_000_000.0
    gm_present = (m_gm.group(1).lower() == "true") if m_gm else True
    return ClockStatus(
        synchronized=gm_present,
        source="ptp_pmc",
        offset_ms=round(offset_ms, 4),
        detail=f"gmPresent={gm_present}",
    )


class FixedClockProbe:
    """Deterministic clock probe for L0/L1 testing (`--clock-mode mock`)."""

    def __init__(
        self,
        offset_ms: float = 0.0,
        *,
        synchronized: bool = True,
        source: str = "mock-ptp",
        detail: str = "deterministic simulation clock",
    ) -> None:
        """Initialize fixed mock clock status."""
        self._status = ClockStatus(
            synchronized=synchronized,
            source=source,
            offset_ms=offset_ms if synchronized else None,
            detail=detail,
        )

    def read_status(self) -> ClockStatus:
        """Return configured fixed clock status."""
        return self._status


class SystemClockProbe:
    """Reads host Chrony or LinuxPTP `pmc` status; fails closed if neither is synchronized."""

    def read_status(self) -> ClockStatus:
        """Query system `chronyc` or `pmc` clock sync state."""
        for cmd, parser in (
            (["chronyc", "-c", "tracking"], parse_chronyc_csv),
            (["chronyc", "tracking"], parse_chronyc_human),
            (["pmc", "-u", "-b", "0", "GET TIME_STATUS_NP"], parse_ptp_pmc),
        ):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode == 0:
                st = parser(proc.stdout)
                if st is not None:
                    return st
        return ClockStatus(
            synchronized=False,
            source="none",
            offset_ms=None,
            detail="neither chronyc nor ptp pmc reported a synchronized clock",
        )


def verify_clock_offset(probe: ClockProbe, max_offset_ms: float) -> ClockStatus:
    """Fail-Closed verification that wall clock is synchronized within `max_offset_ms`."""
    if max_offset_ms <= 0.0 or not math.isfinite(max_offset_ms):
        raise ClockSyncError("max_clock_offset_ms must be > 0")
    st = probe.read_status()
    if not st.synchronized or st.offset_ms is None or not math.isfinite(st.offset_ms):
        raise ClockSyncError(f"clock not synchronized ({st.source}: {st.detail})")
    if abs(st.offset_ms) > max_offset_ms:
        raise ClockSyncError(
            f"clock offset {st.offset_ms:+.3f}ms exceeds max {max_offset_ms:.3f}ms ({st.source})"
        )
    return st
