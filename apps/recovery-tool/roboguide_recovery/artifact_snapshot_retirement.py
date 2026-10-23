"""Retire an original unpublished Snapshot after locking original and current writers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import artifact_gc_journal as gc
from . import artifact_layout, artifact_snapshots, bundle
from .archive_reclamation import _kernel, _observed_lease
from .profile import acquire_all, layout, validate
from .snapshot_references import canonical, digest, digest_profile


def validate_receipt(receipt: dict[str, Any], request: dict[str, Any]) -> None:
    """Validate original cross-profile drain evidence without claiming current process ownership."""
    common = {
        "schema",
        "operation_id",
        "request_digest",
        "store_owner_id",
        "generation",
        "state",
        "proof_digest",
    }
    if set(receipt) != common | {
        "release_command_id",
        "reason",
        "original_profile_digest",
        "fence",
        "original_profile",
        "current_profile",
    } or (
        receipt["schema"] != "roboguide.artifact-snapshot-receipt/v0.2"
        or receipt["operation_id"] != request["operation_id"]
        or receipt["request_digest"] != digest(canonical(request))
        or receipt["store_owner_id"] != request["store_owner_id"]
        or type(receipt["generation"]) is not int
        or receipt["generation"] != 1
        or receipt["state"] != "aborted"
        or not isinstance(receipt["release_command_id"], str)
        or gc.COMMAND.fullmatch(receipt["release_command_id"]) is None
        or not gc._text(receipt["reason"], 1024)
        or receipt["original_profile_digest"] != request["profile_digest"]
        or not isinstance(receipt["fence"], dict)
        or not isinstance(receipt["original_profile"], dict)
        or not isinstance(receipt["current_profile"], dict)
    ):
        raise ValueError("original upgraded Snapshot retirement receipt is invalid")
    fence = receipt["fence"]
    if set(fence) != {"kernel", "process", "writers", "current_profile_digest"} or (
        not isinstance(fence["current_profile_digest"], str)
        or gc.CHECKSUM.fullmatch(fence["current_profile_digest"]) is None
        or receipt["proof_digest"]
        != digest(
            canonical(
                {
                    "original": request,
                    "fence": fence,
                    "release_command_id": receipt["release_command_id"],
                    "reason": receipt["reason"],
                }
            )
        )
    ):
        raise ValueError("original upgraded Snapshot drain proof changed")
    current_components = validate(receipt["current_profile"])
    original_components = validate(receipt["original_profile"])
    if receipt["current_profile"]["schema"] not in gc.PROFILES or (
        digest_profile(receipt["original_profile"]) != request["profile_digest"]
        or digest_profile(receipt["current_profile"]) != fence["current_profile_digest"]
    ):
        raise ValueError("original Snapshot retirement lost its complete original/current profile")
    gc.validate_process(fence["kernel"], fence["process"] | {"writers": fence["writers"]})
    current_keys = {
        (value["kind"], value["root"], value["database"]) for value in current_components
    }
    extra = [
        value
        for value in original_components
        if (value["kind"], value["root"], value["database"]) not in current_keys
    ]
    expected: set[tuple[str, str]] = set()
    for values, schema in (
        (current_components, receipt["current_profile"]["schema"]),
        (extra, receipt["original_profile"]["schema"]),
    ):
        for value in values:
            databases, _, locks = layout(value, profile_schema=schema)
            if value["kind"] == "memory":
                locks += [".memory-use.lock", *databases]
            expected.update(
                (value["component_id"], str(Path(value["root"]) / name)) for name in locks
            )
    if expected != {(value["component_id"], value["path"]) for value in fence["writers"]}:
        raise ValueError("original Snapshot retirement omitted an original/current writer")


def run(
    profile: dict[str, Any],
    original_profile: dict[str, Any],
    component_id: str,
    operation_id: str,
    original_digest: str,
    command_id: str,
    reason: str,
    *,
    reconcile: bool = False,
) -> dict[str, Any]:
    """Hold original and current writers before releasing the exact original pin."""
    components, original_components = validate(profile), validate(original_profile)
    if profile["schema"] not in gc.PROFILES or any(
        value["kind"] == "replica" for value in components + original_components
    ):
        raise ValueError("upgraded Snapshot retirement requires explicit standalone recovery v0.16")
    selected = [
        value
        for value in components
        if value["kind"] == "artifact" and value["component_id"] == component_id
    ]
    if len(selected) != 1 or selected[0]["storage_schema"] != gc.STORAGE:
        raise ValueError("upgraded Snapshot retirement requires its current explicit storage v5")
    if gc.COMMAND.fullmatch(command_id) is None or not gc._text(reason, 1024):
        raise ValueError("upgraded Snapshot retirement command is invalid")
    budget = bundle.Budget(profile)
    current_keys = {(value["kind"], value["root"], value["database"]) for value in components}
    extra = [
        value
        for value in original_components
        if (value["kind"], value["root"], value["database"]) not in current_keys
    ]
    root = Path(selected[0]["root"])
    with (
        acquire_all(components, profile_schema=profile["schema"]),
        acquire_all(extra, profile_schema=original_profile["schema"]),
    ):
        owner = artifact_layout.validate_storage(root, profile["schema"])
        assert owner is not None
        operations = artifact_snapshots.operations(root, budget, profile_schema=profile["schema"])
        chosen = [value for value in operations if value[1]["operation_id"] == operation_id]
        if len(chosen) != 1:
            raise ValueError("original Snapshot operation is absent")
        path, request, previous = chosen[0]
        if digest(canonical(request)) != original_digest or request[
            "profile_digest"
        ] != digest_profile(original_profile):
            raise ValueError("original Snapshot intent/profile digest conflicts")
        original_artifacts = [
            value
            for value in original_components
            if value["kind"] == "artifact" and value["component_id"] == request["component_id"]
        ]
        if len(original_artifacts) != 1:
            raise ValueError("original Snapshot source component is unavailable")
        original = original_artifacts[0]
        original_owner = artifact_layout.validate_storage(
            Path(original["root"]),
            profile["schema"] if original["root"] == str(root) else original_profile["schema"],
        )
        if (
            original_owner is None
            or original_owner["owner_id"] != request["store_owner_id"]
            or owner["owner_id"] != request["store_owner_id"]
        ):
            raise ValueError("original Snapshot source owner changed")
        if previous is not None:
            if previous.get("release_command_id") != command_id or previous.get("reason") != reason:
                raise ValueError("original Snapshot already has another irreversible retirement")
            return {"receipt": previous, "replayed": True, "copied_files": 0}
        pending = path / "receipt.json.pending"
        if pending.exists() or pending.is_symlink():
            original_pending = artifact_snapshots._read(pending)
            validate_receipt(original_pending, request)
            if (
                original_pending["release_command_id"] != command_id
                or original_pending["reason"] != reason
            ):
                raise ValueError(
                    "original pending Snapshot retirement has different immutable input"
                )
            if reconcile:
                budget.check()
                artifact_snapshots._publish(path / "receipt.json", original_pending)
            return {
                "receipt": original_pending,
                "replayed": True,
                "copied_files": 0,
                "phase": "completed" if reconcile else "unknown",
                "reconciled_metadata_only": reconcile,
            }
        if reconcile:
            raise ValueError("original pending Snapshot retirement is unavailable")
        destination = Path(request["destination"])
        if destination.exists() or destination.is_symlink():
            raise ValueError(
                "published original Snapshot requires original publication verification"
            )
        for _, _, receipt in operations:
            if receipt is not None and receipt.get("release_command_id") == command_id:
                raise ValueError("Snapshot retirement command already belongs to another operation")
        writers = []
        for values, schema in (
            (components, profile["schema"]),
            (extra, original_profile["schema"]),
        ):
            for value in values:
                databases, _, locks = layout(value, profile_schema=schema)
                if value["kind"] == "memory":
                    locks += [".memory-use.lock", *databases]
                for name in locks:
                    writer_path = Path(value["root"]) / name
                    writers.append(
                        {
                            "component_id": value["component_id"],
                            "path": str(writer_path),
                            **_observed_lease(writer_path),
                        }
                    )
        fence = {
            "kernel": _kernel(budget),
            "process": {
                "pid": os.getpid(),
                "uid": os.getuid(),
                "start_ticks": int(
                    Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
                ),
            },
            "writers": sorted(writers, key=lambda value: str(value["path"])),
            "current_profile_digest": digest_profile(profile),
        }
        receipt = {
            "schema": "roboguide.artifact-snapshot-receipt/v0.2",
            "operation_id": request["operation_id"],
            "request_digest": original_digest,
            "store_owner_id": owner["owner_id"],
            "generation": 1,
            "state": "aborted",
            "release_command_id": command_id,
            "reason": reason,
            "original_profile_digest": request["profile_digest"],
            "original_profile": original_profile,
            "current_profile": profile,
            "fence": fence,
            "proof_digest": digest(
                canonical(
                    {
                        "original": request,
                        "fence": fence,
                        "release_command_id": command_id,
                        "reason": reason,
                    }
                )
            ),
        }
        validate_receipt(receipt, request)
        budget.check()
        artifact_snapshots._publish(path / "receipt.json", receipt)
        return {"receipt": receipt, "replayed": False, "copied_files": 0}
