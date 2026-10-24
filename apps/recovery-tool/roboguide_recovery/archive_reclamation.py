"""Retire and reclaim one actual private Archive recipient under all original OS writer leases."""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import stat
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from . import archive_references, artifact_layout, bundle
from . import archive_retirement as retirement
from .profile import IDENTIFIER, acquire_all, real_directory, validate
from .snapshot_references import canonical, digest, digest_profile


class EffectBudget:
    """Freeze one actual Linux kernel effect deadline separately from readonly observation time."""

    def __init__(self, budget: bundle.Budget, kernel: dict[str, Any]) -> None:
        """Keep the first durable effect budget without a replacement deadline."""
        self.budget, self.kernel = budget, kernel

    def check(self) -> None:
        """Refuse effects outside the first kernel, namespace and deadline."""
        self.budget.check()
        if (
            Path("/proc/sys/kernel/random/boot_id").read_text().strip() != self.kernel["boot_id"]
            or os.readlink("/proc/self/ns/time") != self.kernel["time_namespace"]
            or not self.kernel["admitted_ns"] <= time.monotonic_ns() < self.kernel["deadline_ns"]
        ):
            raise ValueError("original Archive reclamation effect budget is expired or unavailable")


def _sync(path: Path) -> None:
    """Persist the actual containing real directory after immutable publication or unlink."""
    descriptor = os.open(real_directory(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(path: Path, value: dict[str, Any], effect: EffectBudget) -> None:
    """Publish exact original canonical bytes with durable pending evidence and no overwrite."""
    effect.check()
    raw = canonical(value)
    if len(raw) > retirement.MAX_RECORD_BYTES:
        raise ValueError("Archive reclamation original record exceeds its byte bound")
    if path.exists() or path.is_symlink():
        if retirement.read(path) != value:
            raise ValueError("Archive reclamation immutable original record conflicts")
        return
    pending = path.with_suffix(path.suffix + ".pending")
    if pending.exists() or pending.is_symlink():
        if retirement.read(pending) != value:
            raise ValueError("Archive reclamation pending original record conflicts")
    else:
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


def _lease(path: Path, stack: ExitStack) -> dict[str, int]:
    """Acquire the existing exclusive writer; reject path replacement."""
    real_directory(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    actual, named = os.fstat(descriptor), path.lstat()
    if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
        named.st_dev,
        named.st_ino,
    ):
        raise ValueError("original Archive writer descriptor was replaced")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise ValueError("original Archive recipient writer is active") from error
    stack.callback(fcntl.flock, descriptor, fcntl.LOCK_UN)
    return {"device": actual.st_dev, "inode": actual.st_ino}


def _observed_lease(path: Path) -> dict[str, int]:
    """Observe a named lease already held by acquire_all; another open must fail to acquire it."""
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        actual, named = os.fstat(descriptor), path.lstat()
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
            named.st_dev,
            named.st_ino,
        ):
            raise ValueError("original source writer descriptor changed")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"device": actual.st_dev, "inode": actual.st_ino}
        raise ValueError("original source writer was not independently held")
    finally:
        os.close(descriptor)


def _kernel(budget: bundle.Budget) -> dict[str, Any]:
    """Freeze actual first kernel/time-namespace samples without wall-clock conversion."""
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "time_namespace": os.readlink("/proc/self/ns/time"),
        "admitted_ns": time.monotonic_ns(),
        "deadline_ns": budget.deadline_ns,
    }


def _fence(
    source: dict[str, Any],
    source_cas: dict[str, Any],
    publication: dict[str, Any],
    recipient: dict[str, int],
) -> dict[str, Any]:
    """Retain this actual process and all independently held writer identities."""
    return {
        "pid": os.getpid(),
        "start_ticks": int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]),
        "recipient_writer": recipient,
        "source_writer": _observed_lease(Path(source_cas["root"]) / ".writer.lock"),
        "controller_writer": _observed_lease(
            Path(source["root"]) / (source["database"] + ".writer.lock")
        ),
        "archive_writer": _observed_lease(
            Path(publication["intent"]["request"]["destination"]) / ".archive-writer.lock"
        ),
    }


def _publish_head(journal: Path, head: dict[str, Any], effect: EffectBudget) -> None:
    """Complete validated original head metadata under a new explicit GC budget."""
    known = set(retirement.read(journal / "gc-commands.json")["command_ids"])
    final = journal / ".gc-commands-update.json"
    pending = final.with_suffix(final.suffix + ".pending")
    for path in (final, pending):
        if path.exists() or path.is_symlink():
            previous = retirement.read(path)
            if (
                set(previous) != {"schema", "command_ids"}
                or previous["schema"] != head["schema"]
                or not isinstance(previous["command_ids"], list)
                or any(not isinstance(key, str) for key in previous["command_ids"])
                or previous["command_ids"] != sorted(set(previous["command_ids"]))
                or not known <= set(previous["command_ids"]) <= set(head["command_ids"])
            ):
                raise ValueError("original Archive GC pending inventory conflicts")
            effect.check()
            path.unlink()
            _sync(journal)
    publish(final, head, effect)
    effect.check()
    os.replace(final, journal / "gc-commands.json")
    _sync(journal)


def _hash(path: Path, size: int, budget: bundle.Budget) -> tuple[str, dict[str, int]]:
    """Verify actual original bytes and their inode before any deletion authority is admitted."""
    real_directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or actual.st_size != size:
            raise ValueError("private Archive original byte length or type changed")
        result = hashlib.sha256()
        total = 0
        while block := stream.read(bundle.CHUNK):
            total += len(block)
            budget.check(byte_count=len(block))
            if total > size:
                raise ValueError("private Archive bytes grew beyond original input")
            result.update(block)
        if total != size:
            raise ValueError("private Archive original bytes are truncated")
        return "sha256:" + result.hexdigest(), {"device": actual.st_dev, "inode": actual.st_ino}


def _input_path(root: Path, item: dict[str, Any]) -> Path:
    """Derive only the validated original content path."""
    checksum = str(item["content_digest"])[7:]
    return root / "objects-v1/sha256" / checksum[:2] / checksum


def _current_inputs(
    root: Path, publication: dict[str, Any], budget: bundle.Budget, *, released: bool
) -> list[dict[str, Any]]:
    """Reject extra content and distinguish missing observations from unlink."""
    expected = {_input_path(root, item): item for item in publication["intent"]["inputs"]}
    actual: set[Path] = set()
    sha = root / "objects-v1/sha256"
    if sha.exists() or sha.is_symlink():
        for prefix in real_directory(sha).iterdir():
            budget.check(file_count=1)
            real_directory(prefix)
            for path in prefix.iterdir():
                budget.check(file_count=1)
                if path not in expected or path.is_symlink() or not path.is_file():
                    raise ValueError("private Archive contains unknown or foreign CAS content")
                actual.add(path)
    if not released and actual != expected.keys():
        raise ValueError("unretired private Archive original bytes are missing")
    result = []
    for path, item in expected.items():
        if path not in actual:
            result.append(item | {"present": False, "inode": None})
            continue
        checksum, inode = _hash(path, item["byte_size"], budget)
        if checksum != item["content_digest"]:
            raise ValueError("private Archive original bytes are corrupt")
        result.append(item | {"present": True, "inode": inode})
    return result


def _seal(root: Path, intent: dict[str, Any], effect: EffectBudget) -> None:
    """Irreversibly fence every old reader/writer before reclaiming any receiving content."""
    owner = intent["original_owner"] | {"schema": retirement.STORAGE}
    for name in retirement.HEADERS:
        effect.check()
        current = retirement.read(root / name, 4096, canonical_required=False)
        if current not in (intent["original_owner"], owner):
            raise ValueError("private Archive original owner seal conflicts")
        if current == owner:
            continue
        pending = root / (".archive-seal-" + name.replace("/", "_"))
        publish(pending, owner, effect)
        effect.check()
        os.replace(pending, root / name)
        _sync((root / name).parent)
    publish(root / retirement.DIRECTORY / "receipt.json", retirement.receipt(intent), effect)


def _resolve(
    components: list[dict[str, Any]],
    controller_id: str,
    command_id: str,
    expected: str,
    budget: bundle.Budget,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve the original compacted producer through readonly SQL and owners."""
    controllers = [
        item
        for item in components
        if item["component_id"] == controller_id and item["kind"] == "controller"
    ]
    if len(controllers) != 1 or any(item["kind"] == "replica" for item in components):
        raise ValueError("private Archive reclamation requires its original standalone Controller")
    source = controllers[0]
    database = Path(source["root"]) / source["database"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        originals = archive_references.operations(
            connection, retirement.SCHEMA, budget, current=True
        )
    selected = [entry for entry in originals if entry[0]["request"]["command_id"] == command_id]
    if len(selected) != 1:
        raise ValueError("original Archive producer command is absent")
    intent, state, receipt = selected[0]
    if state != "compacted" or digest(canonical(intent)) != expected:
        raise ValueError("private Archive lacks its exact original compact receipt/input")
    archive = next(
        (
            item
            for item in components
            if item["kind"] == "archive" and item["root"] == intent["request"]["destination"]
        ),
        None,
    )
    if archive is None or controller_id not in archive["depends_on"]:
        raise ValueError("private Archive destination/original Controller was not declared")
    owners = []
    authorities = set()
    for item in components:
        if item["kind"] != "controller":
            continue
        original_database = Path(item["root"]) / item["database"]
        with closing(
            sqlite3.connect(original_database.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'"
            ).fetchone()
        if row is None or row[0] in authorities:
            raise ValueError(
                "declared original Controller authorities are incomplete or duplicated"
            )
        authorities.add(row[0])
    for item in components:
        if item["kind"] != "artifact":
            continue
        owner = artifact_layout.validate_storage(Path(item["root"]), retirement.SCHEMA)
        assert owner is not None
        if not artifact_layout.reference_authorities(Path(item["root"]), owner) <= authorities:
            raise ValueError("known original source consumer is absent from maintenance")
        if owner["owner_id"] == intent["request"]["source_owner"]:
            owners.append(item)
            if intent["request"]["authority_id"] not in artifact_layout.reference_authorities(
                Path(item["root"]), owner
            ):
                raise ValueError("private Archive source lost its original consumer binding")
    if len(owners) != 1 or owners[0]["component_id"] not in archive["depends_on"]:
        raise ValueError("private Archive lacks its actual declared source CAS")
    directory = real_directory(
        Path(archive["root"])
        / "archives-v1"
        / hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
        / ("archive-" + hashlib.sha256(command_id.encode()).hexdigest())
    )
    publication = retirement.read(directory / "publication.json", 128 * 1024 * 1024)
    if (
        publication.get("intent") != intent
        or publication.get("target_owner") == intent["request"]["source_owner"]
        or digest(canonical(publication)) != receipt
    ):
        raise ValueError("private Archive original publication/source receipt conflicts")
    for filename, expected_bytes in (
        ("intent.json", canonical(intent)),
        ("checkpoint.json", canonical(intent["checkpoint"])),
        ("target-owner.json", canonical(publication["target_owner"])),
    ):
        with bundle._regular(directory / filename) as stream:
            raw = stream.read(128 * 1024 * 1024 + 1)
        budget.check(byte_count=len(raw))
        if raw != expected_bytes:
            raise ValueError("private Archive original retained metadata changed")
    for filename, expected_digest in (
        ("events.jsonl", intent["manifest"]["segment_digest"]),
        ("catalog.json", intent["catalog_digest"]),
    ):
        with bundle._regular(directory / filename) as stream:
            checksum = hashlib.sha256()
            while block := stream.read(bundle.CHUNK):
                budget.check(byte_count=len(block))
                checksum.update(block)
        if "sha256:" + checksum.hexdigest() != expected_digest:
            raise ValueError("private Archive original event/Catalog bytes changed")
    return directory, publication, source, owners[0]


def run(
    profile: dict[str, Any],
    controller_id: str,
    original_command: str,
    expected_intent: str,
    command_id: str,
    reason: str,
    *,
    reclaim: bool,
) -> dict[str, Any]:
    """Execute one OS-admin operation; exact retries only observe history."""
    if (
        profile.get("schema")
        not in {
            retirement.SCHEMA,
            "roboguide.recovery-profile/v0.15",
            "roboguide.recovery-profile/v0.16",
            "roboguide.recovery-profile/v0.17",
            "roboguide.recovery-profile/v0.18",
            "roboguide.recovery-profile/v0.19",
            "roboguide.recovery-profile/v0.20",
        }
        or IDENTIFIER.fullmatch(command_id) is None
        or not reason.strip()
        or len(reason) > 1024
        or any(ord(c) < 32 for c in reason)
    ):
        raise ValueError(
            "private Archive maintenance requires explicit v0.14, original ID and retention reason"
        )
    components = validate(profile)
    budget = bundle.Budget(profile)
    with acquire_all(components, profile_schema=retirement.SCHEMA), ExitStack() as stack:
        directory, publication, source, source_cas = _resolve(
            components, controller_id, original_command, expected_intent, budget
        )
        root = real_directory(directory / "content")
        recipient_writer = _lease(root / ".writer.lock", stack)
        journal = root / retirement.DIRECTORY
        prior = journal.exists() or journal.is_symlink()
        if prior:
            real_directory(journal)
            intent = retirement.read(journal / "intent.json")
            retirement.validate_intent(intent, publication)
            original = intent["original_owner"]
            for name in retirement.HEADERS:
                if retirement.read(root / name, 4096, canonical_required=False) not in (
                    original,
                    original | {"schema": retirement.STORAGE},
                ):
                    raise ValueError("private Archive original retirement owner changed")
            if not reclaim:
                if (
                    intent["command_id"] != command_id
                    or intent["reason"] != reason
                    or intent["principal_uid"] != os.getuid()
                    or intent["profile_digest"] != digest_profile(profile)
                ):
                    raise ValueError("original Archive retirement input changed")
                complete = (journal / "receipt.json").exists()
                if complete:
                    retirement.validate_seal(root, publication, budget)
                return {
                    "schema": "roboguide.archive-maintenance-observation/v0.1",
                    "status": "retired" if complete else "unknown",
                    "intent_digest": digest(canonical(intent)),
                    "observation_only": True,
                    "unlink_count": 0,
                }
            retirement.validate_gc_history(root, intent, budget, allow_unaccounted=True)
        else:
            if reclaim:
                raise ValueError("private Archive GC requires its original retirement tombstone")
            original = artifact_layout.validate_storage(root, retirement.SCHEMA)
            assert original is not None
        if artifact_layout.reference_authorities(
            root,
            retirement.read(
                root / "reference-authorities-v1/identity.json", 4096, canonical_required=False
            ),
        ):
            raise ValueError(
                "private Archive has known Controller consumers; complete release required"
            )
        retirement.recipient_reference(root, publication, budget)
        inputs = _current_inputs(root, publication, budget, released=prior)
        if any(not item["present"] for item in inputs) and not (journal / "receipt.json").is_file():
            raise ValueError("unsealed Archive bytes are missing before any GC authority")
        kernel = _kernel(budget)
        effect = EffectBudget(budget, kernel)
        if not prior:
            intent = {
                "schema": "roboguide.archive-recipient-retirement/v0.1",
                "command_id": command_id,
                "original_intent_digest": expected_intent,
                "publication_digest": digest(canonical(publication)),
                "source_receipt": digest(canonical(publication)),
                "source_authority": publication["intent"]["request"]["authority_id"],
                "source_owner": publication["intent"]["request"]["source_owner"],
                "target_owner": publication["target_owner"],
                "original_owner": original,
                "inputs": publication["intent"]["inputs"],
                "principal_uid": os.getuid(),
                "reason": reason,
                "profile_digest": digest_profile(profile),
                "kernel": kernel,
                "fence": _fence(source, source_cas, publication, recipient_writer),
            }
            retirement.validate_intent(intent, publication)
            effect.check()
            journal.mkdir()
            (journal / "operations").mkdir()
            _sync(root)
            publish(
                journal / "gc-commands.json",
                {"schema": "roboguide.archive-gc-command-head/v0.1", "command_ids": []},
                effect,
            )
            publish(journal / "intent.json", intent, effect)
            _seal(root, intent, effect)
            return {
                "schema": "roboguide.archive-maintenance-result/v0.1",
                "status": "retired",
                "intent_digest": digest(canonical(intent)),
                "unlink_count": 0,
            }
        # A new explicit GC intent does not renew or edit the original retirement decision.
        operations = journal / "operations"
        real_directory(operations)
        previous_commands = retirement.bounded_entries(operations, budget, 4096)
        if len(previous_commands) >= 4096:
            raise ValueError("private Archive GC original command history is full")
        operation = operations / hashlib.sha256(command_id.encode()).hexdigest()
        request = {
            "schema": "roboguide.archive-recipient-gc-intent/v0.1",
            "command_id": command_id,
            "retirement_digest": digest(canonical(intent)),
            "profile_digest": digest_profile(profile),
            "principal_uid": os.getuid(),
            "reason": reason,
            "inputs": inputs,
            "kernel": kernel,
            "fence": _fence(source, source_cas, publication, recipient_writer),
        }
        if operation.exists() or operation.is_symlink():
            retained = retirement.read(real_directory(operation) / "intent.json")
            if any(
                retained.get(key) != request[key]
                for key in (
                    "schema",
                    "command_id",
                    "retirement_digest",
                    "profile_digest",
                    "principal_uid",
                    "reason",
                )
            ):
                raise ValueError("original Archive GC command input changed")
            receipt_path = operation / "receipt.json"
            outcome = retirement.read(receipt_path) if receipt_path.exists() else None
            return {
                "schema": "roboguide.archive-maintenance-observation/v0.1",
                "status": "complete" if outcome else "unknown",
                "original_receipt": outcome,
                "observation_only": True,
                "unlink_count": 0,
            }
        effect.check()
        operation.mkdir()
        _sync(operations)
        publish(operation / "intent.json", request, effect)
        head = retirement.read(journal / "gc-commands.json")
        if (
            set(head) != {"schema", "command_ids"}
            or head["schema"] != "roboguide.archive-gc-command-head/v0.1"
            or not isinstance(head["command_ids"], list)
        ):
            raise ValueError("original Archive GC command head is unavailable")
        head["command_ids"] = sorted(
            set(head["command_ids"]) | {path.name for path in previous_commands} | {operation.name}
        )
        _publish_head(journal, head, effect)
        _seal(root, intent, effect)
        retirement.validate_seal(root, publication, budget)
        deleted = []
        for item in inputs:
            if not item["present"]:
                continue
            effect.check()
            path = _input_path(root, item)
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or item["inode"] != {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }:
                raise ValueError("private Archive GC original inode changed before unlink")
            path.unlink()
            _sync(path.parent)
            deleted.append(
                {"content_digest": item["content_digest"], "byte_size": item["byte_size"]}
            )
        result = {
            "schema": "roboguide.archive-recipient-gc-receipt/v0.1",
            "intent_digest": digest(canonical(request)),
            "deleted": deleted,
            "already_missing": [item["content_digest"] for item in inputs if not item["present"]],
        }
        publish(operation / "receipt.json", result, effect)
        return {
            "schema": "roboguide.archive-maintenance-result/v0.1",
            "status": "complete",
            "original_receipt": result,
            "unlink_count": len(deleted),
        }
