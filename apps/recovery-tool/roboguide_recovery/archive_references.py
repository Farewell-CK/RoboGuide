"""Verify exact original Archive commands, Catalog recovery and independent receiving CAS bytes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import archive_aborts, artifact_layout
from .snapshot_references import OperationBudget, canonical, digest

SCHEMA = "roboguide.recovery-profile/v0.12"
SUPPORTED = {
    SCHEMA,
    "roboguide.recovery-profile/v0.13",
    "roboguide.recovery-profile/v0.14",
    "roboguide.recovery-profile/v0.15",
}
WRAPPER = "roboguide.controller-checkpoint/v49"
WRAPPERS = {WRAPPER, "roboguide.controller-checkpoint/v50", "roboguide.controller-checkpoint/v51"}
MAX_HISTORY_BYTES = 256 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_METADATA = 128 * 1024 * 1024
CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}\Z")
OWNER = re.compile(r"artifact-store:[0-9a-f]{64}\Z")
PROTECTED = {
    "ArtifactCatalogReferenceTransition",
    "ArtifactCatalogLegacyAdopted",
    "ArtifactUploadTransition",
    "MapArtifactDeclared",
    "MapArtifactPublished",
    "MapArtifactStaged",
    "MapArtifactImported",
    "MapLocalizationVerified",
    "MapLocalizationEvidenceRecorded",
    "MapArtifactRejected",
    "MemoryManifestPublished",
    "MemoryArtifactStaged",
    "MemoryArtifactImported",
    "MapMemoryReplicaRecorded",
    "MemoryArtifactRejected",
    "SemanticVerifierEvidenceAdmitted",
    "MissionGoalEvidenceAdmitted",
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    """Observe exact original authority tables without creating or repairing any missing journal."""
    return {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _metadata(connection: sqlite3.Connection, key: str) -> str | None:
    """Read one original metadata value without substituting an empty recovery authority."""
    row = connection.execute("SELECT value FROM event_log_metadata WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def catalog_history(
    connection: sqlite3.Connection, profile_schema: str, budget: OperationBudget
) -> list[dict[str, Any]]:
    """Validate every retained original envelope and aggregate before combining it with the tail."""
    tables = _tables(connection)
    if "event_log_metadata" not in tables:
        return []
    raw = _metadata(connection, "catalog_archive_checkpoint")
    archive_raw = _metadata(connection, "archive_manifest")
    latest_archive = json.loads(archive_raw) if archive_raw is not None else None
    compacted = (
        "event_archive_operations" in tables
        and connection.execute(
            "SELECT 1 FROM event_archive_operations WHERE state='compacted' LIMIT 1"
        ).fetchone()
        is not None
    )
    if raw is None:
        if (
            "catalog_archive_events" in tables
            or compacted
            or (
                isinstance(latest_archive, dict)
                and latest_archive.get("schema") == "roboguide.event-archive/v2"
            )
        ):
            raise ValueError("mandatory original Catalog archive recovery checkpoint is missing")
        return []
    if profile_schema not in SUPPORTED:
        raise ValueError("original Catalog archive requires explicit recovery v0.12")
    head = json.loads(raw)
    if (
        not isinstance(head, dict)
        or set(head) != {"schema", "through_sequence", "event_count", "digest"}
        or head["schema"] != "roboguide.catalog-archive-checkpoint/v0.1"
        or type(head["through_sequence"]) is not int
        or head["through_sequence"] <= 0
        or str(head["through_sequence"]) != _metadata(connection, "history_floor")
        or type(head["event_count"]) is not int
        or not 0 <= head["event_count"] <= 16_384
    ):
        raise ValueError("original Catalog archive checkpoint identity/coverage is invalid")
    if "catalog_archive_events" not in tables:
        raise ValueError("mandatory original Catalog archive table is missing")
    rows: list[dict[str, Any]] = []
    total = 0
    previous = 0
    for sequence, value in connection.execute(
        "SELECT sequence,data FROM catalog_archive_events ORDER BY sequence"
    ):
        budget.check()
        total += len(value.encode())
        if total > MAX_CATALOG_BYTES or len(rows) >= 16_384:
            raise ValueError("original Catalog archive exceeds bounded recovery evidence")
        row = json.loads(value)
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "sequence",
                "event_id",
                "timestamp_ms",
                "correlation_id",
                "causation_id",
                "payload_schema",
                "payload_json",
            }
            or type(sequence) is not int
            or not previous < sequence <= head["through_sequence"]
            or row["sequence"] != sequence
            or not isinstance(row["payload_json"], str)
        ):
            raise ValueError("original Catalog archive envelope or sequence changed")
        payload = json.loads(row["payload_json"])
        if (
            not isinstance(payload, dict)
            or len(payload) != 1
            or next(iter(payload)) not in PROTECTED
            or not isinstance(row["payload_schema"], str)
            or re.fullmatch(
                r"domain.EventPayload.json/v(?:[1-9]|1[0-9]|2[0-9]|30)", row["payload_schema"]
            )
            is None
        ):
            raise ValueError("original Catalog archive contains unsupported evidence")
        previous = sequence
        rows.append(row)
    if len(rows) != head["event_count"] or digest(canonical(rows)) != head["digest"]:
        raise ValueError("original Catalog archive recovery is incomplete or changed")
    return rows


def _checkpoint_identity(checkpoint: dict[str, Any]) -> str:
    """Match Rust application identity while retaining the original JSON string."""
    schema = checkpoint["schema"].encode()
    payload = checkpoint["checkpoint_json"].encode()
    value = (
        b"roboguide.application-commit/v1"
        + len(schema).to_bytes(8, "little")
        + schema
        + checkpoint["event_sequence"].to_bytes(8, "little")
        + len(payload).to_bytes(8, "little")
        + payload
    )
    return hashlib.sha256(value).hexdigest()


def _intent(value: Any, command: str) -> dict[str, Any]:
    """Reject changed original owner, command, checkpoint, bounded requirements or kernel budget."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "request",
            "manifest",
            "checkpoint",
            "catalog_digest",
            "inputs",
            "boot_id",
            "time_namespace",
            "issued_ms",
            "deadline_ms",
        }
        or value["schema"] != "roboguide.archive-intent/v0.1"
    ):
        raise ValueError("original Archive intent is not closed/versioned")
    request, manifest, checkpoint = value["request"], value["manifest"], value["checkpoint"]
    if (
        not isinstance(request, dict)
        or set(request)
        != {
            "command_id",
            "principal",
            "tenant",
            "source_owner",
            "authority_id",
            "destination",
            "budget_ms",
        }
        or request["command_id"] != command
        or not isinstance(command, str)
        or not 0 < len(command) <= 128
        or any(
            not isinstance(request[key], str) or not 0 < len(request[key]) <= maximum
            for key, maximum in (("principal", 256), ("tenant", 128))
        )
        or not isinstance(request["source_owner"], str)
        or OWNER.fullmatch(request["source_owner"]) is None
        or not isinstance(request["authority_id"], str)
        or re.fullmatch(r"controller-store:[0-9a-f]{64}", request["authority_id"]) is None
        or not isinstance(request["destination"], str)
        or not Path(request["destination"]).is_absolute()
        or ".." in Path(request["destination"]).parts
        or type(request["budget_ms"]) is not int
        or not 0 < request["budget_ms"] <= 300_000
    ):
        raise ValueError("original Archive request attribution/source/destination is invalid")
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != {"schema", "event_sequence", "checkpoint_json"}
        or checkpoint["schema"] not in WRAPPERS
        or type(checkpoint["event_sequence"]) is not int
        or not 0 <= checkpoint["event_sequence"] < 2**64
        or not isinstance(checkpoint["checkpoint_json"], str)
        or len(checkpoint["checkpoint_json"].encode()) > MAX_METADATA
    ):
        raise ValueError("original Archive complete checkpoint is invalid")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {
            "schema",
            "start_sequence",
            "end_sequence",
            "event_count",
            "segment_digest",
            "checkpoint_sequence",
            "checkpoint_digest",
        }
        or manifest["schema"] != "roboguide.event-archive/v2"
        or any(
            type(manifest[key]) is not int
            for key in ("start_sequence", "end_sequence", "event_count", "checkpoint_sequence")
        )
        or not 0
        < manifest["start_sequence"]
        <= manifest["end_sequence"]
        <= manifest["checkpoint_sequence"]
        or manifest["checkpoint_sequence"] != checkpoint["event_sequence"]
        or not manifest["event_count"]
        == manifest["end_sequence"] - manifest["start_sequence"] + 1
        <= 8192
        or manifest["checkpoint_digest"] != _checkpoint_identity(checkpoint)
        or not isinstance(manifest["segment_digest"], str)
        or CHECKSUM.fullmatch(manifest["segment_digest"]) is None
    ):
        raise ValueError("original Archive prefix/checkpoint identity is invalid")
    if (
        any(
            type(value[key]) is not int or not 0 <= value[key] < 2**64
            for key in ("issued_ms", "deadline_ms")
        )
        or value["deadline_ms"] - value["issued_ms"] != request["budget_ms"]
        or not isinstance(value["boot_id"], str)
        or len(value["boot_id"]) != 36
        or not isinstance(value["time_namespace"], str)
        or not 0 < len(value["time_namespace"]) <= 128
        or not isinstance(value["catalog_digest"], str)
        or CHECKSUM.fullmatch(value["catalog_digest"]) is None
        or not isinstance(value["inputs"], list)
        or len(value["inputs"]) > 4096
    ):
        raise ValueError("original Archive kernel budget/Catalog/content bounds are invalid")
    previous = ""
    for item in value["inputs"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"content_digest", "byte_size"}
            or not isinstance(item["content_digest"], str)
            or CHECKSUM.fullmatch(item["content_digest"]) is None
            or item["content_digest"] <= previous
            or type(item["byte_size"]) is not int
            or not 0 <= item["byte_size"] <= 4 * 1024**3
        ):
            raise ValueError("original Archive input identity/size/order changed")
        previous = item["content_digest"]
    return dict(value)


def operations(
    connection: sqlite3.Connection, profile_schema: str, budget: OperationBudget, *, current: bool
) -> list[tuple[dict[str, Any], str, str | None]]:
    """Retain complete pending/compacted commands after event GC."""
    tables = _tables(connection)
    marker = (
        _metadata(connection, "archive_operations_schema")
        if "event_log_metadata" in tables
        else None
    )
    if current and (
        marker not in {"roboguide.archive-operations/v0.1", "roboguide.archive-operations/v0.2"}
        or "event_archive_operations" not in tables
    ):
        raise ValueError("mandatory original Archive journal is missing")
    if "event_archive_operations" not in tables:
        return []
    if marker not in {"roboguide.archive-operations/v0.1", "roboguide.archive-operations/v0.2"}:
        raise ValueError("original Archive journal marker changed")
    authority_id = _metadata(connection, "archive_authority_id")
    if (
        not isinstance(authority_id, str)
        or re.fullmatch(r"controller-store:[0-9a-f]{64}", authority_id) is None
    ):
        raise ValueError("original Controller reference-authority identity is unavailable")
    result: list[tuple[dict[str, Any], str, str | None]] = []
    total = 0
    for command, raw, state, receipt in connection.execute(
        "SELECT command_id,intent_json,state,receipt_json FROM event_archive_operations "
        "ORDER BY command_id"
    ):
        budget.check()
        total += len(raw.encode())
        if len(result) >= 4096 or total > MAX_HISTORY_BYTES:
            raise ValueError("original Archive history exceeds bounded recovery evidence")
        if profile_schema not in SUPPORTED or not current:
            raise ValueError(
                "original Archive commands require wrapper v49 and explicit recovery v0.12"
            )
        if (
            state not in {"pending", "compacted"}
            or (state == "compacted") != (receipt is not None)
            or (
                receipt is not None
                and (not isinstance(receipt, str) or CHECKSUM.fullmatch(receipt) is None)
            )
        ):
            raise ValueError("original Archive disposition/receipt is invalid")
        intent = _intent(json.loads(raw), command)
        if intent["request"]["authority_id"] != authority_id:
            raise ValueError("original Archive operation changed its Controller authority")
        result.append((intent, state, receipt))
    return result


def _file(
    root: Path, name: str, files: dict[str, dict[str, Any]], maximum: int, budget: OperationBudget
) -> bytes:
    """Require original declared regular bytes already verified by the complete bundle manifest."""
    budget.check()
    record = files.get(name)
    if record is None or record["kind"] != "content" or record["byte_size"] > maximum:
        raise ValueError("Archive publication lacks its exact declared bounded file: " + name)
    raw = (root / name).read_bytes()
    if len(raw) > maximum or digest(raw) != record["content_digest"]:
        raise ValueError("Archive original declared file changed: " + name)
    return raw


def validate(
    root: Path,
    components: list[dict[str, Any]],
    files: dict[str, dict[str, Any]],
    budget: OperationBudget,
    profile_schema: str,
) -> None:
    """Verify source pins and receiving archive ownership in one cold bundle."""
    owners: dict[str, str] = {}
    archives = {item["root"]: item for item in components if item["kind"] == "archive"}
    if len(archives) != sum(item["kind"] == "archive" for item in components):
        raise ValueError("Archive components duplicate original destinations")
    for item in components:
        if item["kind"] == "artifact" and artifact_layout.supports(profile_schema):
            owner = artifact_layout.validate_storage(
                root / "stores" / item["component_id"], profile_schema
            )
            assert owner is not None
            owners[owner["owner_id"]] = item["component_id"]
    seen: set[str] = set()
    authorities: dict[str, str] = {}
    bindings: dict[str, set[str]] = {}
    if profile_schema in SUPPORTED:
        for component_id in owners.values():
            directory = root / "stores" / component_id
            owner = artifact_layout.validate_storage(directory, profile_schema)
            assert owner is not None
            bindings[component_id] = artifact_layout.reference_authorities(directory, owner)
    for component in components:
        if component["kind"] != "controller":
            continue
        database = root / "stores" / component["component_id"] / component["database"]
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_HISTORY_BYTES)

            def progress() -> int:
                """Keep original SQL scans inside their actual recovery deadline."""
                budget.check()
                return 0

            connection.set_progress_handler(progress, 1000)
            tables = _tables(connection)
            checkpoint = (
                connection.execute(
                    "SELECT schema FROM controller_checkpoint WHERE singleton=1"
                ).fetchone()
                if "controller_checkpoint" in tables
                else None
            )
            current = checkpoint is not None and checkpoint[0] in WRAPPERS
            if current:
                authority_id = _metadata(connection, "archive_authority_id")
                if not isinstance(authority_id, str) or authority_id in authorities:
                    raise ValueError(
                        "original Controller reference authority is absent or repeated"
                    )
                authorities[authority_id] = component["component_id"]
            catalog_history(connection, profile_schema, budget)
            rows = operations(connection, profile_schema, budget, current=current)
            cleanup = archive_aborts.operations(
                connection,
                profile_schema,
                budget,
                rows,
                current=checkpoint is not None
                and checkpoint[0]
                in {archive_aborts.WRAPPER, "roboguide.controller-checkpoint/v51"},
            )
            cleanups: dict[str, list[tuple[dict[str, Any], dict[str, Any] | None]]] = {}
            for cleanup_intent, cleanup_receipt in cleanup:
                cleanups.setdefault(cleanup_intent["request"]["original_command_id"], []).append(
                    (cleanup_intent, cleanup_receipt)
                )
        for intent, state, receipt in rows:
            budget.check()
            request = intent["request"]
            target = archives.get(request["destination"])
            source = owners.get(request["source_owner"])
            if (
                target is None
                or source is None
                or not {source, component["component_id"]} <= set(target["depends_on"])
            ):
                raise ValueError(
                    "original Archive lacks its declared source and actual writer-bound destination"
                )
            if request["authority_id"] not in bindings.get(source, set()):
                raise ValueError("Archive source lost its original Controller binding")
            prefix = (
                "stores/"
                + target["component_id"]
                + "/archives-v1/"
                + hashlib.sha256(request["authority_id"].encode()).hexdigest()
                + "/archive-"
                + hashlib.sha256(request["command_id"].encode()).hexdigest()
            )
            if prefix in seen:
                raise ValueError(
                    "Archive commands from independent authorities overlap one destination"
                )
            seen.add(prefix)
            aborted = False
            for cleanup_intent, cleanup_receipt in cleanups.get(request["command_id"], []):
                aborted = (
                    archive_aborts.validate_tombstone(
                        root, prefix, cleanup_intent, cleanup_receipt, files, budget
                    )
                    or aborted
                )
            if state == "pending":
                for item in [] if aborted else intent["inputs"]:
                    checksum = item["content_digest"][7:]
                    name = f"stores/{source}/objects-v1/sha256/{checksum[:2]}/{checksum}"
                    record = files.get(name)
                    if (
                        record is None
                        or record["kind"] != "content"
                        or record["content_digest"] != item["content_digest"]
                        or record["byte_size"] != item["byte_size"]
                    ):
                        raise ValueError(
                            "pending Archive source pin lacks exact same-owner CAS bytes"
                        )
                if prefix + "/publication.json" not in files:
                    continue
            publication_raw = _file(root, prefix + "/publication.json", files, MAX_METADATA, budget)
            publication = json.loads(publication_raw)
            if (
                not isinstance(publication, dict)
                or set(publication) != {"schema", "intent", "target_owner", "checkpoint_digest"}
                or publication["schema"] != "roboguide.content-archive/v0.1"
                or publication["intent"] != intent
                or not isinstance(publication["target_owner"], str)
                or OWNER.fullmatch(publication["target_owner"]) is None
                or publication["target_owner"] in owners
                or publication["checkpoint_digest"] != digest(canonical(intent["checkpoint"]))
                or (receipt is not None and receipt != digest(canonical(publication)))
            ):
                raise ValueError("Archive original publication/receipt/receiving ownership changed")
            for name, expected in (
                ("intent.json", canonical(intent)),
                ("target-owner.json", canonical(publication["target_owner"])),
                ("checkpoint.json", canonical(intent["checkpoint"])),
            ):
                if _file(root, prefix + "/" + name, files, MAX_METADATA, budget) != expected:
                    raise ValueError("Archive original retained identity/checkpoint bytes changed")
            for name, expected in (
                ("events.jsonl", intent["manifest"]["segment_digest"]),
                ("catalog.json", intent["catalog_digest"]),
            ):
                if (
                    digest(_file(root, prefix + "/" + name, files, MAX_METADATA, budget))
                    != expected
                ):
                    raise ValueError("Archive original retained event/Catalog bytes changed")
            content = root / prefix / "content"
            from . import archive_retirement

            if archive_retirement.retired(content, publication, profile_schema, budget):
                if state != "compacted" or receipt != digest(canonical(publication)):
                    raise ValueError("retired receiving Archive lacks its original compact receipt")
                continue
            owner = artifact_layout.validate_storage(content, profile_schema)
            assert owner is not None
            if owner["owner_id"] != publication["target_owner"]:
                raise ValueError("Archive actual receiving CAS owner changed")
            reference = (
                prefix
                + "/content/maintenance-v1/operations/"
                + hashlib.sha256(canonical(intent)).hexdigest()
                + "/archive-reference.json"
            )
            expected_reference = {
                "schema": "roboguide.archive-recipient-reference/v0.1",
                "intent": intent,
                "target_owner": publication["target_owner"],
            }
            if _file(root, reference, files, MAX_METADATA, budget) != canonical(expected_reference):
                raise ValueError("Archive independent receiving reference is missing or changed")
            for item in intent["inputs"]:
                checksum = item["content_digest"][7:]
                name = f"{prefix}/content/objects-v1/sha256/{checksum[:2]}/{checksum}"
                record = files.get(name)
                if (
                    record is None
                    or record["kind"] != "content"
                    or record["content_digest"] != item["content_digest"]
                    or record["byte_size"] != item["byte_size"]
                ):
                    raise ValueError("Archive retained input lacks exact receiving CAS bytes")

    for inventory in bindings.values():
        if not inventory <= authorities.keys():
            raise ValueError("known original Artifact reference authority is absent from recovery")
