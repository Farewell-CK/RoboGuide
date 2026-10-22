"""Verify exact retained Artifact owners independently of CAS or Node/provider identity."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .profile import real_directory

SCHEMA = "roboguide.recovery-profile/v0.9"
REFERENCE_SCHEMA = "roboguide.recovery-profile/v0.10"
MAINTENANCE_SCHEMA = "roboguide.recovery-profile/v0.11"
OWNER_FILE = ".retention-owner.json"
FENCE_FILE = "blobs"
OBJECTS = "objects-v1"
MAX_OWNER_BYTES = 4096


def supports(profile_schema: str) -> bool:
    """Keep original retained layout explicit in both the original and reference-aware profiles."""
    return profile_schema in {SCHEMA, REFERENCE_SCHEMA, MAINTENANCE_SCHEMA}


def metadata_files(component: dict[str, Any], profile_schema: str) -> list[str]:
    """Select only the explicit format's mandatory original identity and old-layout fence."""
    return (
        [OWNER_FILE, FENCE_FILE]
        if component["kind"] == "artifact" and supports(profile_schema)
        else []
    )


def _record(path: Path, *, schema: str = "roboguide.artifact-storage/v1") -> dict[str, Any]:
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
        or value["schema"] != schema
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


def validate_storage(root: Path, profile_schema: str) -> dict[str, Any] | None:
    """Require explicit complete retained format while keeping old profile semantics closed."""
    real_directory(root)
    pending = root / ".retention-intent.json"
    maintenance_pending = root / ".maintenance-upgrade.json"
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
        "roboguide.artifact-storage/v2"
        if profile_schema == MAINTENANCE_SCHEMA
        else "roboguide.artifact-storage/v1"
    )
    owner = _record(root / OWNER_FILE, schema=schema)
    if _record(root / FENCE_FILE, schema=schema) != owner:
        raise ValueError("Artifact original owner and old-layout fence conflict")
    real_directory(root / OBJECTS)
    real_directory(root / "staging")
    if profile_schema == MAINTENANCE_SCHEMA:
        journal = real_directory(root / "maintenance-v1")
        real_directory(journal / "operations")
        if _record(journal / "identity.json", schema=schema) != owner:
            raise ValueError("original Artifact journal identity differs from its actual owner")
    return owner


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
    if profile_schema == MAINTENANCE_SCHEMA:
        if (
            records.get(f"stores/{component_id}/maintenance-v1/identity.json", {}).get("kind")
            != "content"
        ):
            raise ValueError("Artifact bundle lacks its mandatory original maintenance journal")
    validate_storage(root / "stores" / component_id, profile_schema)
