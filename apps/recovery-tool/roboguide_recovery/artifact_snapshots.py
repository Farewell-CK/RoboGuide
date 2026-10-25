"""Produce original actual Artifact Snapshot pins under the caller's complete root writer leases."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from . import artifact_layout
from .profile import real_directory
from .snapshot_references import OperationBudget, canonical, digest, digest_profile

SCHEMA = "roboguide.recovery-profile/v0.11"
SUPPORTED = {
    SCHEMA,
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
    "roboguide.recovery-profile/v0.31",
}
MAX_OPERATIONS = 4096
MAX_INPUTS = 16_384
MAX_RECORD_BYTES = 2 * 1024 * 1024
CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def _read(path: Path) -> dict[str, Any]:
    """Read only a bounded no-follow original regular file and reject partial or ambiguous JSON."""
    real_directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECORD_BYTES:
            raise ValueError("original Artifact journal record is not a bounded regular file")
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("original Artifact journal record exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or canonical(value) != raw:
        raise ValueError("original Artifact journal record is incomplete or noncanonical")
    return value


def _sync(path: Path) -> None:
    """Durably publish one original directory transition before returning any effect permit."""
    real_directory(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(path: Path, value: dict[str, Any]) -> None:
    """Atomically publish an immutable record; exact retry verifies its original bytes only."""
    raw = canonical(value)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("original Artifact journal record exceeds its bound")
    if path.exists() or path.is_symlink():
        if _read(path) != value:
            raise ValueError("original Artifact journal record conflicts")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sync(path.parent)
        return
    temporary = path.with_name(path.name + ".pending")
    if temporary.exists() or temporary.is_symlink():
        if _read(temporary) != value:
            raise ValueError("original pending Artifact journal record conflicts")
        descriptor = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    else:
        with temporary.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    os.link(temporary, path, follow_symlinks=False)
    temporary.unlink()
    _sync(path.parent)


def _purpose(
    profile: dict[str, Any],
    component: dict[str, Any],
    destination: Path,
    command_id: str,
    owner: str,
) -> dict[str, Any]:
    """Bind original copy purpose independently of observation, time or replacement roots."""
    return {
        "command_id": command_id,
        "profile_digest": digest_profile(profile),
        "component_id": component["component_id"],
        "destination": str(destination),
        "store_owner_id": owner,
        "kind": "snapshot",
    }


def _validate_request(request: dict[str, Any], owner: str) -> None:
    """Require original content, actual kernel and nonrenewable budget evidence."""
    fields = {
        "schema",
        "operation_id",
        "command_id",
        "profile_digest",
        "component_id",
        "destination",
        "store_owner_id",
        "kind",
        "generation",
        "inputs",
        "original_uid",
        "kernel",
    }
    if set(request) != fields or request["schema"] != "roboguide.artifact-snapshot-intent/v0.1":
        raise ValueError("original Artifact Snapshot intent is not closed")
    if (
        request["store_owner_id"] != owner
        or request["kind"] != "snapshot"
        or type(request["generation"]) is not int
        or request["generation"] != 1
        or any(
            not isinstance(request[key], str) or not 0 < len(request[key]) <= 4096
            for key in (
                "command_id",
                "component_id",
                "destination",
                "profile_digest",
                "operation_id",
            )
        )
        or not Path(request["destination"]).is_absolute()
        or CHECKSUM.fullmatch(request["profile_digest"]) is None
        or type(request["original_uid"]) is not int
        or not 0 <= request["original_uid"] < 2**32
        or not isinstance(request["inputs"], list)
        or len(request["inputs"]) > MAX_INPUTS
    ):
        raise ValueError("original Artifact Snapshot owner/purpose is invalid")
    purpose = {
        key: request[key]
        for key in (
            "command_id",
            "profile_digest",
            "component_id",
            "destination",
            "store_owner_id",
            "kind",
        )
    }
    if request["operation_id"] != digest(canonical(purpose)):
        raise ValueError("original Artifact Snapshot operation identity conflicts")
    kernel = request["kernel"]
    if (
        not isinstance(kernel, dict)
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
        raise ValueError("original Artifact Snapshot kernel/budget evidence is invalid")
    previous = ""
    for value in request["inputs"]:
        if (
            not isinstance(value, dict)
            or set(value) != {"content_digest", "byte_size"}
            or not isinstance(value["content_digest"], str)
            or CHECKSUM.fullmatch(value["content_digest"]) is None
            or value["content_digest"] <= previous
            or type(value["byte_size"]) is not int
            or not 0 <= value["byte_size"] <= 4 * 1024**3
        ):
            raise ValueError("original Artifact Snapshot content set is invalid or repeated")
        previous = value["content_digest"]


def operations(
    root: Path, budget: OperationBudget, *, profile_schema: str = SCHEMA
) -> list[tuple[Path, dict[str, Any], dict[str, Any] | None]]:
    """Read original history; unknown files or incomplete records keep a strict fence."""
    owner = artifact_layout.validate_storage(root, profile_schema)
    assert owner is not None
    directory = real_directory(root / "maintenance-v1/operations")
    result = []
    total = 0
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            budget.check()
            if (
                index >= MAX_OPERATIONS
                or entry.is_symlink()
                or not entry.is_dir(follow_symlinks=False)
                or HEX.fullmatch(entry.name) is None
            ):
                raise ValueError(
                    "original Artifact operation history is unavailable or exceeds bound"
                )
            path = directory / entry.name
            names = set()
            with os.scandir(real_directory(path)) as original_files:
                for file_index, item in enumerate(original_files):
                    budget.check()
                    if (
                        file_index >= 4
                        or item.is_symlink()
                        or not item.is_file(follow_symlinks=False)
                    ):
                        raise ValueError("original Artifact operation contains unexpected files")
                    names.add(item.name)
            if (
                not {"intent.json"}
                <= names
                <= {"intent.json", "receipt.json", "receipt.json.pending"}
            ):
                raise ValueError("original Artifact operation has incomplete or uncertain records")
            request = _read(path / "intent.json")
            _validate_request(request, owner["owner_id"])
            total += len(canonical(request))
            if total > budget.max_bytes or request["operation_id"][7:] != entry.name:
                raise ValueError(
                    "original Artifact history content/identity exceeds its operation bound"
                )
            receipt = _read(path / "receipt.json") if "receipt.json" in names else None
            pending = (
                _read(path / "receipt.json.pending") if "receipt.json.pending" in names else None
            )
            evidence = receipt if receipt is not None else pending
            if (
                evidence is not None
                and evidence.get("schema") == "roboguide.artifact-snapshot-receipt/v0.2"
            ):
                from .artifact_snapshot_retirement import validate_receipt

                if (
                    profile_schema
                    not in {
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
                        "roboguide.recovery-profile/v0.31",
                    }
                    or owner["schema"] != "roboguide.artifact-storage/v5"
                ):
                    raise ValueError(
                        "upgraded Snapshot retirement requires original storage v5/recovery v0.16"
                    )
                validate_receipt(evidence, request)
                if receipt is not None and pending is not None and receipt != pending:
                    raise ValueError("original upgraded Snapshot pending retirement conflicts")
                result.append((path, request, receipt))
                continue
            if evidence is not None and (
                set(evidence)
                != {
                    "schema",
                    "operation_id",
                    "request_digest",
                    "store_owner_id",
                    "generation",
                    "state",
                    "proof_digest",
                }
                or evidence["schema"] != "roboguide.artifact-snapshot-receipt/v0.1"
                or evidence["operation_id"] != request["operation_id"]
                or evidence["request_digest"] != digest(canonical(request))
                or evidence["store_owner_id"] != owner["owner_id"]
                or type(evidence["generation"]) is not int
                or evidence["generation"] != 1
                or evidence["state"] not in {"released", "aborted"}
                or not isinstance(evidence["proof_digest"], str)
                or CHECKSUM.fullmatch(evidence["proof_digest"]) is None
            ):
                raise ValueError("original Artifact operation retirement conflicts")
            if receipt is not None and pending is not None and receipt != pending:
                raise ValueError("original Artifact pending retirement conflicts with publication")
            result.append((path, request, receipt))
    return result


def _inputs(root: Path, budget: OperationBudget) -> list[dict[str, Any]]:
    """Freeze original CAS names and sizes before copying; later hashing verifies bytes."""
    directory = root / "objects-v1/sha256"
    if not directory.exists() and not directory.is_symlink():
        return []
    real_directory(directory)
    result: list[dict[str, Any]] = []
    tombstones = set()
    owner = (
        artifact_layout.validate_storage(root, "roboguide.recovery-profile/v0.16")
        if (root / "reclamation-v1").exists()
        else None
    )
    if owner is not None and owner["schema"] == "roboguide.artifact-storage/v5":
        from .artifact_gc_journal import validate as validate_gc

        tombstones = set(validate_gc(root, owner["owner_id"], budget)[1])
    total = scanned = 0
    with os.scandir(directory) as prefixes:
        for prefix in prefixes:
            budget.check()
            scanned += 1
            if scanned > budget.max_files or re.fullmatch(r"[0-9a-f]{2}", prefix.name) is None:
                raise ValueError("original Artifact CAS prefix scan exceeds its bound")
            selected = real_directory(directory / prefix.name)
            with os.scandir(selected) as entries:
                for entry in entries:
                    budget.check()
                    scanned += 1
                    if (
                        scanned > budget.max_files
                        or len(result) >= MAX_INPUTS
                        or entry.is_symlink()
                        or not entry.is_file(follow_symlinks=False)
                        or HEX.fullmatch(entry.name) is None
                        or not entry.name.startswith(prefix.name)
                    ):
                        raise ValueError("original Artifact CAS entry is invalid or exceeds bound")
                    if "sha256:" + entry.name in tombstones:
                        continue
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if total > budget.max_bytes:
                        raise ValueError("original Artifact CAS exceeds the original byte bound")
                    result.append({"content_digest": "sha256:" + entry.name, "byte_size": size})
    return sorted(result, key=lambda value: value["content_digest"])


def prepare(
    profile: dict[str, Any],
    components: list[dict[str, Any]],
    destination: Path,
    command_id: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Persist actual original-owner pins before any source content or authority copy."""
    descriptors = []
    for component in components:
        if component["kind"] != "artifact":
            continue
        root = Path(component["root"])
        owner = artifact_layout.validate_storage(root, profile["schema"])
        assert owner is not None
        history = operations(root, budget, profile_schema=profile["schema"])
        if len(history) >= MAX_OPERATIONS:
            raise ValueError("original Artifact operation history is full")
        if any(request["command_id"] == command_id for _, request, _ in history):
            raise ValueError(
                "original Artifact Snapshot requires readonly reconciliation, never another copy"
            )
        purpose = _purpose(profile, component, destination, command_id, owner["owner_id"])
        operation_id = digest(canonical(purpose))
        request = {
            "schema": "roboguide.artifact-snapshot-intent/v0.1",
            **purpose,
            "operation_id": operation_id,
            "generation": 1,
            "inputs": _inputs(root, budget),
            "original_uid": os.getuid(),
            "kernel": {
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "time_namespace": os.readlink("/proc/self/ns/time"),
                "admitted_ns": time.monotonic_ns(),
                "deadline_ns": budget.deadline_ns,
            },
        }
        _validate_request(request, owner["owner_id"])
        budget.check()
        path = root / "maintenance-v1/operations" / operation_id[7:]
        path.mkdir(mode=0o700)
        _sync(path.parent)
        _publish(path / "intent.json", request)
        descriptors.append(
            {
                "component_id": component["component_id"],
                "store_owner_id": owner["owner_id"],
                "operation_id": operation_id,
                "request_digest": digest(canonical(request)),
            }
        )
    return descriptors


def _original(
    root: Path, descriptor: dict[str, Any], manifest: dict[str, Any], budget: OperationBudget
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    """Resolve the original journal row without renewing budget or replacing identity."""
    if set(descriptor) != {"component_id", "store_owner_id", "operation_id", "request_digest"}:
        raise ValueError("original Artifact Snapshot descriptor is not closed")
    selected = next(
        (
            row
            for row in operations(root, budget, profile_schema=manifest["profile"]["schema"])
            if row[1]["operation_id"] == descriptor["operation_id"]
        ),
        None,
    )
    if selected is None:
        raise ValueError("original Artifact Snapshot intent is unavailable")
    _, request, _ = selected
    if (
        request["store_owner_id"] != descriptor["store_owner_id"]
        or request["component_id"] != descriptor["component_id"]
        or digest(canonical(request)) != descriptor["request_digest"]
        or request["profile_digest"] != manifest["profile_digest"]
        or request["command_id"] != manifest["command_id"]
    ):
        raise ValueError("Artifact Snapshot proof differs from its complete original intent")
    return selected


def validate_bundle(bundle: Path, manifest: dict[str, Any], budget: OperationBudget) -> None:
    """Verify producer coverage, active original pins and exact bundled CAS bytes."""
    descriptors = manifest.get("artifact_snapshot_operations")
    expected = {
        item["component_id"]
        for item in manifest["profile"]["components"]
        if item["kind"] == "artifact"
    }
    if (
        not isinstance(descriptors, list)
        or len(descriptors) != len(expected)
        or any(not isinstance(value, dict) for value in descriptors)
        or {value.get("component_id") for value in descriptors} != expected
    ):
        raise ValueError("complete original Artifact Snapshot producer coverage is unavailable")
    files = {value["path"]: value for value in manifest["files"]}
    for descriptor in descriptors:
        root = bundle / "stores" / descriptor["component_id"]
        _, _, receipt = _original(root, descriptor, manifest, budget)
        if receipt is not None:
            raise ValueError(
                "Artifact Snapshot bundle lost its original pre-publication active intent"
            )
        for _, request, retired in operations(
            root, budget, profile_schema=manifest["profile"]["schema"]
        ):
            if retired is not None:
                continue
            for value in request["inputs"]:
                budget.check()
                checksum = value["content_digest"][7:]
                row = files.get(
                    f"stores/{descriptor['component_id']}/objects-v1/sha256/{checksum[:2]}/{checksum}"
                )
                if (
                    row is None
                    or row["kind"] != "content"
                    or row["content_digest"] != value["content_digest"]
                    or row["byte_size"] != value["byte_size"]
                ):
                    raise ValueError(
                        "active original Artifact Snapshot requires exact bundled CAS bytes"
                    )


def complete(
    components: list[dict[str, Any]],
    manifest: dict[str, Any],
    manifest_digest: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Retire only the original Snapshot after complete independent bundle verification."""
    by_id = {component["component_id"]: component for component in components}
    receipts = []
    for descriptor in manifest["artifact_snapshot_operations"]:
        component = by_id.get(descriptor["component_id"])
        if component is None or component["kind"] != "artifact":
            raise ValueError("Artifact Snapshot original component is unavailable")
        path, request, previous = _original(Path(component["root"]), descriptor, manifest, budget)
        receipt = {
            "schema": "roboguide.artifact-snapshot-receipt/v0.1",
            "operation_id": request["operation_id"],
            "request_digest": descriptor["request_digest"],
            "store_owner_id": request["store_owner_id"],
            "generation": 1,
            "state": "released",
            "proof_digest": manifest_digest,
        }
        if previous is not None and previous != receipt:
            raise ValueError("Artifact Snapshot original retirement proof conflicts")
        budget.check()
        _publish(path / "receipt.json", receipt)
        receipts.append(receipt)
    return receipts


def abort(
    profile: dict[str, Any],
    components: list[dict[str, Any]],
    destination: Path,
    command_id: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Fence an unpublished synchronous copier while every original source writer lease is held."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("published Artifact Snapshot requires original bundle verification")
    receipts = []
    for component in components:
        if component["kind"] != "artifact":
            continue
        root = Path(component["root"])
        owner = artifact_layout.validate_storage(root, profile["schema"])
        assert owner is not None
        purpose = _purpose(profile, component, destination, command_id, owner["owner_id"])
        selected = next(
            (
                row
                for row in operations(root, budget, profile_schema=profile["schema"])
                if row[1]["operation_id"] == digest(canonical(purpose))
            ),
            None,
        )
        if selected is None:
            continue
        path, request, previous = selected
        receipt = {
            "schema": "roboguide.artifact-snapshot-receipt/v0.1",
            "operation_id": request["operation_id"],
            "request_digest": digest(canonical(request)),
            "store_owner_id": owner["owner_id"],
            "generation": 1,
            "state": "aborted",
            "proof_digest": digest(
                canonical(
                    {"original": request, "proof": "all-original-root-writers-held-before-abort"}
                )
            ),
        }
        if previous is not None and previous != receipt:
            raise ValueError("Artifact Snapshot already has another irreversible retirement")
        budget.check()
        _publish(path / "receipt.json", receipt)
        receipts.append(receipt)
    return receipts
