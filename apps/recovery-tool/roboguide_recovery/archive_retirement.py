"""Validate original private Archive retirement without releasing another owner."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from . import artifact_layout
from .profile import real_directory
from .snapshot_references import OperationBudget, canonical, digest

SCHEMA = "roboguide.recovery-profile/v0.14"
STORAGE = "roboguide.artifact-storage/v4"
DIRECTORY = "retirement-v1"
MAX_RECORD_BYTES = 768 * 1024
HEADERS = (
    ".retention-owner.json",
    "blobs",
    "maintenance-v1/identity.json",
    "reference-authorities-v1/identity.json",
)


def read(
    path: Path, bound: int = MAX_RECORD_BYTES, *, canonical_required: bool = True
) -> dict[str, Any]:
    """Read bounded original canonical metadata without following links."""
    real_directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > bound:
            raise ValueError("Archive retirement metadata exceeds its regular-file bound")
        raw = stream.read(bound + 1)
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or len(raw) > bound
        or (canonical_required and raw != canonical(value))
    ):
        raise ValueError("Archive retirement metadata is not exact closed canonical evidence")
    return value


def _checksum(value: object) -> bool:
    """Accept only complete lowercase content or evidence identities."""
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def bounded_entries(directory: Path, budget: OperationBudget, maximum: int) -> list[Path]:
    """Bound original directory scans before materializing entries or performing any effects."""
    result = []
    for index, path in enumerate(real_directory(directory).iterdir()):
        budget.check(file_count=1)
        if index >= maximum:
            raise ValueError("private Archive original directory exceeds its scan bound")
        result.append(path)
    return result


def validate_intent(intent: dict[str, Any], publication: dict[str, Any]) -> None:
    """Bind the first administrator decision, publication and writer proof."""
    fields = {
        "schema",
        "command_id",
        "original_intent_digest",
        "publication_digest",
        "source_receipt",
        "source_authority",
        "source_owner",
        "target_owner",
        "original_owner",
        "inputs",
        "principal_uid",
        "reason",
        "profile_digest",
        "kernel",
        "fence",
    }
    copy = publication["intent"]
    request = copy["request"]
    if (
        set(intent) != fields
        or intent["schema"] != "roboguide.archive-recipient-retirement/v0.1"
        or not isinstance(intent["command_id"], str)
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", intent["command_id"]) is None
        or intent["original_intent_digest"] != digest(canonical(copy))
        or intent["publication_digest"] != digest(canonical(publication))
        or intent["source_receipt"] != intent["publication_digest"]
        or intent["source_authority"] != request["authority_id"]
        or intent["source_owner"] != request["source_owner"]
        or intent["target_owner"] != publication["target_owner"]
        or intent["inputs"] != copy["inputs"]
        or type(intent["principal_uid"]) is not int
        or not 0 <= intent["principal_uid"] < 2**32
        or not isinstance(intent["reason"], str)
        or not 0 < len(intent["reason"].strip()) <= 1024
        or any(ord(c) < 32 for c in intent["reason"])
        or not _checksum(intent["profile_digest"])
    ):
        raise ValueError("original private Archive retirement attribution/publication changed")
    owner = intent["original_owner"]
    if (
        not isinstance(owner, dict)
        or set(owner) != {"schema", "owner_id", "source_device", "source_inode"}
        or owner["schema"] != "roboguide.artifact-storage/v3"
        or owner["owner_id"] != intent["target_owner"]
        or any(
            type(owner[k]) is not int or not 0 <= owner[k] < 2**64
            for k in ("source_device", "source_inode")
        )
        or owner["source_inode"] == 0
    ):
        raise ValueError("Archive retirement lost the original receiving owner")
    kernel, fence = intent["kernel"], intent["fence"]
    if (
        not isinstance(kernel, dict)
        or set(kernel) != {"boot_id", "time_namespace", "admitted_ns", "deadline_ns"}
        or not isinstance(kernel["boot_id"], str)
        or len(kernel["boot_id"]) != 36
        or not isinstance(kernel["time_namespace"], str)
        or not 0 < len(kernel["time_namespace"]) <= 128
        or any(
            type(kernel[k]) is not int or not 0 <= kernel[k] < 2**64
            for k in ("admitted_ns", "deadline_ns")
        )
        or not 0 < kernel["deadline_ns"] - kernel["admitted_ns"] <= 3600 * 1_000_000_000
        or not isinstance(fence, dict)
        or set(fence)
        != {
            "pid",
            "start_ticks",
            "source_writer",
            "controller_writer",
            "archive_writer",
            "recipient_writer",
        }
        or any(type(fence[k]) is not int or fence[k] <= 0 for k in ("pid", "start_ticks"))
    ):
        raise ValueError("Archive retirement lacks its original kernel/process fence")
    for name in ("source_writer", "controller_writer", "archive_writer", "recipient_writer"):
        lease = fence[name]
        if (
            not isinstance(lease, dict)
            or set(lease) != {"device", "inode"}
            or any(type(lease[k]) is not int or not 0 <= lease[k] < 2**64 for k in lease)
            or lease["inode"] == 0
        ):
            raise ValueError("Archive retirement lacks its original writer descriptor")


def recipient_reference(root: Path, publication: dict[str, Any], budget: OperationBudget) -> None:
    """Require exactly the original private Archive pin and no other managed retention producer."""
    operations = real_directory(root / "maintenance-v1/operations")
    expected = digest(canonical(publication["intent"]))[7:]
    entries = bounded_entries(operations, budget, 2)
    if len(entries) != 1 or entries[0].name != expected:
        raise ValueError("private Archive has another or unknown Snapshot/Archive producer")
    original = real_directory(entries[0])
    if sorted(path.name for path in bounded_entries(original, budget, 2)) != [
        "archive-reference.json"
    ]:
        raise ValueError("private Archive original receiving reference is incomplete or ambiguous")
    if read(original / "archive-reference.json", 128 * 1024 * 1024) != {
        "schema": "roboguide.archive-recipient-reference/v0.1",
        "intent": publication["intent"],
        "target_owner": publication["target_owner"],
    }:
        raise ValueError("private Archive original receiving reference changed")
    if bounded_entries(root / "staging", budget, 1):
        raise ValueError("private Archive has outstanding staging ownership")


def receipt(intent: dict[str, Any]) -> dict[str, Any]:
    """Describe the owner seal independently of physical byte deletion."""
    return {
        "schema": "roboguide.archive-recipient-retirement-receipt/v0.1",
        "intent_digest": digest(canonical(intent)),
        "target_owner": intent["target_owner"],
        "storage_schema": STORAGE,
    }


def validate_seal(
    root: Path, publication: dict[str, Any], budget: OperationBudget
) -> dict[str, Any]:
    """Verify all mandatory terminal metadata before released history may omit receiving bytes."""
    intent = read(root / DIRECTORY / "intent.json")
    validate_intent(intent, publication)
    owner = intent["original_owner"] | {"schema": STORAGE}
    for name in HEADERS:
        budget.check()
        if read(root / name, 4096) != owner:
            raise ValueError("original receiving Archive terminal owner seal is incomplete")
    if artifact_layout.reference_authorities(root, owner):
        raise ValueError("retired private Archive still has known Controller consumers")
    recipient_reference(root, publication, budget)
    if read(root / DIRECTORY / "receipt.json", 4096) != receipt(intent):
        raise ValueError("mandatory original Archive retirement receipt is missing or changed")
    validate_gc_history(root, intent, budget)
    real_directory(root / "objects-v1")
    return intent


def validate_gc_history(
    root: Path, intent: dict[str, Any], budget: OperationBudget, *, allow_unaccounted: bool = False
) -> None:
    """Keep complete original GC history, including uncertain effect outcomes."""
    journal = real_directory(root / DIRECTORY)
    directory = real_directory(journal / "operations")
    head = read(journal / "gc-commands.json")
    if (
        set(head) != {"schema", "command_ids"}
        or head["schema"] != "roboguide.archive-gc-command-head/v0.1"
        or not isinstance(head["command_ids"], list)
        or len(head["command_ids"]) > 4096
        or any(
            not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None
            for key in head["command_ids"]
        )
        or head["command_ids"] != sorted(set(head["command_ids"]))
    ):
        raise ValueError("original Archive GC command inventory is invalid")
    known = set(head["command_ids"])
    seen = set()
    expected = {item["content_digest"]: item["byte_size"] for item in intent["inputs"]}
    for index, path in enumerate(bounded_entries(directory, budget, 4096)):
        budget.check()
        if index >= 4096 or re.fullmatch(r"[0-9a-f]{64}", path.name) is None:
            raise ValueError("original Archive GC history exceeds bounded authority")
        real_directory(path)
        seen.add(path.name)
        names = {item.name for item in bounded_entries(path, budget, 4)}
        if not {"intent.json"} <= names <= {"intent.json", "receipt.json", "receipt.json.pending"}:
            raise ValueError("original Archive GC command evidence is incomplete")
        command = read(path / "intent.json")
        if (
            set(command)
            != {
                "schema",
                "command_id",
                "retirement_digest",
                "profile_digest",
                "principal_uid",
                "reason",
                "inputs",
                "kernel",
                "fence",
            }
            or command["schema"] != "roboguide.archive-recipient-gc-intent/v0.1"
            or not isinstance(command["command_id"], str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", command["command_id"]) is None
            or digest(command["command_id"].encode())[7:] != path.name
            or command["retirement_digest"] != digest(canonical(intent))
            or not _checksum(command["profile_digest"])
            or type(command["principal_uid"]) is not int
            or not 0 <= command["principal_uid"] < 2**32
            or not isinstance(command["reason"], str)
            or not 0 < len(command["reason"].strip()) <= 1024
            or any(ord(c) < 32 for c in command["reason"])
            or not isinstance(command["inputs"], list)
            or len(command["inputs"]) != len(expected)
        ):
            raise ValueError("original Archive GC command lost immutable attribution/input")
        kernel = command["kernel"]
        if (
            not isinstance(kernel, dict)
            or set(kernel) != {"boot_id", "time_namespace", "admitted_ns", "deadline_ns"}
            or not isinstance(kernel["boot_id"], str)
            or len(kernel["boot_id"]) != 36
            or not isinstance(kernel["time_namespace"], str)
            or not 0 < len(kernel["time_namespace"]) <= 128
            or any(
                type(kernel[key]) is not int or not 0 <= kernel[key] < 2**64
                for key in ("admitted_ns", "deadline_ns")
            )
            or not 0 < kernel["deadline_ns"] - kernel["admitted_ns"] <= 3600 * 1_000_000_000
        ):
            raise ValueError("original Archive GC command budget is invalid")
        fence = command["fence"]
        if (
            not isinstance(fence, dict)
            or set(fence)
            != {
                "pid",
                "start_ticks",
                "source_writer",
                "controller_writer",
                "archive_writer",
                "recipient_writer",
            }
            or any(type(fence[key]) is not int or fence[key] <= 0 for key in ("pid", "start_ticks"))
        ):
            raise ValueError("original Archive GC actual process fence is invalid")
        for key in ("source_writer", "controller_writer", "archive_writer", "recipient_writer"):
            lease = fence[key]
            if (
                not isinstance(lease, dict)
                or set(lease) != {"device", "inode"}
                or any(
                    type(lease[name]) is not int or not 0 <= lease[name] < 2**64 for name in lease
                )
                or lease["inode"] == 0
            ):
                raise ValueError("original Archive GC lacks its actual writer description")
        inputs = {}
        for item in command["inputs"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"content_digest", "byte_size", "present", "inode"}
                or not isinstance(item["content_digest"], str)
                or item["content_digest"] not in expected
                or type(item["byte_size"]) is not int
                or item["byte_size"] != expected[item["content_digest"]]
                or item["content_digest"] in inputs
                or type(item["present"]) is not bool
            ):
                raise ValueError("original Archive GC input changed source content")
            inode = item["inode"]
            if (not item["present"] and inode is not None) or (
                item["present"]
                and (
                    not isinstance(inode, dict)
                    or set(inode) != {"device", "inode"}
                    or any(
                        type(inode[key]) is not int or not 0 <= inode[key] < 2**64 for key in inode
                    )
                    or inode["inode"] == 0
                )
            ):
                raise ValueError("original Archive GC lost actual inode evidence")
            inputs[item["content_digest"]] = item
        previous = None
        for filename in ("receipt.json", "receipt.json.pending"):
            if filename not in names:
                continue
            evidence = read(path / filename)
            deleted = [
                {"content_digest": key, "byte_size": value["byte_size"]}
                for key, value in inputs.items()
                if value["present"]
            ]
            missing = [key for key, value in inputs.items() if not value["present"]]
            if evidence != {
                "schema": "roboguide.archive-recipient-gc-receipt/v0.1",
                "intent_digest": digest(canonical(command)),
                "deleted": deleted,
                "already_missing": missing,
            }:
                raise ValueError("original Archive GC receipt changed actual deletion evidence")
            if previous is not None and previous != evidence:
                raise ValueError("original Archive GC pending receipt conflicts")
            previous = evidence
    if not known <= seen or (not allow_unaccounted and known != seen):
        raise ValueError("mandatory original Archive GC command is missing or unaccounted")


def retired(
    root: Path, publication: dict[str, Any], profile_schema: str, budget: OperationBudget
) -> bool:
    """Reject old profiles and incomplete tombstones without guessing emptiness."""
    marker = root / DIRECTORY
    owner = read(root / artifact_layout.OWNER_FILE, 4096, canonical_required=False)
    if not marker.exists() and not marker.is_symlink() and owner.get("schema") != STORAGE:
        return False
    if profile_schema not in {
        SCHEMA,
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
    }:
        raise ValueError("retired private Archive requires explicit recovery v0.14")
    validate_seal(root, publication, budget)
    return True
