"""Archive the original Catalog of actual completed CPU/file Missions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from catalog_retention_processes import CatalogRetentionCheck
from check_state_verifier_processes import save


def run(
    directory: Path,
    check: CatalogRetentionCheck,
    exchange: Callable[..., tuple[int, dict[str, Any]]],
    restart: Callable[[], None],
) -> dict[str, Any]:
    """Archive original Node publication and CPU proofs without fabricating devices."""
    original = check.observe()
    assert original["record"]["retirement"] is not None
    source_rows = check.rows(check.manifest["selector"])
    assert len(source_rows) == 2
    with closing(sqlite3.connect(check.database.as_uri() + "?mode=ro", uri=True)) as connection:
        original_references = [
            (sequence, json.loads(raw)["ArtifactCatalogReferenceTransition"]["record"])
            for sequence, raw in connection.execute(
                "SELECT sequence,payload_json FROM events "
                "WHERE json_type(payload_json,'$.ArtifactCatalogReferenceTransition') "
                "IS NOT NULL ORDER BY sequence"
            )
        ]
    status, view = exchange("/v1/maintenance/archive")
    assert status == 200
    cutoff = view["latest_sequence"]
    command = {
        "schema": "roboguide.archive-command/v0.2",
        "command_id": "original-file-archive-" + uuid.uuid4().hex,
        "end_sequence": cutoff,
        "budget_ms": 60_000,
    }
    status, output = exchange("/v1/maintenance/archive", command)
    assert status == 200, output
    with closing(sqlite3.connect(check.database.as_uri() + "?mode=ro", uri=True)) as connection:
        operation = connection.execute(
            "SELECT intent_json,state,receipt_json FROM event_archive_operations "
            "WHERE command_id=?",
            (command["command_id"],),
        ).fetchone()
        assert operation is not None and operation[1] == "compacted"
        protected = connection.execute(
            "SELECT sequence,data FROM catalog_archive_events WHERE sequence<=? ORDER BY sequence",
            (cutoff,),
        ).fetchall()
    original_reference_events = [
        (sequence, json.loads(row)["payload_json"])
        for sequence, row in protected
        if "ArtifactCatalogReferenceTransition" in json.loads(row)["payload_json"]
    ]
    assert [
        (sequence, json.loads(raw)["ArtifactCatalogReferenceTransition"]["record"])
        for sequence, raw in original_reference_events
    ] == original_references
    assert [
        (sequence, record)
        for sequence, record in original_references
        if record["selector"] == check.manifest["selector"]
    ] == source_rows
    intent = json.loads(operation[0])
    archive = (
        directory
        / "content-archives/archives-v1"
        / hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
        / ("archive-" + hashlib.sha256(command["command_id"].encode()).hexdigest())
    )
    actual = json.loads((archive / "publication.json").read_bytes())
    assert actual["intent"] == intent
    original_files = {
        str(path.relative_to(archive)): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in archive.rglob("*")
        if path.is_file()
    }
    assert exchange("/v1/maintenance/archive", command)[0] == 200
    assert check.observe() == original
    restart()
    assert check.observe() == original
    assert {
        str(path.relative_to(archive)): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in archive.rglob("*")
        if path.is_file()
    } == original_files
    with closing(sqlite3.connect(check.database.as_uri() + "?mode=ro", uri=True)) as connection:
        assert (
            connection.execute(
                "SELECT intent_json,state,receipt_json FROM event_archive_operations "
                "WHERE command_id=?",
                (command["command_id"],),
            ).fetchone()
            == operation
        )
    result = {
        "actual_original_publication": True,
        "original_retirement_preserved": True,
        "original_catalog_envelopes_preserved": True,
        "original_budget_unchanged": True,
        "exact_retry_copied_files": 0,
        "actual_controller_restart_after_archive": True,
        "retired_selector_never_revived": True,
        "independently_retained_archive_inputs": len(intent["inputs"]),
        "actual_source_owner": intent["request"]["source_owner"],
        "actual_target_owner": actual["target_owner"],
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    save(directory / "content-archive-result.json", result)
    return result
