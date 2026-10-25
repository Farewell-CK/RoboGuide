"""Produce exact original-provider Snapshot pins while all declared writer leases are held."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from .profile import real_directory, validate_memory_storage

SCHEMA = "roboguide.recovery-profile/v0.8"
MAX_REFERENCES = 16_384
MAX_OPERATIONS = 4096
MAX_REQUEST_BYTES = 600 * 1024
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def supports(profile_schema: str) -> bool:
    """Require explicit profiles that preserve the original Snapshot producer authority."""
    return profile_schema in {
        SCHEMA,
        "roboguide.recovery-profile/v0.9",
        "roboguide.recovery-profile/v0.10",
        "roboguide.recovery-profile/v0.11",
        "roboguide.recovery-profile/v0.12",
        "roboguide.recovery-profile/v0.13",
        "roboguide.recovery-profile/v0.14",
        "roboguide.recovery-profile/v0.15",
        "roboguide.recovery-profile/v0.16",
        "roboguide.recovery-profile/v0.17",
        "roboguide.recovery-profile/v0.18",
        "roboguide.recovery-profile/v0.19",
        "roboguide.recovery-profile/v0.20",
        "roboguide.recovery-profile/v0.21",
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
        "roboguide.recovery-profile/v0.28",
        "roboguide.recovery-profile/v0.29",
        "roboguide.recovery-profile/v0.30",
    }


class OperationBudget(Protocol):
    """Retain the caller's original deadline while scanning and writing bounded metadata."""

    deadline_ns: int
    max_files: int
    max_bytes: int

    def check(self, byte_count: int = 0, file_count: int = 0) -> None:
        """Refuse work after the original nonrenewable operation budget is exhausted."""
        ...


def canonical(value: object) -> bytes:
    """Bind exact operation metadata without Python representation or mutable object identity."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: bytes) -> str:
    """Return the exact original SHA-256 identity shared by intent and immutable descriptors."""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _provider(connection: sqlite3.Connection, profile_schema: str = SCHEMA) -> tuple[str, str]:
    """Require explicit v4 authority and its original persistent provider instance."""
    validate_memory_storage(connection, profile_schema)
    rows = connection.execute("SELECT provider,instance FROM identity WHERE singleton=1").fetchall()
    if len(rows) != 1 or any(
        not isinstance(value, str) or not 0 < len(value) <= 128 for value in rows[0]
    ):
        raise ValueError("original snapshot provider identity is unavailable")
    return str(rows[0][0]), str(rows[0][1])


def _inputs(
    root: Path, connection: sqlite3.Connection, budget: OperationBudget
) -> list[dict[str, Any]]:
    """Freeze actual regular current CAS files; provenance alone never invents local content."""
    directory = real_directory(root / "content")
    inputs: list[dict[str, Any]] = []
    total = 0
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            budget.check()
            if index >= budget.max_files:
                raise ValueError("snapshot CAS scan exceeds the original file bound")
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise ValueError("snapshot CAS entry must be an original regular file")
            if re.fullmatch(r"[0-9a-f]{64}", entry.name) is None:
                if entry.name.startswith(".import-"):
                    continue
                raise ValueError("snapshot CAS entry lacks a canonical content identity")
            checksum = "sha256:" + entry.name
            if (
                connection.execute(
                    "SELECT 1 FROM content_tombstones WHERE content_digest=?", (checksum,)
                ).fetchone()
                is not None
            ):
                continue
            size = entry.stat(follow_symlinks=False).st_size
            total += size
            if total > budget.max_bytes or len(inputs) >= MAX_REFERENCES:
                raise ValueError("snapshot content set exceeds the original operation bound")
            inputs.append({"content_digest": checksum, "byte_size": size})
    return sorted(inputs, key=lambda item: item["content_digest"])


def prepare(
    profile: dict[str, Any],
    components: list[dict[str, Any]],
    destination: Path,
    command_id: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Persist original intents and pins before any backup copy, under already held leases."""
    descriptors = []
    for component in components:
        if component["kind"] != "memory":
            continue
        budget.check()
        root = Path(component["root"])
        with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
            provider, instance = _provider(connection, profile["schema"])
            purpose = {
                "command_id": command_id,
                "profile_digest": digest_profile(profile),
                "component_id": component["component_id"],
                "destination": str(destination),
                "provider_id": provider,
                "provider_instance_id": instance,
            }
            operation_id = digest(canonical(purpose))
            previous = connection.execute(
                "SELECT operation_id FROM snapshot_operations WHERE command_id=?", (command_id,)
            ).fetchone()
            if previous is not None and previous != (operation_id,):
                raise ValueError("original snapshot command identity conflicts")
            if previous is not None:
                raise ValueError(
                    "original snapshot outcome must be reconciled without another copy"
                )
            if (
                connection.execute("SELECT COUNT(*) FROM snapshot_operations").fetchone()[0]
                >= MAX_OPERATIONS
            ):
                raise ValueError("original snapshot operation history is full")
            inputs = _inputs(root, connection, budget)
            if (
                connection.execute("SELECT COUNT(*) FROM content_references").fetchone()[0]
                + len(inputs)
                > MAX_REFERENCES
            ):
                raise ValueError("original snapshot reference history is full")
            owner = "snapshot-operation:" + operation_id[7:]
            for value in inputs:
                value["reference_id"] = owner + ":" + value["content_digest"][7:]
            request = {
                "schema": "roboguide.memory-snapshot-intent/v0.1",
                **purpose,
                "operation_id": operation_id,
                "owner": owner,
                "generation": 1,
                "inputs": inputs,
                "original_uid": os.getuid(),
                "kernel": {
                    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                    "time_namespace": os.readlink("/proc/self/ns/time"),
                    "admitted_ns": time.monotonic_ns(),
                    "deadline_ns": budget.deadline_ns,
                },
            }
            encoded = canonical(request)
            if len(encoded) > MAX_REQUEST_BYTES:
                raise ValueError("snapshot original input metadata exceeds its bound")
            connection.execute("PRAGMA synchronous=FULL")
            budget.check()
            with connection:
                connection.execute(
                    "INSERT INTO snapshot_operations VALUES (?,?,?,'active',NULL)",
                    (operation_id, command_id, encoded),
                )
                connection.executemany(
                    "INSERT INTO content_references "
                    "(reference_id,content_digest,kind,owner,generation,active) "
                    "VALUES (?,?,'snapshot',?,1,1)",
                    [(value["reference_id"], value["content_digest"], owner) for value in inputs],
                )
            descriptors.append(
                {
                    "component_id": component["component_id"],
                    "operation_id": operation_id,
                    "provider_id": provider,
                    "provider_instance_id": instance,
                    "request_digest": digest(encoded),
                }
            )
    return descriptors


def digest_profile(profile: dict[str, Any]) -> str:
    """Use the recovery manifest's canonical profile encoding, including non-ASCII escaping."""
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return digest(encoded)


def _original(
    connection: sqlite3.Connection, descriptor: dict[str, Any], manifest: dict[str, Any]
) -> tuple[dict[str, Any], str, bytes | None]:
    """Bind an exact operation to its original actual provider and externally verified bundle."""
    expected = {
        "component_id",
        "operation_id",
        "provider_id",
        "provider_instance_id",
        "request_digest",
    }
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != expected
        or any(
            not isinstance(value, str) or not 0 < len(value) <= 256 for value in descriptor.values()
        )
        or DIGEST.fullmatch(descriptor["operation_id"]) is None
        or DIGEST.fullmatch(descriptor["request_digest"]) is None
    ):
        raise ValueError("snapshot descriptor is not closed")
    provider, instance = _provider(connection, manifest["profile"]["schema"])
    if (descriptor["provider_id"], descriptor["provider_instance_id"]) != (provider, instance):
        raise ValueError("snapshot proof belongs to a different actual provider instance")
    row = connection.execute(
        "SELECT request_json,state,receipt_json,command_id FROM snapshot_operations "
        "WHERE operation_id=?",
        (descriptor["operation_id"],),
    ).fetchone()
    if (
        row is None
        or not isinstance(row[0], bytes)
        or len(row[0]) > MAX_REQUEST_BYTES
        or digest(row[0]) != descriptor["request_digest"]
    ):
        raise ValueError("snapshot proof lacks its exact original durable intent")
    request = json.loads(row[0])
    if (
        not isinstance(request, dict)
        or set(request)
        != {
            "schema",
            "command_id",
            "profile_digest",
            "component_id",
            "destination",
            "provider_id",
            "provider_instance_id",
            "operation_id",
            "owner",
            "generation",
            "inputs",
            "original_uid",
            "kernel",
        }
        or request.get("schema") != "roboguide.memory-snapshot-intent/v0.1"
        or request.get("command_id") != manifest["command_id"]
        or request.get("command_id") != row[3]
        or request.get("profile_digest") != manifest["profile_digest"]
        or request.get("component_id") != descriptor["component_id"]
        or request.get("operation_id") != descriptor["operation_id"]
        or request.get("provider_id") != provider
        or request.get("provider_instance_id") != instance
        or type(request.get("generation")) is not int
        or request.get("generation") != 1
        or not isinstance(request.get("inputs"), list)
        or len(request["inputs"]) > MAX_REFERENCES
    ):
        raise ValueError("snapshot original purpose differs from its published proof")
    purpose = {
        key: request[key]
        for key in (
            "command_id",
            "profile_digest",
            "component_id",
            "destination",
            "provider_id",
            "provider_instance_id",
        )
    }
    operation_id = digest(canonical(purpose))
    kernel = request["kernel"]
    if (
        canonical(request) != row[0]
        or operation_id != request["operation_id"]
        or request["owner"] != "snapshot-operation:" + operation_id[7:]
        or not isinstance(request["destination"], str)
        or len(request["destination"]) > 4096
        or not Path(request["destination"]).is_absolute()
        or type(request["original_uid"]) is not int
        or not 0 <= request["original_uid"] < 2**32
        or not isinstance(kernel, dict)
        or set(kernel) != {"boot_id", "time_namespace", "admitted_ns", "deadline_ns"}
        or any(
            not isinstance(kernel[key], str) or not 0 < len(kernel[key]) <= 128
            for key in ("boot_id", "time_namespace")
        )
        or any(
            type(kernel[key]) is not int or not 0 <= kernel[key] < 2**63
            for key in ("admitted_ns", "deadline_ns")
        )
        or kernel["deadline_ns"] <= kernel["admitted_ns"]
    ):
        raise ValueError("snapshot original identity or kernel bounds are invalid")
    seen: set[str] = set()
    total = 0
    for value in request["inputs"]:
        if (
            not isinstance(value, dict)
            or set(value) != {"content_digest", "byte_size", "reference_id"}
            or not isinstance(value["content_digest"], str)
            or DIGEST.fullmatch(value["content_digest"]) is None
            or value["reference_id"] != request["owner"] + ":" + value["content_digest"][7:]
            or type(value["byte_size"]) is not int
            or value["byte_size"] < 0
            or value["content_digest"] in seen
        ):
            raise ValueError("snapshot original content set is invalid or repeated")
        total += value["byte_size"]
        if total > manifest["profile"]["max_bytes"]:
            raise ValueError("snapshot original content set exceeds its byte bound")
        seen.add(value["content_digest"])
    return request, str(row[1]), row[2]


def validate_bundle(bundle: Path, manifest: dict[str, Any], budget: OperationBudget) -> None:
    """Require every published descriptor to cover its original SQL intent and actual CAS bytes."""
    descriptors = manifest.get("snapshot_operations")
    memory = {
        item["component_id"]
        for item in manifest["profile"]["components"]
        if item["kind"] == "memory"
    }
    if (
        not isinstance(descriptors, list)
        or len(descriptors) != len(memory)
        or any(not isinstance(value, dict) for value in descriptors)
        or {value.get("component_id") for value in descriptors} != memory
    ):
        raise ValueError("complete original snapshot descriptor coverage is unavailable")
    files = {value["path"]: value for value in manifest["files"]}
    for descriptor in descriptors:
        budget.check()
        name = descriptor["component_id"]
        database = bundle / "stores" / name / "memory.sqlite3"
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            request, state, receipt = _original(connection, descriptor, manifest)
            if state != "active" or receipt is not None:
                raise ValueError("snapshot SQL does not retain its original pre-publication intent")
            for value in request["inputs"]:
                budget.check()
                checksum = value["content_digest"]
                row = files.get(f"stores/{name}/content/{checksum[7:]}")
                if (
                    row is None
                    or row["kind"] != "content"
                    or row["content_digest"] != checksum
                    or row["byte_size"] != value["byte_size"]
                ):
                    raise ValueError("snapshot original reference lacks its exact bundled content")
                reference = connection.execute(
                    "SELECT content_digest,kind,owner,generation,active FROM content_references "
                    "WHERE reference_id=?",
                    (value["reference_id"],),
                ).fetchone()
                if reference != (checksum, "snapshot", request["owner"], 1, 1):
                    raise ValueError("snapshot original reference identity is unavailable")


def complete(
    components: list[dict[str, Any]],
    manifest: dict[str, Any],
    manifest_digest: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Retire exact original pins only after the caller verifies all immutable bundle bytes."""
    by_id = {component["component_id"]: component for component in components}
    receipts = []
    if not isinstance(manifest_digest, str) or DIGEST.fullmatch(manifest_digest) is None:
        raise ValueError("snapshot completion requires the verified original manifest digest")
    for descriptor in manifest["snapshot_operations"]:
        budget.check()
        component = by_id.get(descriptor["component_id"])
        if component is None or component["kind"] != "memory":
            raise ValueError("snapshot reconciliation lost its declared original component")
        with closing(sqlite3.connect(Path(component["root"]) / "memory.sqlite3")) as connection:
            request, state, old_receipt = _original(connection, descriptor, manifest)
            receipt = {
                "schema": "roboguide.memory-snapshot-receipt/v0.1",
                "operation_id": descriptor["operation_id"],
                "request_digest": descriptor["request_digest"],
                "provider_id": descriptor["provider_id"],
                "provider_instance_id": descriptor["provider_instance_id"],
                "manifest_digest": manifest_digest,
                "state": "released",
                "generation": 1,
            }
            if state == "released":
                if old_receipt != canonical(receipt):
                    raise ValueError("original snapshot completion proof conflicts")
                receipts.append(receipt)
                continue
            if state != "active":
                raise ValueError("original snapshot was irreversibly aborted")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                for value in request["inputs"]:
                    row = connection.execute(
                        "SELECT content_digest,kind,owner,generation,active "
                        "FROM content_references WHERE reference_id=?",
                        (value["reference_id"],),
                    ).fetchone()
                    if row != (value["content_digest"], "snapshot", request["owner"], 1, 1):
                        raise ValueError("original snapshot pin changed before retirement")
                    connection.execute(
                        "UPDATE content_references SET active=0 WHERE reference_id=?",
                        (value["reference_id"],),
                    )
                connection.execute(
                    "UPDATE snapshot_operations SET state='released',receipt_json=? "
                    "WHERE operation_id=? AND state='active'",
                    (canonical(receipt), descriptor["operation_id"]),
                )
            receipts.append(receipt)
    return receipts


def abort(
    profile: dict[str, Any],
    components: list[dict[str, Any]],
    destination: Path,
    command_id: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Fence an unpublished original copy under all its original writer leases before release."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("published or replaced snapshot destination requires actual verification")
    receipts = []
    for component in components:
        if component["kind"] != "memory":
            continue
        budget.check()
        with closing(sqlite3.connect(Path(component["root"]) / "memory.sqlite3")) as connection:
            provider, instance = _provider(connection, profile["schema"])
            purpose = {
                "command_id": command_id,
                "profile_digest": digest_profile(profile),
                "component_id": component["component_id"],
                "destination": str(destination),
                "provider_id": provider,
                "provider_instance_id": instance,
            }
            operation_id = digest(canonical(purpose))
            row = connection.execute(
                "SELECT request_json,state,receipt_json FROM snapshot_operations "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("original snapshot abort intent is unavailable")
            request, _, _ = _original(
                connection,
                {
                    "component_id": component["component_id"],
                    "operation_id": operation_id,
                    "provider_id": provider,
                    "provider_instance_id": instance,
                    "request_digest": digest(row[0]),
                },
                {
                    "command_id": command_id,
                    "profile_digest": digest_profile(profile),
                    "profile": profile,
                },
            )
            if any(request.get(key) != value for key, value in purpose.items()):
                raise ValueError("original snapshot abort purpose conflicts")
            receipt = {
                "schema": "roboguide.memory-snapshot-receipt/v0.1",
                "operation_id": operation_id,
                "request_digest": digest(row[0]),
                "provider_id": provider,
                "provider_instance_id": instance,
                "state": "aborted",
                "generation": 1,
                "copy_fenced_by_original_writer_leases": True,
            }
            if row[1] == "aborted" and row[2] == canonical(receipt):
                receipts.append(receipt)
                continue
            if row[1] != "active":
                raise ValueError("original completed snapshot cannot be aborted")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                # This command cannot copy again. Complete original leases fence the previous
                # synchronous copy; this protocol launches no child or background copy work.
                connection.execute(
                    "UPDATE snapshot_operations SET state='aborted',receipt_json=? "
                    "WHERE operation_id=? AND state='active'",
                    (canonical(receipt), operation_id),
                )
                for value in request["inputs"]:
                    cursor = connection.execute(
                        "UPDATE content_references SET active=0 WHERE reference_id=? "
                        "AND content_digest=? AND kind='snapshot' AND owner=? AND generation=1",
                        (value["reference_id"], value["content_digest"], request["owner"]),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("original snapshot abort reference is unavailable")
            receipts.append(receipt)
    return receipts
