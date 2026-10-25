"""Verify online Archive and lost replies on owned Controller/Replica processes."""

from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

Request = Callable[..., tuple[int, bytes]]


def snapshot(directory: Path) -> dict[str, Any]:
    """Read committed SQL independently of HTTP status."""
    with closing(
        sqlite3.connect((directory / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        sql.execute("BEGIN")
        checkpoint = sql.execute(
            "SELECT event_sequence,schema,checkpoint_json "
            "FROM controller_checkpoint WHERE singleton=1"
        ).fetchone()
        assert checkpoint is not None
        floor = sql.execute(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'"
        ).fetchone()
        marker = sql.execute(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'"
        ).fetchone()
        names = {row[0] for row in sql.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = (
            dict(sql.execute("SELECT command_id,record_json FROM coordinated_archive_operations"))
            if "coordinated_archive_operations" in names
            else {}
        )
        return {
            "checkpoint": list(checkpoint),
            "floor": int(floor[0]) if floor else 0,
            "marker": marker[0],
            "operations": records,
        }


def _wait_original(request: Request, origin: str, command: str) -> dict[str, Any]:
    """Reconcile only through bounded readonly observations; a lost POST is never repeated."""
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        code, raw = request(origin, "/v1/maintenance/coordinated-archive/operations")
        if code == 200:
            decoded: dict[str, Any] = json.loads(raw)
            rows: list[dict[str, Any]] = decoded["operations"]
            for row in rows:
                if row["intent"]["request"]["command_id"] == command:
                    if row["completed_ms"] is not None:
                        return row
        time.sleep(0.1)
    raise ValueError("actual original Archive did not obtain a readonly completion proof")


def _package(row: dict[str, Any], configs: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Independently verify actual exact same-owner bytes in every receiving member's real CAS."""
    key = row["intent"]["package"]
    deadline = time.monotonic() + 30
    observed: dict[str, Any] = {}
    while time.monotonic() < deadline:
        for member, config in configs.items():
            root = Path(config["content"]["root"])
            path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
            if path.is_file():
                raw = path.read_bytes()
                assert len(raw) == key["byte_size"]
                assert "sha256:" + hashlib.sha256(raw).hexdigest() == key["content_digest"]
                package = json.loads(raw)
                assert package["manifest"] == row["intent"]["manifest"]
                assert package["authority_id"] == row["intent"]["authority_id"]
                observed[str(member)] = {
                    "path": str(path),
                    "digest": key["content_digest"],
                    "byte_size": len(raw),
                }
        if len(observed) == len(configs):
            return observed
        time.sleep(0.1)
    raise ValueError("actual Archive package is absent from a declared original member Root")


def exercise(request: Request, configs: dict[int, dict[str, Any]], member: int) -> dict[str, Any]:
    """Verify original history, readonly duplicates and one discarded reply."""
    config = configs[member]
    directory = Path(config["storage_directory"])
    before = snapshot(directory)
    assert before["checkpoint"][0] > before["floor"]
    body = {
        "schema": "roboguide.coordinated-archive-command/v0.1",
        "command_id": "actual-online-original-archive",
        "end_sequence": before["checkpoint"][0],
        "budget_ms": 120000,
    }
    code, raw = request(
        config["http_address"],
        "/v1/maintenance/coordinated-archive",
        "POST",
        json.dumps(body).encode(),
        timeout_seconds=150,
    )
    assert code == 200, raw
    row = json.loads(raw)
    assert row["completed_ms"] is not None and row["abort"] is None
    after = snapshot(directory)
    assert after["floor"] == before["checkpoint"][0]
    assert after["checkpoint"] == before["checkpoint"]
    assert after["marker"] == "roboguide.archive-operations/v0.3"
    replicated = _package(row, configs)
    assert (
        request(
            config["artifact_address"],
            "/v1/artifacts/" + row["intent"]["package"]["content_digest"][7:],
        )[0]
        == 403
    )
    duplicate = request(
        config["http_address"],
        "/v1/maintenance/coordinated-archive",
        "POST",
        json.dumps(body).encode(),
    )
    assert duplicate == (200, raw)
    assert snapshot(directory) == after
    # Create one genuine later event through normal Artifact admission before another exact cutoff.
    code, later = request(
        config["artifact_address"],
        "/v1/artifact-uploads",
        "POST",
        json.dumps({"upload_id": "actual-after-archive-original-upload"}).encode(),
    )
    assert code == 201, later
    original = snapshot(directory)
    body2 = dict(
        body, command_id="actual-online-lost-archive-reply", end_sequence=original["checkpoint"][0]
    )
    # This maintained loss case uses the actual declared loopback HTTP profile. TLS compositions use
    # their separate actual relay gate; no TLS connection is replaced by an unverified raw socket.
    assert config.get("operator_tls") is None
    host, port = config["http_address"].rsplit(":", 1)
    connection = http.client.HTTPConnection(host, int(port), timeout=150)
    try:
        connection.request(
            "POST",
            "/v1/maintenance/coordinated-archive",
            json.dumps(body2).encode(),
            {"Content-Type": "application/json"},
        )
        # Deliberately discard the original response after sending one complete real request.
        connection.close()
    finally:
        connection.close()
    lost = _wait_original(request, config["http_address"], body2["command_id"])
    lost_packages = _package(lost, configs)
    current = snapshot(directory)
    assert current["checkpoint"] == original["checkpoint"]
    assert current["floor"] == body2["end_sequence"]
    return {
        "first_original": row,
        "first_member_packages": replicated,
        "duplicate_readonly": True,
        "lost_reply_original": lost,
        "lost_reply_posts": 1,
        "lost_reply_member_packages": lost_packages,
        "checkpoint_unchanged_by_maintenance": True,
        "actual_new_event_after_archive": True,
    }


def sql_failure_and_transfer(
    request: Request,
    configs: dict[int, dict[str, Any]],
    current: Callable[..., tuple[int, dict[str, Any]]],
    supervisor: Path,
    active_members: list[int],
) -> dict[str, Any]:
    """Preserve final-SQL failure through original child drain and a new epoch."""
    member, status = current()
    config = configs[member]
    directory = Path(config["storage_directory"])
    before = snapshot(directory)
    if before["checkpoint"][0] == before["floor"]:
        code, raw = request(
            config["artifact_address"],
            "/v1/artifact-uploads",
            "POST",
            json.dumps({"upload_id": "actual-before-archive-sql-fault"}).encode(),
        )
        assert code == 201, raw
        before = snapshot(directory)
    body = {
        "schema": "roboguide.coordinated-archive-command/v0.1",
        "command_id": "actual-archive-final-sql-failure",
        "end_sequence": before["checkpoint"][0],
        "budget_ms": 120000,
    }
    database = directory / "controller.sqlite3"
    # The real SQLite fault fires only on the final new floor; the original pending transaction
    # must first complete normal metadata/content quorum. No application test hook is introduced.
    with closing(sqlite3.connect(database)) as sql:
        sql.execute(
            "CREATE TRIGGER actual_archive_final_failure BEFORE INSERT ON event_log_metadata "
            "WHEN NEW.key='history_floor' AND NEW.value='"
            + str(body["end_sequence"])
            + "' BEGIN SELECT RAISE(ABORT,'actual original Archive final SQL failure'); END"
        )
        sql.commit()
    try:
        code, raw = request(
            config["http_address"],
            "/v1/maintenance/coordinated-archive",
            "POST",
            json.dumps(body).encode(),
            timeout_seconds=150,
        )
        assert code != 200, raw
    finally:
        with closing(sqlite3.connect(database)) as sql:
            sql.execute("DROP TRIGGER actual_archive_final_failure")
            sql.commit()
    failed = snapshot(directory)
    assert failed["checkpoint"] == before["checkpoint"] and failed["floor"] == before["floor"]
    original_raw = failed["operations"][body["command_id"]]
    original = json.loads(original_raw)
    assert original["completed_ms"] is None and original["abort"] is None
    key = original["intent"]["package"]
    package = (
        directory
        / "artifacts/objects-v1/sha256"
        / key["content_digest"][7:9]
        / key["content_digest"][7:]
    )
    actual = package.read_bytes()
    assert (
        len(actual) == key["byte_size"]
        and "sha256:" + hashlib.sha256(actual).hexdigest() == key["content_digest"]
    )
    target = next(value for value in active_members if value != member)
    path = directory.parent / f"configuration-{member}.json"
    transfer = subprocess.run(
        [
            str(supervisor),
            "--admin",
            str(path),
            "--leader",
            str(member),
            "--transfer-leader",
            str(target),
            "--transfer-id",
            "actual-archive-unknown-source-drain",
        ],
        check=False,
        capture_output=True,
        timeout=45,
    )
    assert transfer.returncode == 0, transfer.stderr
    successor, after = current(status["child_pid"])
    assert successor == target and after["epoch"]["term"] > status["epoch"]["term"]
    next_config = configs[successor]
    retained = snapshot(Path(next_config["storage_directory"]))
    assert json.loads(retained["operations"][body["command_id"]]) == original
    retry_code, retry = request(
        next_config["http_address"],
        "/v1/maintenance/coordinated-archive",
        "POST",
        json.dumps(body).encode(),
    )
    assert retry_code == 200 and json.loads(retry) == original
    assert snapshot(Path(next_config["storage_directory"])) == retained
    abort_code, abort = request(
        next_config["http_address"],
        "/v1/maintenance/coordinated-archive/abort",
        "POST",
        json.dumps(
            {
                "schema": "roboguide.coordinated-archive-abort/v0.1",
                "original_command_id": body["command_id"],
            }
        ).encode(),
    )
    assert abort_code == 200, abort
    abandoned = json.loads(abort)
    assert abandoned["intent"] == original["intent"] and abandoned["completed_ms"] is None
    assert abandoned["abort"] is not None
    # A new explicitly identified operation may archive current history; the abandoned first ID
    # remains Unknown and never receives a replacement duration, package or success receipt.
    replacement = dict(
        body,
        command_id="actual-archive-after-original-abandonment",
        end_sequence=retained["checkpoint"][0],
    )
    next_code, next_raw = request(
        next_config["http_address"],
        "/v1/maintenance/coordinated-archive",
        "POST",
        json.dumps(replacement).encode(),
        timeout_seconds=150,
    )
    assert next_code == 200, next_raw
    assert json.loads(next_raw)["completed_ms"] is not None
    final = snapshot(Path(next_config["storage_directory"]))
    assert json.loads(final["operations"][body["command_id"]]) == abandoned
    return {
        "actual_sql_rejection": raw.decode(errors="replace"),
        "original_unknown": original,
        "actual_transfer": json.loads(transfer.stdout),
        "original_source_child_pid": status["child_pid"],
        "successor": after,
        "cross_owner_duplicate_readonly": True,
        "abandoned_original": abandoned,
        "new_original_completed": json.loads(next_raw),
    }
