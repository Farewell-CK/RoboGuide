"""Retire an original unpublished provider Snapshot after complete original/current writer drain."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import snapshot_references as snapshots
from .bundle import Budget
from .profile import acquire_all, validate

SCHEMA = "roboguide.recovery-profile/v0.16"


def _original(
    connection: sqlite3.Connection, descriptor: dict[str, str], manifest: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Check the complete existing intent, original receipt and every generation-1 pin."""
    request, state, raw_receipt = snapshots._original(connection, descriptor, manifest)
    receipt = {
        "schema": "roboguide.memory-snapshot-receipt/v0.1",
        "operation_id": descriptor["operation_id"],
        "request_digest": descriptor["request_digest"],
        "provider_id": descriptor["provider_id"],
        "provider_instance_id": descriptor["provider_instance_id"],
        "state": "aborted",
        "generation": 1,
        "copy_fenced_by_original_writer_leases": True,
    }
    if state not in {"active", "aborted"} or (
        (state == "active" and raw_receipt is not None)
        or (state == "aborted" and raw_receipt != snapshots.canonical(receipt))
    ):
        raise ValueError(
            "published or inconsistent original Snapshot needs publication verification"
        )
    for value in request["inputs"]:
        pin = connection.execute(
            "SELECT content_digest,kind,owner,generation,active "
            "FROM content_references WHERE reference_id=?",
            (value["reference_id"],),
        ).fetchone()
        if pin != (
            value["content_digest"],
            "snapshot",
            request["owner"],
            1,
            int(state == "active"),
        ):
            raise ValueError("original provider Snapshot lost its exact generation-1 pin")
    return request, state


def run(
    profile: dict[str, Any],
    original_profile: dict[str, Any],
    component_id: str,
    operation_id: str,
    original_digest: str,
) -> dict[str, Any]:
    """Execute the original abort after locking and checking both complete profiles."""
    components, original_components = validate(profile), validate(original_profile)
    if (
        profile["schema"] != SCHEMA
        or not snapshots.supports(original_profile["schema"])
        or (
            any(value["kind"] == "replica" for value in components + original_components)
            or profile["artifact_replica_sets"]
            or original_profile["artifact_replica_sets"]
        )
    ):
        raise ValueError("provider Snapshot retirement requires complete standalone recovery v0.16")
    if any(
        not isinstance(value, str) or snapshots.DIGEST.fullmatch(value) is None
        for value in (operation_id, original_digest)
    ):
        raise ValueError("provider Snapshot retirement requires exact original operation/digest")
    selected = [
        value
        for value in components
        if value["kind"] == "memory" and value["component_id"] == component_id
    ]
    original = [
        value
        for value in original_components
        if value["kind"] == "memory" and value["component_id"] == component_id
    ]
    if len(selected) != 1 or len(original) != 1:
        raise ValueError("original/current provider Snapshot component is unavailable")
    current_keys = {(value["kind"], value["root"], value["database"]) for value in components}
    extra = [
        value
        for value in original_components
        if (value["kind"], value["root"], value["database"]) not in current_keys
    ]
    budget = Budget(profile)
    with (
        acquire_all(components, profile_schema=profile["schema"]),
        acquire_all(extra, profile_schema=original_profile["schema"]),
        closing(
            sqlite3.connect(
                (Path(selected[0]["root"]) / "memory.sqlite3").as_uri() + "?mode=rw", uri=True
            )
        ) as connection,
        closing(
            sqlite3.connect(
                (Path(original[0]["root"]) / "memory.sqlite3").as_uri() + "?mode=ro", uri=True
            )
        ) as source,
    ):
        budget.check()
        for database in (connection, source):
            database.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, snapshots.MAX_REQUEST_BYTES)
        provider, instance = snapshots._provider(connection)
        if snapshots._provider(source) != (provider, instance):
            raise ValueError("original provider Snapshot instance changed after recovery")
        row = connection.execute(
            "SELECT request_json,command_id FROM snapshot_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if (
            row is None
            or not isinstance(row[0], bytes)
            or snapshots.digest(row[0]) != original_digest
        ):
            raise ValueError("original provider Snapshot request is unavailable or changed")
        descriptor = {
            "component_id": component_id,
            "operation_id": operation_id,
            "provider_id": provider,
            "provider_instance_id": instance,
            "request_digest": original_digest,
        }
        manifest = {
            "command_id": row[1],
            "profile_digest": snapshots.digest_profile(original_profile),
            "profile": original_profile,
        }
        request, state = _original(connection, descriptor, manifest)
        original_request, _ = _original(source, descriptor, manifest)
        if original_request != request:
            raise ValueError("original source/current provider Snapshot request conflicts")
        # End readonly SQL handles before the original implementation takes its atomic writer.
        connection.close()
        source.close()
        budget.check()
        receipts = snapshots.abort(
            original_profile, selected, Path(request["destination"]), row[1], budget
        )
    return {
        "schema": "roboguide.provider-snapshot-retirement/v0.1",
        "receipts": receipts,
        "partial_evidence_preserved": True,
        "copied_files": 0,
        "replayed": state == "aborted",
    }
