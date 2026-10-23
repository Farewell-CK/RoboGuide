"""Actual HTTP/SQL Prepared takeover checks; no Node, device, model or simulator is created."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

Http = Callable[[str, str, str, bytes], tuple[int, bytes]]


def writer_table(connection: sqlite3.Connection) -> str:
    """Check the declared v1 marker and readonly view before targeting the actual writer."""
    assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    assert connection.execute(
        "SELECT version,schema FROM controller_storage_format"
    ).fetchall() == [(1, "roboguide.controller-storage/v1")]
    assert connection.execute(
        "SELECT type,sql FROM sqlite_master WHERE name='events'"
    ).fetchone() == ("view", "CREATE VIEW events AS SELECT * FROM controller_v1_events")
    assert connection.execute(
        "SELECT type FROM sqlite_master WHERE name='controller_v1_events'"
    ).fetchone() == ("table",)
    return "controller_v1_events"


def canonical(value: object) -> str:
    """Preserve the Rust canonical object bytes used by the original-upload digest contract."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def original_rows(database: Path) -> list[dict[str, Any]]:
    """Read bounded actual event envelopes independently without authority changes."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
        rows = sql.execute(
            "SELECT sequence,payload_schema,payload_json FROM events ORDER BY sequence LIMIT 16385"
        ).fetchall()
    if len(rows) > 16384:
        raise ValueError("actual recovery evidence exceeds its original scan bound")
    return [dict(sequence=row[0], schema=row[1], payload=json.loads(row[2])) for row in rows]


def stage(http: Http, origin: str, directory: Path) -> tuple[dict[str, Any], bytes]:
    """Fail original Completed SQL after actual Prepared quorum and CAS finalization."""
    upload = "actual-original-prepared"
    payload = os.urandom(192 * 1024 + 47)
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    assert (
        http(origin, "/v1/artifact-uploads", "POST", canonical({"upload_id": upload}).encode())[0]
        == 201
    )
    assert http(origin, f"/v1/artifact-uploads/{upload}/content", "POST", payload)[0] == 202
    database = directory / "controller.sqlite3"
    with closing(sqlite3.connect(database)) as sql:
        sql.execute(
            "CREATE TRIGGER original_prepared_completion_failure BEFORE INSERT ON "
            + writer_table(sql)
            + " "
            "WHEN NEW.payload_json LIKE '%\"ArtifactUploadTransition\"%' "
            'AND NEW.payload_json LIKE \'%"phase":"completed"%\' '
            "BEGIN SELECT RAISE(ABORT,'actual original completion failure'); END"
        )
        sql.commit()
    try:
        code, _ = http(
            origin,
            f"/v1/artifact-uploads/{upload}/finalize",
            "POST",
            canonical({"content_digest": checksum, "byte_size": len(payload)}).encode(),
        )
        assert code >= 500
    finally:
        with closing(sqlite3.connect(database)) as sql:
            sql.execute("DROP TRIGGER original_prepared_completion_failure")
            sql.commit()
    receipts = [
        row["payload"]["ArtifactUploadTransition"]["record"]
        for row in original_rows(database)
        if "ArtifactUploadTransition" in row["payload"]
        and row["payload"]["ArtifactUploadTransition"]["record"]["upload_id"] == upload
    ]
    assert [row["phase"] for row in receipts] == ["started", "prepared"]
    assert receipts[-1]["content_digest"] == checksum
    assert receipts[-1]["byte_size"] == len(payload)
    return receipts[-1], payload


def verify(
    http: Http,
    origin: str,
    directory: Path,
    original: dict[str, Any],
    payload: bytes,
    source_owner: str,
    destination_owner: str,
) -> dict[str, Any]:
    """Recover on the real new child and verify read-only retries after completion failure."""
    underlying = http
    observations: list[dict[str, Any]] = []

    def observed_http(origin: str, path: str, method: str, body: bytes) -> tuple[int, bytes]:
        """Keep actual complete HTTP timing and unavailable outcomes beside original SQL proof."""
        started = time.monotonic()
        observation: dict[str, Any] = {"path": path, "method": method, "response_status": None}
        try:
            result = underlying(origin, path, method, body)
            observation["response_status"] = result[0]
            return result
        finally:
            observation["actual_seconds"] = time.monotonic() - started
            observations.append(observation)
            (directory / "recovery-http-observations.json").write_text(
                json.dumps(observations, indent=2) + "\n"
            )

    http = observed_http
    checksum = original["content_digest"]
    blob = directory / "artifacts" / "objects-v1" / "sha256" / checksum[7:9] / checksum[7:]
    assert not blob.exists()
    assert http(origin, "/v1/artifacts/" + checksum[7:], "GET", b"")[0] != 200
    path = f"/v1/artifact-uploads/{original['upload_id']}/recover"
    command: dict[str, Any] = {
        "schema": "roboguide.artifact-upload-recovery-command/v0.1",
        "command_id": "actual-prepared-takeover",
        "original_upload_digest": "sha256:"
        + hashlib.sha256(canonical(original).encode()).hexdigest(),
        "budget_ms": 120000,
    }
    code, raw = http(origin, path, "POST", canonical(command).encode())
    assert code == 200, (code, raw.decode())
    result = json.loads(raw)
    assert result["status"] == "completed" and not result["replayed"]
    recovered = result["record"]
    assert recovered["upload"] == original
    assert (
        json.loads(recovered["completion"]["reader_receipt_json"])["reader_id"]
        == recovered["reader_id"]
    )
    assert recovered["source_owner_id"] == source_owner
    assert recovered["destination_owner_id"] == destination_owner != source_owner
    assert blob.read_bytes() == payload
    assert http(origin, "/v1/artifacts/" + checksum[7:], "GET", b"") == (200, payload)
    before = original_rows(directory / "controller.sqlite3")
    code, raw = http(origin, path, "POST", canonical(command).encode())
    assert code == 200
    assert json.loads(raw)["record"] == recovered and json.loads(raw)["replayed"]
    assert original_rows(directory / "controller.sqlite3") == before
    changed = {**command, "budget_ms": command["budget_ms"] + 1}
    assert http(origin, path, "POST", canonical(changed).encode())[0] == 409
    database = directory / "controller.sqlite3"
    with closing(sqlite3.connect(database)) as sql:
        sql.execute(
            "CREATE TRIGGER recovery_completion_failure BEFORE INSERT ON " + writer_table(sql) + " "
            "WHEN NEW.payload_schema='domain.EventPayload.json/v32' "
            "AND NEW.payload_json NOT LIKE '%\"completion\":null%' "
            "BEGIN SELECT RAISE(ABORT,'actual recovery completion failure'); END"
        )
        sql.commit()
    failed_command = {**command, "command_id": "actual-recovery-unknown"}
    try:
        code, _ = http(origin, path, "POST", canonical(failed_command).encode())
        assert code >= 500
    finally:
        with closing(sqlite3.connect(database)) as sql:
            sql.execute("DROP TRIGGER recovery_completion_failure")
            sql.commit()
    before = original_rows(database)
    assert http(origin, path, "POST", canonical(failed_command).encode())[0] == 503
    observation_path = (
        f"/v1/artifact-uploads/{original['upload_id']}/recoveries/{failed_command['command_id']}"
    )
    code, raw = http(origin, observation_path, "GET", b"")
    assert code == 200, (code, raw.decode())
    uncertain = json.loads(raw)
    assert uncertain["committed_local"] and not uncertain["audit_persisted"]
    assert not uncertain["write_status"]["write_enabled"]
    assert uncertain["status"] == "unknown"
    assert uncertain["record"]["completion"] is None
    assert original_rows(database) == before
    source_history = [
        row["payload"]["ArtifactUploadTransition"]["record"]
        for row in before
        if "ArtifactUploadTransition" in row["payload"]
        and row["payload"]["ArtifactUploadTransition"]["record"]["upload_id"]
        == original["upload_id"]
    ]
    assert source_history[-1] == original and all(
        row["phase"] != "completed" for row in source_history
    )
    return {
        "original_prepared": original,
        "recovered": recovered,
        "unknown": uncertain["record"],
        "actual_sql_completion_failure": True,
        "original_unchanged": True,
        "retries_read_only": True,
    }


def release(
    http: Http,
    origin: str,
    directory: Path,
    original: dict[str, Any],
    source_owner: str,
    destination_owner: str,
    unknown: dict[str, Any],
) -> dict[str, Any]:
    """Release the original upload after restart, retaining origins and Unknown history."""
    assert source_owner != destination_owner
    path = f"/v1/artifact-uploads/{original['upload_id']}"
    query = path + "/recoveries/" + unknown["command_id"]
    code, raw = http(origin, query, "GET", b"")
    assert code == 200 and json.loads(raw)["record"] == unknown, (code, raw.decode())
    before = original_rows(directory / "controller.sqlite3")
    code, raw = http(origin, path, "DELETE", b"")
    assert code == 200 and json.loads(raw)["cas_deleted"] is False, (code, raw.decode())
    after = original_rows(directory / "controller.sqlite3")
    assert len(after) == len(before) + 1 and after[:-1] == before
    abandoned = {**original, "phase": "abandoned"}
    assert after[-1]["payload"] == {"ArtifactUploadTransition": {"record": abandoned}}
    with closing(
        sqlite3.connect((directory / "replica.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        current = json.loads(
            sql.execute("SELECT value FROM replica_v1_meta WHERE key='content_state'").fetchone()[0]
        )["candidate"]
    assert current["source_writer"]["owner"] == destination_owner
    original_key = {
        "source_owner": source_owner,
        "content_digest": original["content_digest"],
        "byte_size": original["byte_size"],
    }
    assert original_key not in current["inputs"]
    binding = current["origins"][current["batch"]["events"][-1]["event_id"]]
    assert binding == original_key
    code, raw = http(origin, path, "DELETE", b"")
    assert code == 200 and original_rows(directory / "controller.sqlite3") == after
    code, raw = http(origin, query, "GET", b"")
    assert code == 200 and json.loads(raw)["record"] == unknown
    return {
        "original": original,
        "abandoned": abandoned,
        "source_owner": source_owner,
        "destination_owner": destination_owner,
        "retained_origin": binding,
        "current_inputs": current["inputs"],
        "unknown_unchanged": True,
        "repeat_created_no_domain_event": True,
        "cas_deleted": False,
    }
