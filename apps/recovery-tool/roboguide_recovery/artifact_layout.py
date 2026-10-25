"""Verify exact retained Artifact owners independently of CAS or Node/provider identity."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .profile import HeldWriters, real_directory

SCHEMA = "roboguide.recovery-profile/v0.9"
REFERENCE_SCHEMA = "roboguide.recovery-profile/v0.10"
MAINTENANCE_SCHEMA = "roboguide.recovery-profile/v0.11"
ARCHIVE_SCHEMA = "roboguide.recovery-profile/v0.12"
ARCHIVE_SCHEMAS = {
    ARCHIVE_SCHEMA,
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
}
OWNER_FILE = ".retention-owner.json"
FENCE_FILE = "blobs"
OBJECTS = "objects-v1"
MAINTENANCE_SCHEMAS = {
    MAINTENANCE_SCHEMA,
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
}
MAX_OWNER_BYTES = 4096


def supports(profile_schema: str) -> bool:
    """Keep original retained layout explicit in both the original and reference-aware profiles."""
    return profile_schema in {SCHEMA, REFERENCE_SCHEMA} | MAINTENANCE_SCHEMAS


def metadata_files(component: dict[str, Any], profile_schema: str) -> list[str]:
    """Select only the explicit format's mandatory original identity and old-layout fence."""
    if component["kind"] == "replica-content":
        from .replica_content import metadata_files as root_metadata

        return root_metadata(component)
    return (
        [OWNER_FILE, FENCE_FILE]
        if component["kind"] == "artifact" and supports(profile_schema)
        else []
    )


def _record(
    path: Path, *, schema: str | frozenset[str] = "roboguide.artifact-storage/v1"
) -> dict[str, Any]:
    """Read one bounded no-follow regular-file owner without accepting a replacement identity."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_OWNER_BYTES:
            raise ValueError("Artifact owner must be a bounded original regular file")
        raw = stream.read(MAX_OWNER_BYTES + 1)
        if len(raw) > MAX_OWNER_BYTES:
            raise ValueError("Artifact owner exceeds its original byte bound")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "owner_id", "source_device", "source_inode"}
        or (
            value["schema"] not in schema
            if isinstance(schema, frozenset)
            else value["schema"] != schema
        )
        or not isinstance(value["owner_id"], str)
        or re.fullmatch(r"artifact-store:[0-9a-f]{64}", value["owner_id"]) is None
        or any(
            type(value[key]) is not int or not 0 <= value[key] < 2**64
            for key in ("source_device", "source_inode")
        )
        or value["source_inode"] == 0
    ):
        raise ValueError("Artifact original owner metadata is invalid")
    return dict(value)


def validate_storage(
    root: Path, profile_schema: str, *, deferred_gc: HeldWriters | None = None
) -> dict[str, Any] | None:
    """Require explicit complete retained format while keeping old profile semantics closed."""
    real_directory(root)
    pending = root / ".retention-intent.json"
    maintenance_pending = root / ".maintenance-upgrade.json"
    if (root / ".archive-reference-upgrade.json").exists() or (
        root / ".archive-reference-upgrade.json"
    ).is_symlink():
        raise ValueError("original Artifact reference-authority upgrade is pending")
    if (root / ".reclamation-upgrade.json").exists() or (
        root / ".reclamation-upgrade.json"
    ).is_symlink():
        raise ValueError("original Artifact reclamation upgrade is pending")
    if maintenance_pending.exists() or maintenance_pending.is_symlink():
        raise ValueError("original Artifact maintenance upgrade is still pending")
    if not supports(profile_schema):
        if any(
            path.exists() or path.is_symlink()
            for path in (root / OWNER_FILE, pending, root / OBJECTS)
        ):
            raise ValueError("retained Artifact storage requires explicit recovery profile v0.9")
        return None
    if pending.exists() or pending.is_symlink():
        raise ValueError("original Artifact owner migration is still pending")
    schema = (
        frozenset({"roboguide.artifact-storage/v3", "roboguide.artifact-storage/v5"})
        if profile_schema
        in {
            "roboguide.recovery-profile/v0.16",
            "roboguide.recovery-profile/v0.17",
            "roboguide.recovery-profile/v0.18",
            "roboguide.recovery-profile/v0.19",
            "roboguide.recovery-profile/v0.20",
            "roboguide.recovery-profile/v0.21",
            "roboguide.recovery-profile/v0.22",
            "roboguide.recovery-profile/v0.23",
            "roboguide.recovery-profile/v0.24",
        }
        else "roboguide.artifact-storage/v3"
        if profile_schema in ARCHIVE_SCHEMAS
        else "roboguide.artifact-storage/v2"
        if profile_schema in MAINTENANCE_SCHEMAS
        else "roboguide.artifact-storage/v1"
    )
    owner = _record(root / OWNER_FILE, schema=schema)
    if _record(root / FENCE_FILE, schema=owner["schema"]) != owner:
        raise ValueError("Artifact original owner and old-layout fence conflict")
    real_directory(root / OBJECTS)
    real_directory(root / "staging")
    if profile_schema in MAINTENANCE_SCHEMAS:
        journal = real_directory(root / "maintenance-v1")
        real_directory(journal / "operations")
        if _record(journal / "identity.json", schema=owner["schema"]) != owner:
            raise ValueError("original Artifact journal identity differs from its actual owner")
    if profile_schema in ARCHIVE_SCHEMAS:
        reference_authorities(root, owner)
    if owner["schema"] == "roboguide.artifact-storage/v5":
        from .artifact_gc_journal import validate as validate_gc

        if _record(root / "reclamation-v1/identity.json", schema=owner["schema"]) != owner:
            raise ValueError("original reclamation journal owner differs")
        if deferred_gc is None:
            validate_gc(root, owner["owner_id"])
        else:
            deferred_gc.verify(root / ".writer.lock")
    return owner


def reference_authorities(root: Path, owner: dict[str, Any]) -> set[str]:
    """Retain every known original consumer, including a partial same-owner binding publication."""
    directory = real_directory(root / "reference-authorities-v1")
    if _record(directory / "identity.json", schema=owner["schema"]) != owner:
        raise ValueError("original Artifact reference authority inventory owner differs")
    controllers = real_directory(directory / "controllers")
    result: dict[str, dict[str, Any]] = {}
    head_path = directory / "inventory.json"
    with os.fdopen(os.open(head_path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128 * 1024:
            raise ValueError("original Artifact inventory head exceeds its bound")
        head = json.loads(stream.read(128 * 1024 + 1))
    if (
        not isinstance(head, dict)
        or set(head) != {"schema", "store_owner_id", "authority_ids"}
        or head["schema"] != "roboguide.artifact-reference-inventory/v0.1"
        or head["store_owner_id"] != owner["owner_id"]
        or not isinstance(head["authority_ids"], list)
        or len(head["authority_ids"]) > 1024
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"controller-store:[0-9a-f]{64}", value) is None
            for value in head["authority_ids"]
        )
        or head["authority_ids"] != sorted(set(head["authority_ids"]))
    ):
        raise ValueError("original Artifact inventory head owner or consumer identities changed")
    for index, path in enumerate(controllers.iterdir()):
        if index >= 2048:
            raise ValueError("original Artifact authority inventory exceeds its bound")
        match = re.fullmatch(r"(?:([0-9a-f]{64})\.json|\.([0-9a-f]{64})\.pending)", path.name)
        if match is None:
            raise ValueError("original Artifact authority inventory contains an unknown record")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_OWNER_BYTES:
                raise ValueError("original Artifact authority record is not a bounded regular file")
            value = json.loads(stream.read(MAX_OWNER_BYTES + 1))
        key = "controller-store:" + (match[1] or match[2])
        expected = {
            "schema": "roboguide.artifact-reference-authority/v0.1",
            "authority_id": key,
            "store_owner_id": owner["owner_id"],
        }
        if value != expected or (key in result and result[key] != value):
            raise ValueError("original Artifact reference authority binding changed")
        result[key] = value
        if path.suffix == ".json" and key not in head["authority_ids"]:
            raise ValueError("original final Artifact consumer is absent from its retained head")
    if len(result) > 1024:
        raise ValueError("original Artifact authority inventory exceeds its consumer bound")
    if not set(head["authority_ids"]) <= result.keys():
        raise ValueError("original known Artifact consumer record is missing")
    pending = directory / ".inventory.pending"
    if pending.exists() or pending.is_symlink():
        with os.fdopen(os.open(pending, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 128 * 1024:
                raise ValueError("original pending Artifact head exceeds its bound")
            candidate = json.loads(stream.read(128 * 1024 + 1))
        if (
            not isinstance(candidate, dict)
            or set(candidate) != set(head)
            or candidate["schema"] != head["schema"]
            or candidate["store_owner_id"] != head["store_owner_id"]
            or not isinstance(candidate["authority_ids"], list)
            or any(not isinstance(value, str) for value in candidate["authority_ids"])
            or candidate["authority_ids"] != sorted(set(candidate["authority_ids"]))
            or not set(head["authority_ids"]) <= set(candidate["authority_ids"]) <= result.keys()
        ):
            raise ValueError("original pending Artifact inventory head changed")
    return set(result)


def validate_bundle(
    root: Path, component: dict[str, Any], records: dict[str, dict[str, Any]], profile_schema: str
) -> None:
    """Bind mandatory original owner records to the exact declared immutable component files."""
    if not supports(profile_schema):
        return
    component_id = component["component_id"]
    for name in metadata_files(component, profile_schema):
        record = records.get(f"stores/{component_id}/{name}")
        if record is None or record["kind"] != "content" or record["byte_size"] > MAX_OWNER_BYTES:
            raise ValueError("retained Artifact bundle lacks its mandatory original owner files")
    if profile_schema in MAINTENANCE_SCHEMAS:
        if (
            records.get(f"stores/{component_id}/maintenance-v1/identity.json", {}).get("kind")
            != "content"
        ):
            raise ValueError("Artifact bundle lacks its mandatory original maintenance journal")
    owner = validate_storage(root / "stores" / component_id, profile_schema)
    if profile_schema in {
        "roboguide.recovery-profile/v0.16",
        "roboguide.recovery-profile/v0.17",
        "roboguide.recovery-profile/v0.18",
        "roboguide.recovery-profile/v0.19",
        "roboguide.recovery-profile/v0.20",
        "roboguide.recovery-profile/v0.21",
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
        "roboguide.recovery-profile/v0.24",
    } and (owner is None or owner["schema"] != component["storage_schema"]):
        raise ValueError("original Artifact bundle format differs from the explicit declaration")
