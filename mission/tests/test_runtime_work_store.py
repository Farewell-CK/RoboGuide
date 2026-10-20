"""Actual SQL, inode leases and OS-clock checks; all remote outcomes are labelled unit fixtures."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from mission.runtime_work_store import RuntimeWorkError, RuntimeWorkStore, kernel_time


def test_effect_intent_precedes_io_and_original_response_survives_restart(tmp_path: Path) -> None:
    """An independent SQLite reader observes the intent before the scripted operation starts."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    store.admit("work", {"source": "labelled-unit-input"}, {"code": "original"}, 10000)
    calls = []

    def operation(timeout: float) -> dict[str, Any]:
        """Inspect actual committed SQL before returning a clearly synthetic transport result."""
        assert 0 < timeout <= 10
        with sqlite3.connect(path) as connection:
            row = connection.execute("SELECT status,request_json FROM effects").fetchone()
        assert row == ("pending", '{"fixture":"request"}')
        calls.append(1)
        return {"fixture": "response", "actual_device_evidence": False}

    response = store.perform("work", "fixture-effect", {"fixture": "request"}, operation)
    before = store.remaining("work")
    original = store.effects("work")[0]
    assert original["finished_kernel"] is not None
    store.close()
    restored = RuntimeWorkStore(path)
    try:
        assert (
            restored.perform("work", "fixture-effect", {"fixture": "request"}, operation)
            == response
        )
        assert calls == [1]
        assert restored.remaining("work") <= before
        assert restored.effects("work")[0] == original
        with pytest.raises(RuntimeWorkError, match="input changed"):
            restored.perform("work", "fixture-effect", {"fixture": "different"}, operation)
        assert calls == [1]
    finally:
        restored.close()


def test_uncertain_effect_never_repeats_and_read_observation_stays_separate(tmp_path: Path) -> None:
    """Keep interrupted outcomes uncertain across restart and separate observation."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    identity = {"code": "original"}
    store.admit("work", {"source": "unit"}, identity, 10000)
    calls = []

    def interrupted(timeout: float) -> dict[str, Any]:
        """Model a lost response after one scripted effect without inventing device progress."""
        calls.append(timeout)
        raise TimeoutError("labelled-unit-transport-interruption")

    request = {"fixture": "original"}
    with pytest.raises(TimeoutError):
        store.perform("work", "candidate", request, interrupted)
    store.close()
    restored = RuntimeWorkStore(path)
    try:
        with pytest.raises(RuntimeWorkError, match="uncertain"):
            restored.perform("work", "candidate", request, interrupted)
        assert len(calls) == 1
        result = {"outcome": "unit-observation-only"}
        restored.retain_observation(
            "work", "candidate", request, {"fixture": "independent-read"}, result
        )
        assert restored.perform("work", "candidate", request, interrupted) == result
        assert len(calls) == 1
        assert restored.effects("work")[0]["status"] == "uncertain"
        assert restored.effects("work")[0]["response"] is None
        restored.retain_observation("work", "candidate", request, {"fixture": "later-read"}, result)
        with pytest.raises(RuntimeWorkError, match="observation changed"):
            restored.retain_observation("work", "candidate", request, {}, {"different": True})
    finally:
        restored.close()


def test_database_wait_cannot_renew_original_effect_budget(tmp_path: Path) -> None:
    """Actual SQLite write contention outlives the original budget and prevents any callback."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    store.admit("work", {}, {}, 100)
    ready = threading.Event()

    def blocker() -> None:
        """Hold an actual independent writer transaction past the original deadline."""
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(0.25)
            connection.commit()

    thread = threading.Thread(target=blocker)
    thread.start()
    assert ready.wait(2)
    calls = []

    def operation(timeout: float) -> dict[str, Any]:
        """Record a forbidden callback if the original deadline were incorrectly refreshed."""
        calls.append(timeout)
        return {}

    try:
        with pytest.raises(RuntimeWorkError, match="expired"):
            store.perform("work", "after-sql-wait", {}, operation)
        assert calls == []
        assert store.remaining("work") == 0
    finally:
        thread.join(3)
        store.close()
    assert not thread.is_alive()


def test_original_input_configuration_and_inode_cannot_be_replaced(tmp_path: Path) -> None:
    """A hardlink cannot obtain another writer and later config/source changes fail closed."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    try:
        store.admit("work", {"source": 1}, {"config": 1}, 1000)
        alias = tmp_path / "alias.sqlite3"
        os.link(path, alias)
        with pytest.raises(BlockingIOError):
            RuntimeWorkStore(alias)
        with pytest.raises(RuntimeWorkError, match="configuration"):
            store.original("work", {"config": 2})
        with pytest.raises(RuntimeWorkError, match="source/configuration"):
            store.admit("work", {"source": 2}, {"config": 1}, 1000)
        path.rename(tmp_path / "original.sqlite3")
        path.touch()
        with pytest.raises(RuntimeWorkError, match="inode changed"):
            store.remaining("work")
    finally:
        store.close()


def test_admission_keeps_clock_before_source_read_and_sql_wait(tmp_path: Path) -> None:
    """Actual source/SQL delay cannot move an original work budget to a later admission time."""
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeWorkStore(path)
    issued = kernel_time()
    ready = threading.Event()

    def blocker() -> None:
        """Keep the real writer occupied beyond the original short source budget."""
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(0.2)
            connection.commit()

    thread = threading.Thread(target=blocker)
    thread.start()
    assert ready.wait(2)
    try:
        store.admit("work", {"source": "unit-before-delay"}, {}, 100, issued)
        assert store.remaining("work") == 0
        with sqlite3.connect(path) as connection:
            import json

            assert (
                json.loads(connection.execute("SELECT kernel_json FROM work").fetchone()[0])
                == issued
            )
    finally:
        thread.join(3)
        store.close()
