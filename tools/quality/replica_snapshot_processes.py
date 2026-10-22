"""Exercise actual original Snapshot producers in independently spawned maintained storage code."""

from __future__ import annotations

import json
import signal
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import Processes, digest, save


def run(
    output: Path,
    snapshot_probe: Path,
    content_probe: Path,
    source_sql: Path,
    source_cas: Path,
    profile: str,
    first_member: int,
    second_member: int,
) -> dict[str, Any]:
    """Observe actual Snapshot pins, independent bytes, original reader death and retirement."""
    output.mkdir(mode=0o700)
    workers = Processes(snapshot_probe, output)
    binary = digest(snapshot_probe)
    content_binary = digest(content_probe)
    members: list[dict[str, Any]] = [
        {"root": str(output / name), "profile": profile, "member": member}
        for name, member in [("first", first_member), ("second", second_member)]
    ]
    result: dict[str, Any] = {
        "schema": "roboguide.original-snapshot-storage-process-check/v0.1",
        "scope": "direct storage/Snapshot code; no network quorum or leader authority",
        "passed": False,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "snapshot_probe_sha256": binary,
        "content_probe_sha256": content_binary,
    }
    try:
        process = workers.start(
            {
                "action": "exercise",
                "source_sql": str(source_sql),
                "source_cas": str(source_cas),
                "first": members[0],
                "second": members[1],
                "hold": True,
            },
            "actual-snapshot-producers",
        )
        ready = workers.ready(process)
        if (
            ready["nodes"] != 0
            or ready["devices"] != 0
            or not ready["missing_bytes_refused"]
            or not ready["stale_snapshot_refused"]
        ):
            raise ValueError("actual original Snapshot producer observations are incomplete")
        for member in members:
            workers.refuse(
                {"action": "inspect", "member": member},
                f"live-{member['member']}-writer-refused",
                "still active",
            )
        process.kill()
        original_wait, _ = workers.wait(process)
        if original_wait != -signal.SIGKILL:
            raise ValueError("actual original Snapshot process lacks its native killed wait")
        observations = []
        retired = []
        for member in members:
            observed = workers.run(
                {"action": "inspect", "member": member}, f"reopen-{member['member']}"
            )
            if (
                not observed["actual_bytes"]
                or observed["batch_digest"] != ready["first"]["batch_digest"]
            ):
                raise ValueError("actual accepted metadata/content changed across process death")
            root = Path(member["root"]) / "content"
            with closing(sqlite3.connect(root / "content.sqlite3")) as database:
                readers = [
                    json.loads(raw)
                    for (raw,) in database.execute(
                        "SELECT record FROM content_readers ORDER BY reader_id"
                    )
                ]
                version = database.execute("PRAGMA user_version").fetchone()[0]
            if version != 2:
                raise ValueError("actual Snapshot scopes lack their explicit new layout fence")
            for reader in readers:
                if reader["receipt"] is None and reader["process_end"] is None:
                    if reader["intent"].get("retention_only") is not True:
                        raise ValueError("actual Snapshot scope became a fabricated payload reader")
                    request = {
                        "action": "retire-reader",
                        "store": {
                            "root": str(root),
                            "profile": member["profile"],
                            "member": member["member"],
                        },
                        "reader_id": reader["intent"]["reader_id"],
                    }
                    code, closed = workers.wait(
                        workers.start(request, "original-snapshot-reader-end", content_probe)
                    )
                    if (
                        code != 0
                        or closed["result"]["reader"]["receipt"] is not None
                        or closed["result"]["reader"]["process_end"] is None
                    ):
                        raise ValueError(
                            "process-end observation fabricated original byte evidence"
                        )
                    retired.append(closed["result"]["reader"])
            after = workers.run(
                {"action": "inspect", "member": member}, f"after-reader-end-{member['member']}"
            )
            for item in after["pins"]:
                kinds = [pin["kind"] for pin in item["references"]]
                if "reader" in kinds or kinds.count("snapshot") != 1 or kinds.count("view") != 1:
                    raise ValueError(
                        f"independent original producers lost exact ownership: {kinds}"
                    )
            observations.append(after)
        if len(retired) != 2:
            raise ValueError("old/current Snapshot handles lost their independent Unknown readers")
        damaged = Path(members[1]["root"]) / "replica.sqlite3"
        with closing(sqlite3.connect(damaged)) as database:
            database.execute("DELETE FROM replica_v1_meta WHERE key='content_state'")
            database.commit()
        workers.refuse(
            {"action": "inspect", "member": members[1]},
            "missing-complete-state-head-refused",
            "root/head",
        )
        if digest(snapshot_probe) != binary or digest(content_probe) != content_binary:
            raise ValueError("actual native process executables changed during observation")
        result.update(
            passed=True,
            original=ready,
            reopened=observations,
            original_reader_ends=retired,
            original_process_wait=original_wait,
            missing_original_head_refused=True,
        )
    finally:
        workers.cleanup()
        result["processes"] = [record for _, record in workers.owned]
        save(output / "result.json", result)
    return result
