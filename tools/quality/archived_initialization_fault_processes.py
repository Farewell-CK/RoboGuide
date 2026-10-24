"""Preserve actual interrupted initialization and original Unknown through native restart."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from check_network_content_processes import Actor, sql_identity
from replica_parent_adoption_processes import PayloadReadBarrier


def rows(path: Path) -> dict[str, str]:
    """Read exact original member-local evidence without sampling or renewing its process budget."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        return dict(sql.execute("SELECT command_id,record FROM content_initializations"))


def interrupt(
    actor: Actor,
    restart: Callable[[], Actor],
    request: dict[str, Any],
    parent: Path,
    barrier_path: Path,
    parents: list[Path],
) -> dict[str, Any]:
    """Kill only the actual source after durable capture and native attempted payload open."""
    if actor.process.stdin is None:
        raise ValueError("actual source control pipe is absent")
    with PayloadReadBarrier(barrier_path) as barrier:
        actor.process.stdin.write(json.dumps(request).encode() + b"\n")
        actor.process.stdin.flush()
        deadline = time.monotonic() + 20
        original = {}
        while time.monotonic() < deadline:
            original = rows(parent)
            if (
                barrier.signals
                and original
                and all(json.loads(raw)["captured"] is not None for raw in original.values())
            ):
                break
            time.sleep(0.01)
        else:
            raise ValueError("source did not reach the original capture/native payload barrier")
        source_pid = actor.process.pid
        actor.record["interrupted_original_request"] = request
        actor.stop(kill=True)
        signal_count = len(barrier.signals)
        assert signal_count > 0
        for raw in original.values():
            value = json.loads(raw)
            assert value["completion"] is None
            assert value["intent"]["budget"]["pid"] == source_pid
        replacement = restart()
        observed = replacement.ask(request)["original_status"]
        assert observed["source_captured"] is True
        assert observed["original_receipt"] is None and observed["current_local_anchor"] is None
        assert rows(parent) == original and len(barrier.signals) == signal_count
        assert all(sql_identity(path)[1]["content_state"] is None for path in parents)
        return {
            "actual_source_pid": source_pid,
            "actual_wait": actor.record["returncode"],
            "kernel_payload_open_signals": signal_count,
            "same_id_reopened_source": False,
            "original_rows": original,
            "original_status_after_restart": observed,
        }


def miss_initialization(
    actor: Actor,
    receiver: Actor,
    request: dict[str, Any],
    parent: Path,
    barrier_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stop one real receiver after all-voter admission but before original source payload open."""
    if actor.process.stdin is None:
        raise ValueError("actual source control pipe is absent")
    with PayloadReadBarrier(barrier_path) as barrier:
        actor.process.stdin.write(json.dumps(request).encode() + b"\n")
        actor.process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            original = rows(parent)
            if (
                barrier.signals
                and original
                and all(json.loads(raw)["captured"] is not None for raw in original.values())
            ):
                break
            time.sleep(0.01)
        else:
            raise ValueError("actual source capture/native payload barrier was not reached")
        receiver.stop()
        receiver_config = json.loads(Path(receiver.record["config"]).read_bytes())
        assert sql_identity(Path(receiver_config["parent_sql"]))[1]["content_state"] is None
        observed = {
            "actual_receiver_pid": receiver.process.pid,
            "actual_wait": receiver.record["returncode"],
            "kernel_payload_open_signals": len(barrier.signals),
        }
    response = actor.receive()
    actor.record["exchanges"].append({"request": request, "response": response})
    if "result" not in response:
        raise ValueError(f"actual initialization after receiver loss refused: {response}")
    return response["result"], observed
