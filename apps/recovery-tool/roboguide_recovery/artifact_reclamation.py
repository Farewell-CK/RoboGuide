"""Reclaim source content under actual writers and the original authority closure."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import artifact_gc_journal as journal
from . import artifact_layout, artifact_snapshots, artifact_source_references, bundle
from .archive_reclamation import EffectBudget, _hash, _kernel, _observed_lease, _sync
from .profile import acquire_all, layout, real_directory, validate
from .snapshot_references import canonical, digest, digest_profile


def _writers(components: list[dict[str, Any]], profile_schema: str) -> list[dict[str, Any]]:
    """Independently observe every named original lease already acquired by this actual process."""
    result = []
    for component in components:
        databases, _, locks = layout(component, profile_schema=profile_schema)
        if component["kind"] == "memory":
            locks += [".memory-use.lock", *databases]
        for name in locks:
            path = Path(component["root"]) / name
            result.append(
                {
                    "component_id": component["component_id"],
                    "path": str(path),
                    **_observed_lease(path),
                }
            )
    return sorted(result, key=lambda value: str(value["path"]))


def _closure(
    components: list[dict[str, Any]],
    root: Path,
    owner: dict[str, Any],
    checksum: str,
    budget: bundle.Budget,
    profile_schema: str,
) -> list[dict[str, Any]]:
    """Freeze WAL-consistent SQL before inspecting the original content authorities."""
    known = artifact_layout.reference_authorities(root, owner)
    archives = {component["root"] for component in components if component["kind"] == "archive"}
    for _, request, receipt in artifact_snapshots.operations(
        root, budget, profile_schema=profile_schema
    ):
        if receipt is None and any(
            value["content_digest"] == checksum for value in request["inputs"]
        ):
            raise ValueError("active original Snapshot still retains source content")
    proofs = {}
    with TemporaryDirectory(prefix="roboguide-original-content-") as directory:
        for component in components:
            if component["kind"] != "controller":
                continue
            source = Path(component["root"]) / component["database"]
            destination = Path(directory) / (component["component_id"] + ".sqlite3")

            def progress(_status: int, _remaining: int, _total: int) -> None:
                """Keep the actual native SQLite copy inside the first original GC budget."""
                budget.check()

            with (
                closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as retained,
                closing(sqlite3.connect(destination)) as copied,
            ):
                count = retained.execute("PRAGMA page_count").fetchone()[0]
                size = retained.execute("PRAGMA page_size").fetchone()[0]
                budget.check(byte_count=count * size, file_count=1)
                retained.backup(copied, pages=256, progress=progress)
                row = copied.execute(
                    "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'"
                ).fetchone()
            if row is None or row[0] not in known:
                continue
            authority = row[0]
            if authority in proofs:
                raise ValueError("original source Controller authority is declared more than once")
            proof = artifact_source_references.inspect(
                destination,
                owner["owner_id"],
                checksum,
                budget,
                archives,
                profile_schema=profile_schema,
            )
            proofs[authority] = proof | {"component_id": component["component_id"]}
    if set(proofs) != known:
        raise ValueError("source GC omits an original known Controller reference authority")
    return [proofs[key] for key in sorted(known)]


def run(
    profile: dict[str, Any],
    component_id: str,
    checksum: str,
    byte_size: int,
    command_id: str,
    reason: str,
) -> dict[str, Any]:
    """Persist the original tombstone before unlink; retry never repeats effects."""
    components = validate(profile)
    if (
        profile["schema"] not in journal.PROFILES
        or profile["artifact_replica_sets"]
        or any(value["kind"] == "replica" for value in components)
    ):
        raise ValueError(
            "source GC requires explicit standalone recovery v0.16 or v0.18; "
            "online Replica authority is not yet supported"
        )
    selected = [
        value
        for value in components
        if value["kind"] == "artifact" and value["component_id"] == component_id
    ]
    if len(selected) != 1 or selected[0]["storage_schema"] != journal.STORAGE:
        raise ValueError("source GC requires one explicitly declared original storage v5 owner")
    if (
        journal.COMMAND.fullmatch(command_id) is None
        or journal.CHECKSUM.fullmatch(checksum) is None
        or not journal._integer(byte_size)
        or byte_size > 4 * 1024**3
        or not journal._text(reason, 1024)
    ):
        raise ValueError("source GC command/content/retention input is invalid")
    budget = bundle.Budget(profile)
    root = real_directory(Path(selected[0]["root"]))
    with acquire_all(components, profile_schema=profile["schema"]):
        owner = artifact_layout.validate_storage(root, profile["schema"])
        assert owner is not None
        commands, tombstones = journal.validate(root, owner["owner_id"], budget)
        if command_id in commands:
            original, receipt = commands[command_id]
            if any(
                original[key] != value
                for key, value in (
                    ("component_id", component_id),
                    ("content_digest", checksum),
                    ("byte_size", byte_size),
                    ("reason", reason),
                )
            ):
                raise ValueError("source GC original command has different immutable input")
            return {
                "schema": "roboguide.artifact-gc-observation/v0.1",
                "intent": original,
                "receipt": receipt,
                "phase": "unknown" if receipt is None else "completed",
                "replayed": True,
                "effects_performed": 0,
            }
        if len(commands) >= journal.MAX_COMMANDS:
            raise ValueError("original source GC command history is full")
        closure = _closure(components, root, owner, checksum, budget, profile["schema"])
        path = root / "objects-v1/sha256" / checksum[7:9] / checksum[7:]
        inode = None
        if path.exists() or path.is_symlink():
            observed, inode = _hash(path, byte_size, budget)
            if observed != checksum:
                raise ValueError("original source GC bytes differ from their complete identity")
        elif checksum not in tombstones:
            raise ValueError("missing source bytes cannot create original GC deletion authority")
        if checksum in tombstones and tombstones[checksum]["byte_size"] != byte_size:
            raise ValueError("original source GC tombstone has another byte identity")
        kernel = _kernel(budget)
        effect = EffectBudget(budget, kernel)
        intent = {
            "schema": "roboguide.artifact-gc-intent/v0.1",
            "command_id": command_id,
            "component_id": component_id,
            "store_owner_id": owner["owner_id"],
            "profile_digest": digest_profile(profile),
            "content_digest": checksum,
            "byte_size": byte_size,
            "reason": reason,
            "kernel": kernel,
            "fence": {
                "pid": os.getpid(),
                "uid": os.getuid(),
                "start_ticks": int(
                    Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
                ),
                "writers": _writers(components, profile["schema"]),
            },
            "content_inode": inode,
            "known_authorities": [value["authority_id"] for value in closure],
            "closure": closure,
            "original_tombstone": None
            if checksum not in tombstones
            else tombstones[checksum]["first_command_id"],
        }
        journal.validate_intent(intent, owner["owner_id"])
        if len(canonical(intent)) > journal.MAX_RECORD:
            raise ValueError("original source GC intent exceeds its fixed byte bound")
        tombstone = tombstones.get(
            checksum,
            {
                "schema": "roboguide.artifact-gc-tombstone/v0.1",
                "store_owner_id": owner["owner_id"],
                "content_digest": checksum,
                "byte_size": byte_size,
                "first_command_id": command_id,
                "first_intent_digest": digest(canonical(intent)),
            },
        )
        with closing(sqlite3.connect(root / journal.DATABASE)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            effect.check()
            with connection:
                connection.execute(
                    "INSERT INTO gc_commands VALUES(?,?,NULL)",
                    (command_id, canonical(intent).decode()),
                )
                if checksum not in tombstones:
                    connection.execute(
                        "INSERT INTO gc_tombstones VALUES(?,?,?)",
                        (checksum, command_id, canonical(tombstone).decode()),
                    )
                for key, values in (
                    ("command_ids", sorted([*commands, command_id])),
                    ("tombstone_digests", sorted(set(tombstones) | {checksum})),
                ):
                    connection.execute(
                        "UPDATE gc_metadata SET value=? WHERE key=?",
                        (canonical(values).decode(), key),
                    )
            _sync((root / journal.DATABASE).parent)
            # Original command and irreversible tombstone are durable before the first unlink.
            effect.check()
            if inode is not None:
                actual = path.lstat()
                if (actual.st_dev, actual.st_ino) != (
                    inode["device"],
                    inode["inode"],
                ) or path.is_symlink():
                    raise ValueError("original source GC inode changed before unlink")
                effect.check()
                path.unlink()
                _sync(path.parent)
            effect.check()
            receipt = {
                "schema": "roboguide.artifact-gc-receipt/v0.1",
                "command_id": command_id,
                "intent_digest": digest(canonical(intent)),
                "tombstone_digest": digest(canonical(tombstone)),
                "result": "deleted" if inode is not None else "already_missing",
                "completed_ns": time.monotonic_ns(),
                "deleted_inode": inode,
            }
            with connection:
                connection.execute(
                    "UPDATE gc_commands SET receipt_json=? "
                    "WHERE command_id=? AND receipt_json IS NULL",
                    (canonical(receipt).decode(), command_id),
                )
            _sync((root / journal.DATABASE).parent)
        journal.validate(root, owner["owner_id"], budget)
        return {
            "schema": "roboguide.artifact-gc-observation/v0.1",
            "intent": intent,
            "receipt": receipt,
            "phase": "completed",
            "replayed": False,
            "effects_performed": 0 if inode is None else 1,
        }
