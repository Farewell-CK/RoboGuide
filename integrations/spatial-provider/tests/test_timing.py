"""Clock rollback/reboot fences for once-only native file computation."""

from __future__ import annotations

import pytest
from roboguide_spatial_provider import timing


def test_wall_rollback_cannot_renew_original_native_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpired audit wall timestamp cannot overrule an elapsed original monotonic deadline."""
    intent = {**timing.freeze(10), "deadline_ms": 20_000}
    original = intent["admitted_monotonic_ns"]
    monkeypatch.setattr(
        "roboguide_spatial_provider.timing.time.monotonic_ns", lambda: original + 9_000_000_000
    )
    assert timing.remaining(intent, 1, 10) == 1.0
    monkeypatch.setattr(
        "roboguide_spatial_provider.timing.time.monotonic_ns", lambda: original + 10_000_000_001
    )
    assert timing.remaining(intent, 0, 10) == 0.0
    proof = {"clock_id": intent["clock_id"], "computed_monotonic_ns": original + 10_000_000_001}
    assert timing.completed_late(intent, proof) is True


def test_restart_clock_change_refuses_work_but_preserves_original_readonly_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new host clock cannot start old work or renew retained computation evidence."""
    intent = {**timing.freeze(10), "deadline_ms": 20_000}
    proof = {
        "clock_id": intent["clock_id"],
        "computed_monotonic_ns": intent["admitted_monotonic_ns"] + 1000,
    }
    monkeypatch.setattr(timing, "clock_domain", lambda: "sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="observation only"):
        timing.remaining(intent, 0, 10)
    assert timing.completed_late(intent, proof) is False
    with pytest.raises(ValueError, match="contradictory"):
        timing.completed_late(intent, {**proof, "clock_id": "another-clock"})
    assert timing.completed_late({"deadline_ms": 20000}, {"computed_at_ms": 10000}) is None
    with pytest.raises(ValueError, match="historical"):
        timing.completed_late({"deadline_ms": 20000}, proof)
