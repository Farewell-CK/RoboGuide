"""Verify actual original-provider reclamation after formal Memory use and revocation."""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save
from memory_use_profile import request


def run(directory: Path, backend: str, restart: Callable[[], None]) -> dict[str, Any]:
    """Exercise real Unix HTTP, bytes, failed final SQL, original restart and readonly history."""
    database = directory / "consumer/backend/memory.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT original_json FROM imports ORDER BY import_id").fetchall()
    assert len(rows) == 1
    original = json.loads(rows[0][0])
    manifest = json.loads(original["original_invocation"]["manifest_json"])
    artifact = manifest["artifact"]
    content = directory / "consumer/backend/content" / artifact["content_digest"][7:]
    recovery_copy = directory / "retained-verifier-source.bin"
    shutil.copyfile(content, recovery_copy)
    with recovery_copy.open("rb") as stream:
        assert (
            "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            == artifact["content_digest"]
        )
    command = {
        "schema": "roboguide.content-reclamation-command/v0.1",
        "command_id": "actual-content-reclamation-" + uuid.uuid4().hex,
        "provider_id": original["provider_id"],
        "provider_instance_id": original["provider_instance_id"],
        "content_digest": artifact["content_digest"],
        "byte_size": artifact["byte_size"],
    }
    save(directory / "reclamation-command.json", command)
    status, completion = request(
        backend, "/v1/import-query", {"memory_import": original}, method="GET"
    )
    assert status == 200 and completion["result"]["outcome"] == "completed", completion
    status, tombstone = request(backend, "/v1/content/tombstone", command)
    assert status == 200 and tombstone["state"] == "tombstoned", tombstone
    assert content.is_file()
    status, availability = request(
        backend, "/v1/availability", {"memory_import": original}, method="GET"
    )
    assert status == 200 and availability["availability"]["available"] is False, availability
    save(directory / "reclamation-tombstone.json", tombstone)
    save(directory / "reclamation-availability.json", availability)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER actual_reclamation_receipt_fault "
            "BEFORE UPDATE ON reclamation_commands WHEN NEW.state='deleted' "
            "BEGIN SELECT RAISE(ABORT,'original reclamation receipt SQL failure'); END"
        )
    try:
        request(backend, "/v1/content/collect", command)
    except (http.client.RemoteDisconnected, ConnectionError) as error:
        save(directory / "reclamation-interrupted-response.json", {"error": repr(error)})
    else:
        raise AssertionError("actual final receipt SQL fault did not interrupt the operation")
    assert not content.exists()
    status, observed = request(
        backend, "/v1/content/reclamation", {"command_id": command["command_id"]}, method="GET"
    )
    assert status == 200 and observed == tombstone, observed
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER actual_reclamation_receipt_fault")
    restart()
    status, observed = request(
        backend, "/v1/content/reclamation", {"command_id": command["command_id"]}, method="GET"
    )
    assert status == 200 and observed == tombstone, observed
    status, collected = request(backend, "/v1/content/collect", command)
    assert status == 200 and collected["state"] == "deleted", collected
    assert collected["reconciled_missing_after_tombstone"] is True
    assert collected["local_copy_only"] is True
    assert request(backend, "/v1/content/collect", command) == (200, collected)
    assert request(backend, "/v1/import-query", {"memory_import": original}, method="GET") == (
        200,
        completion,
    )
    status, denied = request(
        backend,
        "/v1/import",
        {
            "memory_import": original,
            "memory_manifest": manifest,
            "staged_artifact": str(recovery_copy),
        },
    )
    assert status == 409 and "tombstoned" in denied["error"], denied
    assert not content.exists()
    save(directory / "reclamation-receipt.json", collected)
    return {
        "passed": True,
        "provider_instance_id": original["provider_instance_id"],
        "original_import_id": original["import_id"],
        "content_digest": artifact["content_digest"],
        "actual_tombstones": 1,
        "actual_local_unlinks": 1,
        "actual_final_sql_failures": 1,
        "original_explicit_reconciliations": 1,
        "original_completion_history_preserved": True,
        "current_availability_after_tombstone": False,
        "reimport_after_tombstone": False,
        "local_copy_only": True,
        "devices": 0,
    }
