"""Explicit native GC SQL rollback under original writers; forensic files grant no authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from . import artifact_gc_journal as gc
from . import artifact_layout, bundle
from .archive_reclamation import EffectBudget, _kernel, _sync
from .profile import acquire_writers, real_directory, validate, validate_held_storage
from .snapshot_references import canonical, digest, digest_profile

INTENT_SCHEMA = "roboguide.artifact-sql-recovery-intent/v0.1"
RECEIPT_SCHEMA = "roboguide.artifact-sql-recovery-receipt/v0.1"
MAX_RECORD = 16 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
CHUNK = 64 * 1024


def _record(path: Path) -> dict[str, Any]:
    """Read bounded immutable original evidence without following a replaced leaf."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or actual.st_size > MAX_RECORD:
            raise ValueError("original SQL recovery evidence is not a bounded regular record")
        raw = stream.read(MAX_RECORD + 1)
    value = json.loads(raw)
    if not isinstance(value, dict) or len(raw) > MAX_RECORD or canonical(value) != raw:
        raise ValueError("original SQL recovery evidence is not complete canonical JSON")
    return value


def _publish(path: Path, value: dict[str, Any], effect: EffectBudget) -> None:
    """Fsync an original pending record before no-overwrite publication; never rewrite history."""
    effect.check()
    raw = canonical(value)
    if len(raw) > MAX_RECORD:
        raise ValueError("original SQL recovery intent/receipt exceeds its bound")
    pending = path.with_suffix(path.suffix + ".pending")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)
    effect.check()
    os.link(pending, path, follow_symlinks=False)
    _sync(path.parent)
    pending.unlink()
    _sync(path.parent)


def _open(path: Path) -> BinaryIO:
    """Keep a no-follow actual original file descriptor alive through the native operation."""
    real_directory(path.parent)
    stream = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb")
    actual, named = os.fstat(stream.fileno()), path.lstat()
    if (
        not stat.S_ISREG(actual.st_mode)
        or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
        or not 0 < actual.st_size <= MAX_FILE
    ):
        stream.close()
        raise ValueError("original SQL/journal descriptor is absent, replaced or unbounded")
    return stream


def _hash(stream: BinaryIO, budget: bundle.Budget) -> dict[str, Any]:
    """Hash exact original bytes under the first operation budget without closing its descriptor."""
    stream.seek(0)
    actual = os.fstat(stream.fileno())
    checksum = hashlib.sha256()
    size = 0
    while block := stream.read(CHUNK):
        budget.check(byte_count=len(block))
        size += len(block)
        if size > actual.st_size or size > MAX_FILE:
            raise ValueError("original SQL/journal grew beyond its frozen bound")
        checksum.update(block)
    if size != actual.st_size:
        raise ValueError("original SQL/journal was truncated during inspection")
    return {
        "device": actual.st_dev,
        "inode": actual.st_ino,
        "byte_size": size,
        "content_digest": "sha256:" + checksum.hexdigest(),
    }


def _same(path: Path, stream: BinaryIO, original: dict[str, Any]) -> None:
    """Refuse a changed original file name, inode or size before any native writable access."""
    actual, named = os.fstat(stream.fileno()), path.lstat()
    expected = original["device"], original["inode"], original["byte_size"]
    if (
        not stat.S_ISREG(named.st_mode)
        or (actual.st_dev, actual.st_ino, actual.st_size) != expected
        or (named.st_dev, named.st_ino, named.st_size) != expected
    ):
        raise ValueError("original SQL recovery source descriptor/path changed")


def _delete_mode(path: Path, stream: BinaryIO) -> None:
    """Reject WAL/foreign headers before interpreting any file as an original rollback journal."""
    stream.seek(0)
    header = stream.read(20)
    if header[:16] != b"SQLite format 3\0" or len(header) != 20 or header[18:] != b"\x01\x01":
        raise ValueError("native GC SQL recovery requires the original DELETE-mode header")
    if any(
        Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink()
        for suffix in ("-wal", "-shm")
    ):
        raise ValueError("native GC SQL recovery refuses WAL authority")


def _copy(stream: BinaryIO, path: Path, original: dict[str, Any], effect: EffectBudget) -> None:
    """Retain original forensic bytes before writable SQLite, preserving partial files."""
    stream.seek(0)
    checksum = hashlib.sha256()
    size = 0
    with path.open("xb") as target:
        while block := stream.read(CHUNK):
            effect.check()
            effect.budget.check(byte_count=len(block))
            size += len(block)
            if size > original["byte_size"]:
                raise ValueError("original forensic source grew during copy")
            target.write(block)
            checksum.update(block)
        target.flush()
        os.fsync(target.fileno())
    _sync(path.parent)
    if (
        size != original["byte_size"]
        or "sha256:" + checksum.hexdigest() != original["content_digest"]
    ):
        raise ValueError("original forensic SQL/journal copy differs")
    effect.check()


def _consumers(components: list[dict[str, Any]], root: Path, owner: dict[str, Any]) -> list[str]:
    """Require every known Controller authority exactly once under the complete held profile."""
    known = artifact_layout.reference_authorities(root, owner)
    found: set[str] = set()
    for component in components:
        if component["kind"] != "controller":
            continue
        database = Path(component["root"]) / component["database"]
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'"
            ).fetchone()
        if row is not None and row[0] in known:
            if row[0] in found:
                raise ValueError("original Controller reference authority is declared twice")
            found.add(row[0])
    if found != known:
        raise ValueError("SQL recovery omits a known original Controller reference authority")
    return sorted(found)


def _snapshot(value: Any) -> bool:
    """Validate the complete bounded file observation without querying current source files."""
    return (
        isinstance(value, dict)
        and set(value) == {"device", "inode", "byte_size", "content_digest"}
        and all(gc._integer(value[key]) for key in ("device", "inode", "byte_size"))
        and value["inode"] > 0
        and 0 < value["byte_size"] <= MAX_FILE
        and isinstance(value["content_digest"], str)
        and gc.CHECKSUM.fullmatch(value["content_digest"]) is not None
    )


def _validate_intent(value: dict[str, Any]) -> None:
    """Refuse incomplete/corrupt original metadata; evidence never reconstructs missing facts."""
    if (
        set(value)
        != {
            "schema",
            "operation_id",
            "component_id",
            "store_owner_id",
            "profile",
            "profile_digest",
            "reason",
            "kernel",
            "process",
            "writers",
            "known_authorities",
            "sql",
            "journal",
        }
        or value["schema"] != INTENT_SCHEMA
        or not isinstance(value["operation_id"], str)
        or re.fullmatch(r"[a-f0-9]{32}", value["operation_id"]) is None
        or not isinstance(value["profile"], dict)
        or digest_profile(value["profile"]) != value["profile_digest"]
        or not isinstance(value["store_owner_id"], str)
        or gc.OWNER.fullmatch(value["store_owner_id"]) is None
        or not gc._text(value["reason"], 1024)
        or not _snapshot(value["sql"])
        or not _snapshot(value["journal"])
    ):
        raise ValueError("original SQL recovery intent identity or original files differ")
    components = validate(value["profile"])
    if value["profile"]["schema"] not in {
        "roboguide.recovery-profile/v0.17",
        "roboguide.recovery-profile/v0.18",
        "roboguide.recovery-profile/v0.19",
        "roboguide.recovery-profile/v0.20",
        "roboguide.recovery-profile/v0.21",
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
    } or not any(
        row["component_id"] == value["component_id"]
        and row["kind"] == "artifact"
        and row["storage_schema"] == gc.STORAGE
        for row in components
    ):
        raise ValueError("original SQL recovery component/profile differs")
    kernel, process = value["kernel"], value["process"]
    if (
        not isinstance(kernel, dict)
        or set(kernel) != {"boot_id", "time_namespace", "admitted_ns", "deadline_ns"}
        or not all(gc._text(kernel[key], 128) for key in ("boot_id", "time_namespace"))
        or not all(gc._integer(kernel[key]) for key in ("admitted_ns", "deadline_ns"))
        or kernel["deadline_ns"] <= kernel["admitted_ns"]
        or not isinstance(process, dict)
        or set(process) != {"pid", "uid", "effective_uid", "pid_namespace", "start_ticks"}
        or not all(
            gc._integer(process[key]) for key in ("pid", "uid", "effective_uid", "start_ticks")
        )
        or process["pid"] == 0
        or process["start_ticks"] == 0
        or not gc._text(process["pid_namespace"], 128)
    ):
        raise ValueError("original SQL recovery kernel/process evidence differs")
    writers, known = value["writers"], value["known_authorities"]
    if (
        not isinstance(writers, list)
        or not 1 <= len(writers) <= 8192
        or any(
            not isinstance(row, dict)
            or set(row) != {"path", "device", "inode"}
            or not gc._text(row["path"], 4096)
            or not Path(row["path"]).is_absolute()
            or not gc._integer(row["device"])
            or not gc._integer(row["inode"], positive=True)
            for row in writers
        )
    ):
        raise ValueError("original SQL recovery writer evidence differs")
    paths = [row["path"] for row in writers]
    if paths != sorted(set(paths)):
        raise ValueError("original SQL recovery writer paths are duplicated or reordered")
    if (
        not isinstance(known, list)
        or len(known) > 1024
        or any(not isinstance(row, str) or gc.AUTHORITY.fullmatch(row) is None for row in known)
        or known != sorted(set(known))
    ):
        raise ValueError("original SQL recovery known consumer evidence differs")


def observe(directory: Path) -> dict[str, Any]:
    """Observe immutable evidence without source leases, writable SQL or budget renewal."""
    real_directory(directory)
    if not (directory / "intent.json").exists():
        return {"phase": "unavailable", "writable_sql_opens": 0}
    intent = _record(directory / "intent.json")
    _validate_intent(intent)
    receipt = _record(directory / "receipt.json") if (directory / "receipt.json").exists() else None
    if receipt is not None and (
        set(receipt)
        != {
            "schema",
            "operation_id",
            "intent_digest",
            "sql_recovery_finished_ns",
            "result_sql",
            "original_gc_semantics_verified",
            "native_writable_sql_opens",
        }
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("operation_id") != intent["operation_id"]
        or receipt.get("intent_digest") != digest(canonical(intent))
        or type(receipt.get("sql_recovery_finished_ns")) is not int
        or not intent["kernel"]["admitted_ns"]
        <= receipt["sql_recovery_finished_ns"]
        < intent["kernel"]["deadline_ns"]
        or receipt.get("original_gc_semantics_verified") is not True
        or type(receipt.get("native_writable_sql_opens")) is not int
        or receipt["native_writable_sql_opens"] != 1
        or not _snapshot(receipt.get("result_sql"))
        or any(receipt["result_sql"][key] != intent["sql"][key] for key in ("device", "inode"))
    ):
        raise ValueError("original SQL recovery receipt or original budget differs")
    if receipt is not None:
        observation_deadline = time.monotonic() + 30
        for key, name in (("sql", "sql.raw"), ("journal", "rollback-journal.raw")):
            with _open(directory / name) as stream:
                checksum = hashlib.sha256()
                size = 0
                while block := stream.read(CHUNK):
                    size += len(block)
                    if time.monotonic() >= observation_deadline or size > intent[key]["byte_size"]:
                        raise ValueError("readonly forensic observation exceeded its own bound")
                    checksum.update(block)
                if (
                    size != intent[key]["byte_size"]
                    or "sha256:" + checksum.hexdigest() != intent[key]["content_digest"]
                ):
                    raise ValueError("original completed SQL recovery lost exact forensic bytes")
    return {
        "phase": "unknown" if receipt is None else "completed",
        "intent": intent,
        "receipt": receipt,
        "writable_sql_opens": 0,
    }


def _native(path: Path, effect: EffectBudget) -> None:
    """Recover the original journal with SQLite and finish an exclusive integrity read."""
    effect.check()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.25)) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, gc.MAX_RECORD + 1)
        connection.execute("PRAGMA synchronous=FULL")
        effect.check()
        connection.execute("BEGIN EXCLUSIVE")
        try:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("native recovered GC SQL failed its complete integrity check")
            effect.check()
        finally:
            connection.rollback()
    effect.check()


def run(
    profile: dict[str, Any],
    component_id: str,
    expected_sql: str,
    expected_journal: str,
    directory: Path,
    reason: str,
) -> dict[str, Any]:
    """Persist pinned intent/forensics before repair; existing directories only observe."""
    if directory.exists() or directory.is_symlink():
        observation = observe(directory)
        original = observation.get("intent")
        if original is not None and (
            original["profile_digest"] != digest_profile(profile)
            or original["component_id"] != component_id
            or original["sql"]["content_digest"] != expected_sql
            or original["journal"]["content_digest"] != expected_journal
            or original["reason"] != reason
        ):
            raise ValueError("original SQL recovery directory has different immutable input")
        return observation | {"replayed": True}
    components = validate(profile)
    if (
        profile["schema"]
        not in {"roboguide.recovery-profile/v0.17", "roboguide.recovery-profile/v0.18"}
        or profile["artifact_replica_sets"]
        or any(value["kind"] == "replica" for value in components)
        or gc.CHECKSUM.fullmatch(expected_sql) is None
        or gc.CHECKSUM.fullmatch(expected_journal) is None
        or not gc._text(reason, 1024)
    ):
        raise ValueError("explicit standalone SQL recovery profile/digests/reason are invalid")
    selected = [value for value in components if value["component_id"] == component_id]
    if (
        len(selected) != 1
        or selected[0]["kind"] != "artifact"
        or selected[0]["storage_schema"] != gc.STORAGE
    ):
        raise ValueError("SQL recovery requires one declared original v5 Artifact")
    parent = real_directory(directory.parent)
    directory = parent / directory.name
    if any(directory.is_relative_to(Path(value["root"])) for value in components):
        raise ValueError("forensic SQL recovery evidence must stay outside every source root")
    budget = bundle.Budget(profile)
    root = real_directory(Path(selected[0]["root"]))
    with acquire_writers(components, profile_schema=profile["schema"]) as held:
        validate_held_storage(components, profile["schema"], held, deferred_gc_root=root)
        owner = artifact_layout.validate_storage(root, profile["schema"], deferred_gc=held)
        assert owner is not None
        known = _consumers(components, root, owner)
        database = root / gc.DATABASE
        journal_path = Path(str(database) + "-journal")
        with _open(database) as sql:
            _delete_mode(database, sql)
            with _open(journal_path) as journal:
                original_sql, original_journal = _hash(sql, budget), _hash(journal, budget)
                if (
                    original_sql["content_digest"] != expected_sql
                    or original_journal["content_digest"] != expected_journal
                ):
                    raise ValueError(
                        "actual original SQL/journal differs from explicit pinned input"
                    )
                try:
                    gc.validate(root, owner["owner_id"], budget)
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode != sqlite3.SQLITE_READONLY_ROLLBACK:
                        raise ValueError(
                            "GC SQL does not have the admitted native rollback condition"
                        ) from error
                else:
                    raise ValueError(
                        "GC SQL is already readable; writable recovery is not admitted"
                    )
                held.verify_components(components, profile["schema"])
                kernel = _kernel(budget)
                effect = EffectBudget(budget, kernel)
                intent = {
                    "schema": INTENT_SCHEMA,
                    "operation_id": secrets.token_hex(16),
                    "component_id": component_id,
                    "store_owner_id": owner["owner_id"],
                    "profile": profile,
                    "profile_digest": digest_profile(profile),
                    "reason": reason,
                    "kernel": kernel,
                    "process": {
                        "pid": os.getpid(),
                        "uid": os.getuid(),
                        "effective_uid": os.geteuid(),
                        "pid_namespace": os.readlink("/proc/self/ns/pid"),
                        "start_ticks": int(
                            Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
                        ),
                    },
                    "writers": held.evidence(),
                    "known_authorities": known,
                    "sql": original_sql,
                    "journal": original_journal,
                }
                _validate_intent(intent)
                effect.check()
                directory.mkdir(mode=0o700)
                _sync(parent)
                _publish(directory / "intent.json", intent, effect)
                _copy(sql, directory / "sql.raw", original_sql, effect)
                _copy(journal, directory / "rollback-journal.raw", original_journal, effect)
                _same(database, sql, original_sql)
                _same(journal_path, journal, original_journal)
                if _hash(sql, budget) != original_sql or _hash(journal, budget) != original_journal:
                    raise ValueError(
                        "original SQL recovery input bytes changed before native access"
                    )
                _native(database, effect)
                current = database.lstat()
                if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
                    original_sql["device"],
                    original_sql["inode"],
                ):
                    raise ValueError("actual original GC SQL inode changed during native recovery")
                validate_held_storage(components, profile["schema"], held)
                gc.validate(root, owner["owner_id"], budget)
                result_sql = _hash(sql, budget)
                receipt = {
                    "schema": RECEIPT_SCHEMA,
                    "operation_id": intent["operation_id"],
                    "intent_digest": digest(canonical(intent)),
                    "sql_recovery_finished_ns": time.monotonic_ns(),
                    "result_sql": result_sql,
                    "original_gc_semantics_verified": True,
                    "native_writable_sql_opens": 1,
                }
                effect.check()
                _publish(directory / "receipt.json", receipt, effect)
    return observe(directory) | {"replayed": False, "writable_sql_opens": 1}
