"""Check unpublished Archive cleanup using actual mTLS, SQL and OS writer leases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import ssl
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save


def run(
    directory: Path,
    operator: str,
    artifact: str,
    admin: ssl.SSLContext,
    viewer: ssl.SSLContext,
    call: Callable[..., tuple[int, bytes]],
    restart: Callable[[], None],
    published_id: str,
) -> dict[str, Any]:
    """Use actual storage effects and SQL failures with zero Nodes/devices."""
    database = directory / "controller/events.sqlite3"
    status, raw = call(
        operator, admin, "GET", "/v1/maintenance/archive/operations?command_id=" + published_id
    )
    assert status == 200, raw
    published = json.loads(raw)
    forbidden = {
        "schema": "roboguide.archive-abort-command/v0.1",
        "command_id": "never-abort-published",
        "original_command_id": published_id,
        "original_intent_digest": published["original_intent_digest"],
        "budget_ms": 30_000,
    }
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", forbidden)[0] == 409
    payload = (directory / "source-identity.json").read_bytes()
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    upload = "original-cleanup-file"
    assert call(artifact, admin, "POST", "/v1/artifact-uploads", {"upload_id": upload})[0] == 201
    assert call(artifact, admin, "PUT", f"/v1/artifact-uploads/{upload}/content", payload)[0] == 202
    assert (
        call(
            artifact,
            admin,
            "POST",
            f"/v1/artifact-uploads/{upload}/finalize",
            {"content_digest": checksum, "byte_size": len(payload)},
        )[0]
        == 201
    )
    cutoff = json.loads(call(operator, admin, "GET", "/v1/maintenance/archive")[1])[
        "latest_sequence"
    ]
    original_id = "original-interrupted-archive"
    command = {
        "schema": "roboguide.archive-command/v0.2",
        "command_id": original_id,
        "end_sequence": cutoff,
        "budget_ms": 60_000,
    }
    root = (
        directory
        / "archive/archives-v1"
        / hashlib.sha256(published["request"]["authority_id"].encode()).hexdigest()
    )
    partial = root / ("archive-" + hashlib.sha256(original_id.encode()).hexdigest())
    # An actual pre-existing managed directory causes create_new to refuse after durable admission.
    partial.mkdir()
    assert call(operator, admin, "POST", "/v1/maintenance/archive", command)[0] == 503
    status, raw = call(
        operator, admin, "GET", "/v1/maintenance/archive/operations?command_id=" + original_id
    )
    assert status == 200
    first = json.loads(raw)
    assert first["publication_receipt"] is None and not first["original_abort_confirmed"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        copy_before = connection.execute(
            "SELECT intent_json,state,receipt_json FROM event_archive_operations "
            "WHERE command_id=?",
            (original_id,),
        ).fetchone()
    restart()
    assert call(operator, admin, "POST", "/v1/maintenance/archive", command)[0] == 503
    assert not (partial / "publication.json").exists()
    expired = {
        "schema": "roboguide.archive-abort-command/v0.1",
        "command_id": "expired-original-cleanup",
        "original_command_id": original_id,
        "original_intent_digest": first["original_intent_digest"],
        "budget_ms": 1,
    }
    assert call(operator, viewer, "POST", "/v1/maintenance/archive/abort", expired)[0] == 403
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", expired)[0] == 503
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", expired)[0] == 503
    with closing(sqlite3.connect(database)) as connection, connection:
        expired_row = connection.execute(
            "SELECT intent_json,receipt_json FROM event_archive_aborts WHERE command_id=?",
            (expired["command_id"],),
        ).fetchone()
        assert expired_row is not None and expired_row[1] is None
        connection.execute(
            "CREATE TRIGGER abort_receipt_fault BEFORE UPDATE OF receipt_json "
            "ON event_archive_aborts BEGIN SELECT RAISE(ABORT,"
            "'actual abort receipt SQL fault'); END"
        )
    cleanup = expired | {"command_id": "original-current-cleanup", "budget_ms": 30_000}
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", cleanup)[0] == 503
    with closing(sqlite3.connect(database)) as connection, connection:
        pending = connection.execute(
            "SELECT intent_json,receipt_json FROM event_archive_aborts WHERE command_id=?",
            (cleanup["command_id"],),
        ).fetchone()
        assert pending is not None and pending[1] is None
        connection.execute("DROP TRIGGER abort_receipt_fault")
    tombstone = partial / (
        "abort-" + hashlib.sha256(cleanup["command_id"].encode()).hexdigest() + ".json"
    )
    proof = json.loads(tombstone.read_bytes())
    immutable = (tombstone.stat().st_ino, tombstone.stat().st_mtime_ns, tombstone.read_bytes())
    fence = proof["fence"]
    source_writer = (directory / "artifact/.writer.lock").stat()
    archive_writer = (directory / "archive/.archive-writer.lock").stat()
    assert fence["source_writer"] == {"device": source_writer.st_dev, "inode": source_writer.st_ino}
    assert fence["archive_writer"] == {
        "device": archive_writer.st_dev,
        "inode": archive_writer.st_ino,
    }
    raw_stat = Path(f"/proc/{fence['process_id']}/stat").read_text()
    actual_start = int(raw_stat.rsplit(")", 1)[1].split()[19])
    assert fence["process_start_ticks"] == actual_start
    assert fence["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    assert fence["time_namespace"] == os.readlink(f"/proc/{fence['process_id']}/ns/time")
    restart()
    status, raw = call(operator, admin, "POST", "/v1/maintenance/archive/abort", cleanup)
    assert status == 200, raw
    receipt = json.loads(raw)["receipt"]
    assert receipt["fence"] == fence
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", cleanup)[0] == 200
    assert call(operator, admin, "POST", "/v1/maintenance/archive/abort", expired)[0] == 409
    assert (
        tombstone.stat().st_ino,
        tombstone.stat().st_mtime_ns,
        tombstone.read_bytes(),
    ) == immutable
    assert call(operator, admin, "POST", "/v1/maintenance/archive", command)[0] == 409
    assert (
        call(
            operator,
            admin,
            "POST",
            "/v1/maintenance/archive/abort",
            cleanup | {"budget_ms": 29_999},
        )[0]
        == 409
    )
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM event_archive_aborts WHERE original_command_id=? "
            "AND receipt_json IS NOT NULL",
            (original_id,),
        ).fetchone() == (1,)
        assert (
            connection.execute(
                "SELECT intent_json,state,receipt_json FROM event_archive_operations "
                "WHERE command_id=?",
                (original_id,),
            ).fetchone()
            == copy_before
        )
        assert (
            connection.execute(
                "SELECT intent_json,receipt_json FROM event_archive_aborts WHERE command_id=?",
                (expired["command_id"],),
            ).fetchone()
            == expired_row
        )
        complete = connection.execute(
            "SELECT intent_json,receipt_json FROM event_archive_aborts WHERE command_id=?",
            (cleanup["command_id"],),
        ).fetchone()
        assert complete is not None and complete[0] == pending[0] and complete[1] is not None
    new = command | {"command_id": "new-independent-original-archive"}
    assert call(operator, admin, "POST", "/v1/maintenance/archive", new)[0] == 200
    output = {
        "actual_native_writer_descriptors_verified": True,
        "actual_original_controller_incarnation_verified": True,
        "actual_unpublished_archive_failure": True,
        "original_copy_intent_budget_unchanged": True,
        "expired_cleanup_remains_unknown": True,
        "independent_cleanup_budget_not_copy_renewal": True,
        "actual_tombstone_before_sql_receipt_failure": True,
        "actual_restart_and_wait_before_reconciliation": True,
        "exact_reconciliation_repeated_copy_effects": 0,
        "partial_evidence_preserved": True,
        "published_abort_refused": True,
        "new_original_copy_after_confirmed_cleanup": True,
        "only_one_cleanup_confirmation": True,
        "actual_extra_uploads": 1,
        "devices": 0,
        "nodes": 0,
        "models": 0,
        "simulators": 0,
    }
    save(directory / "archive-abort-result.json", output)
    return output
