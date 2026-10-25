"""Publish and restore complete configured SQLite/content snapshots with immutable manifests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from . import (
    archive_references,
    artifact_layout,
    artifact_snapshots,
    catalog_references,
    replica_content_bundle,
    snapshot_references,
)
from .profile import (
    IDENTIFIER,
    MAX_PROFILE_BYTES,
    acquire_all,
    layout,
    real_directory,
    relative,
    validate,
    validate_memory_storage,
    validate_replica_sets,
    validate_replica_storage,
)

MAX_MANIFEST_BYTES = 64 * 1024 * 1024
RESTORE_RECEIPT_SCHEMAS = {
    f"roboguide.recovery-profile/v0.{version}": f"roboguide.restore-receipt/v0.{version}"
    for version in range(1, 29)
}
CHUNK = 1024 * 1024
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def canonical(value: object) -> bytes:
    """Encode exact deterministic evidence without changing original SQLite content."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    """Return a lowercase SHA-256 identity for a manifest or bounded evidence record."""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _private_root_staging(component: dict[str, Any], schema: str, name: str) -> bool:
    """Keep only Root8's empty private staging directory under the explicit new recovery format."""
    return (
        schema
        in {
            "roboguide.recovery-profile/v0.22",
            "roboguide.recovery-profile/v0.23",
            "roboguide.recovery-profile/v0.24",
            "roboguide.recovery-profile/v0.25",
            "roboguide.recovery-profile/v0.26",
            "roboguide.recovery-profile/v0.27",
            "roboguide.recovery-profile/v0.28",
        }
        and component["kind"] == "replica-content"
        and component["storage_schema"] == "roboguide.replica-content-layout/v0.8"
        and Path(name).parts[0] == "staging-v1"
    )


class Budget:
    """Bound original elapsed time, total stored bytes and files without retry renewal."""

    def __init__(self, profile: dict[str, Any]) -> None:
        """Freeze a fresh operation's monotonic deadline before any filesystem effect."""
        self.deadline_ns = time.monotonic_ns() + profile["budget_seconds"] * 1_000_000_000
        self.max_bytes, self.max_files = profile["max_bytes"], profile["max_files"]
        self.bytes = self.files = 0

    def check(self, byte_count: int = 0, file_count: int = 0) -> None:
        """Fail the current operation before exceeding its nonrenewable resource bounds."""
        self.bytes += byte_count
        self.files += file_count
        if (
            time.monotonic_ns() >= self.deadline_ns
            or self.bytes > self.max_bytes
            or self.files > self.max_files
        ):
            raise ValueError("original recovery operation budget exhausted")


def _regular(path: Path) -> BinaryIO:
    """Open an existing no-follow regular file beneath real directories."""
    real_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("recovery input is not a regular file")
    return os.fdopen(descriptor, "rb")


def _sync(path: Path) -> None:
    """Persist the exact real directory after file publication or atomic rename."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directories(root: Path, budget: Budget) -> list[Path]:
    """Retain actual original directories, including empty mandatory nested backend structures."""
    result: list[Path] = []
    pending = [real_directory(root)]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                budget.check()
                if entry.is_symlink():
                    raise ValueError("original recovery directory cannot contain symlinks")
                if entry.is_dir(follow_symlinks=False):
                    if len(result) >= budget.max_files * 4 + 8192:
                        raise ValueError("original recovery directories exceed bounded evidence")
                    path = real_directory(Path(entry.path))
                    result.append(path)
                    pending.append(path)
    return result


def _validate_directories(root: Path, manifest: dict[str, Any], budget: Budget) -> None:
    """Check original directory coverage without repairing the source bundle."""
    directories = manifest.get("directories")
    if (
        not isinstance(directories, list)
        or len(directories) > budget.max_files * 4 + 8192
        or any(not isinstance(value, str) for value in directories)
        or directories != sorted(set(directories))
    ):
        raise ValueError("original recovery directory coverage is not closed or bounded")
    by_id = {item["component_id"]: item for item in manifest["profile"]["components"]}
    for value in directories:
        budget.check()
        path = relative(value)
        parts = Path(path).parts
        if parts[0] != "stores" or (len(parts) > 1 and parts[1] not in by_id):
            raise ValueError("original recovery directory is outside declared storage")
        if len(parts) > 2:
            if len(parts) > 3 and _private_root_staging(
                by_id[parts[1]], manifest["profile"]["schema"], "/".join(parts[2:])
            ):
                raise ValueError("private Root8 staging directories are outside recovery content")
            databases, trees, _ = layout(
                by_id[parts[1]], profile_schema=manifest["profile"]["schema"]
            )
            selected = Path(*parts[2:])
            if not any(
                selected.is_relative_to(Path(tree)) or Path(tree).is_relative_to(selected)
                for tree in trees
            ) and not any(selected in Path(database).parents for database in databases):
                raise ValueError("original recovery directory is outside selected content trees")
        real_directory(root / path)
    actual = sorted(path.relative_to(root).as_posix() for path in _directories(root, budget))
    if directories != actual:
        raise ValueError("original recovery directory coverage is missing or changed")


def _sync_tree(root: Path, budget: Budget) -> None:
    """Persist every created directory entry, including empty managed content roots."""
    directories, pending = [], [root]
    while pending:
        directory = pending.pop()
        real_directory(directory)
        directories.append(directory)
        if len(directories) > budget.max_files * 4 + 8192:
            raise ValueError("recovery directory count exceeds its bound")
        with os.scandir(directory) as entries:
            for entry in entries:
                budget.check()
                if entry.is_symlink():
                    raise ValueError("recovery output contains a symlink")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    for directory in reversed(directories):
        budget.check()
        _sync(directory)


def _check_cas_path(
    component: dict[str, Any],
    name: str,
    checksum: str,
    profile_schema: str = "roboguide.recovery-profile/v0.1",
) -> None:
    """Reject finalized CAS bytes whose actual hash differs from their storage identity."""
    parts = Path(name).parts
    if name in artifact_layout.metadata_files(component, profile_schema):
        return
    if component["kind"] == "artifact" and parts[0] in {"blobs", "objects-v1"}:
        expected = "objects-v1" if artifact_layout.supports(profile_schema) else "blobs"
        if (
            len(parts) != 4
            or parts[0] != expected
            or parts[1] != "sha256"
            or parts[2] != checksum[7:9]
            or parts[3] != checksum[7:]
        ):
            raise ValueError("Artifact CAS path differs from actual content digest")
    if component["kind"] == "memory" and len(parts) == 2 and parts[0] == "content":
        if re.fullmatch(r"[0-9a-f]{64}", parts[1]) is not None and checksum != "sha256:" + parts[1]:
            raise ValueError("Memory CAS path differs from actual content digest")
    if component["kind"] == "replica-content" and parts[0] == "content-v1":
        if (
            len(parts) != 3
            or re.fullmatch(r"[0-9a-f]{64}", parts[1]) is None
            or parts[2] != checksum[7:]
        ):
            raise ValueError("Replica qualified CAS path differs from actual content digest")


def _identity(path: Path, budget: Budget) -> dict[str, Any]:
    """Read all SQLite schema/rows independently of any checkpoint or event retention policy."""
    real_directory(path.parent)
    with _regular(path):
        pass
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_MANIFEST_BYTES)
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("recovery SQLite integrity check failed")
        tables = []
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        for kind, name, _, _ in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            order = ",".join(str(index + 1) for index in range(len(columns)))
            checksum, count = hashlib.sha256(), 0
            for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
                budget.check()
                encoded = canonical(
                    [
                        {"bytes": base64.b64encode(value).decode()}
                        if isinstance(value, bytes)
                        else {"type": type(value).__name__, "value": value}
                        for value in row
                    ]
                )
                checksum.update(len(encoded).to_bytes(8, "big"))
                checksum.update(encoded)
                count += 1
            tables.append(
                {"table": name, "rows": count, "digest": "sha256:" + checksum.hexdigest()}
            )
        return {
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "schema_digest": digest(canonical(schema)),
            "tables": tables,
        }


def _validate_memory_content_references(
    root: Path, component_id: str, records: dict[str, dict[str, Any]], budget: Budget
) -> None:
    """Require every active local Memory reference and map head to a bundled CAS object."""
    database_path = f"stores/{component_id}/memory.sqlite3"
    if database_path not in records or records[database_path]["kind"] != "sqlite":
        raise ValueError("Memory content reference authority is absent from its bundle")
    database = root / database_path

    def progress() -> int:
        """Bound SQLite scans and sorting by the original recovery operation deadline."""
        budget.check()
        return 0

    def require_content(value: object, source: str) -> None:
        """Resolve one exact digest to the immutable provider content in this bundle."""
        budget.check()
        if not isinstance(value, str) or SHA256.fullmatch(value) is None:
            raise ValueError(f"{source} has an invalid content digest")
        path = f"stores/{component_id}/content/{value[7:]}"
        row = records.get(path)
        if row is None or row["kind"] != "content" or row.get("content_digest") != value:
            raise ValueError(
                f"active Memory reference {source} lacks its bundled CAS content: {value}"
            )
        if (
            "content_tombstones" in tables
            and connection.execute(
                "SELECT 1 FROM content_tombstones WHERE content_digest=?", (value,)
            ).fetchone()
        ):
            raise ValueError("active Memory content reference points at a durable tombstone")

    try:
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_MANIFEST_BYTES)
            connection.set_progress_handler(progress, 1000)
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                connection.execute("PRAGMA user_version").fetchone()[0] in (3, 4, 5)
                and not {"content_references", "content_tombstones"} <= tables
            ):
                raise ValueError("Memory v3 content reference authority table is missing")
            if "content_references" in tables:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(content_references)")
                }
                if not {"reference_id", "content_digest", "active"} <= columns:
                    raise ValueError(
                        "Memory content reference table is missing its authority columns"
                    )
                for index, (value,) in enumerate(
                    connection.execute(
                        "SELECT content_digest FROM content_references "
                        "WHERE active=1 ORDER BY reference_id"
                    )
                ):
                    require_content(value, f"content_references[{index}]")
            map_path = f"stores/{component_id}/active-map/active-map.sqlite3"
            if map_path in records:
                map_database = root / map_path
                with closing(
                    sqlite3.connect(map_database.as_uri() + "?mode=ro&immutable=1", uri=True)
                ) as map_connection:
                    map_connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_MANIFEST_BYTES)
                    map_connection.set_progress_handler(progress, 1000)
                    map_tables = {
                        row[0]
                        for row in map_connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if (
                        map_connection.execute("PRAGMA user_version").fetchone()[0] in (1, 2)
                        and not {"refs", "heads", "handoffs"} <= map_tables
                    ):
                        raise ValueError("Active Map content reference authority table is missing")
                    if "refs" in map_tables:
                        for index, (value,) in enumerate(
                            map_connection.execute(
                                "SELECT digest FROM refs WHERE active=1 ORDER BY ref_id"
                            )
                        ):
                            require_content(value, f"active-map.refs[{index}]")
                    if "heads" in map_tables:
                        for index, (context_json,) in enumerate(
                            map_connection.execute(
                                "SELECT context_json FROM heads WHERE state='active' "
                                "ORDER BY selector"
                            )
                        ):
                            try:
                                context = json.loads(context_json)
                            except (TypeError, json.JSONDecodeError) as error:
                                raise ValueError(
                                    f"active-map head {index} has invalid context JSON"
                                ) from error
                            if not isinstance(context, dict):
                                raise ValueError(
                                    f"active-map head {index} context is not an object"
                                )
                            require_content(
                                context.get("content_digest"), f"active-map.heads[{index}]"
                            )
                    if "handoffs" in map_tables:
                        for index, (encoded,) in enumerate(
                            map_connection.execute(
                                "SELECT request_json FROM handoffs WHERE state='prepared' "
                                "ORDER BY barrier_id"
                            )
                        ):
                            budget.check()
                            try:
                                handoff = json.loads(encoded)
                            except (TypeError, json.JSONDecodeError) as error:
                                raise ValueError("pending map handoff has invalid JSON") from error
                            for side in ("source", "target"):
                                context = handoff.get(side) if isinstance(handoff, dict) else None
                                if not isinstance(context, dict):
                                    raise ValueError("pending map handoff content is unavailable")
                                require_content(
                                    context.get("content_digest"),
                                    f"active-map.handoffs[{index}].{side}",
                                )
    except sqlite3.DatabaseError as error:
        raise ValueError("Memory content reference authority cannot be read") from error


def _copy(source: Path, target: Path, budget: Budget) -> tuple[str, int]:
    """Stream exact stable bytes to a new private file and fsync before acknowledging them."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    real_directory(target.parent)
    checksum, size = hashlib.sha256(), 0
    with _regular(source) as incoming, target.open("xb") as output:
        before = os.fstat(incoming.fileno())
        budget.check(file_count=1)
        while chunk := incoming.read(CHUNK):
            budget.check(byte_count=len(chunk))
            checksum.update(chunk)
            output.write(chunk)
            size += len(chunk)
        after = os.fstat(incoming.fileno())
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or size != before.st_size:
            raise ValueError("recovery source changed while being copied")
        output.flush()
        os.fsync(output.fileno())
    target.chmod(0o600)
    _sync(target.parent)
    return "sha256:" + checksum.hexdigest(), size


def _hash(path: Path, budget: Budget) -> tuple[str, int]:
    """Verify a bounded actual file without loading content into a manifest or checkpoint."""
    checksum, size = hashlib.sha256(), 0
    with _regular(path) as incoming:
        budget.check(file_count=1)
        while chunk := incoming.read(CHUNK):
            budget.check(byte_count=len(chunk))
            checksum.update(chunk)
            size += len(chunk)
    return "sha256:" + checksum.hexdigest(), size


def _walk(root: Path, budget: Budget) -> list[Path]:
    """Enumerate an actual managed tree, rejecting symlinks, devices and unbounded empty trees."""
    real_directory(root)
    result, pending, count = [], [root], 0
    while pending:
        directory = pending.pop()
        real_directory(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                budget.check()
                count += 1
                if count > budget.max_files * 4 + 8192:
                    raise ValueError("recovery tree entry bound exhausted")
                if entry.is_symlink():
                    raise ValueError("recovery content tree contains a symlink")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    result.append(Path(entry.path))
                else:
                    raise ValueError("recovery content tree contains a non-file entry")
    return sorted(result)


def _target(path: Path, command_id: str) -> Path:
    """Create a new private partial directory without overwriting any prior recovery result."""
    if IDENTIFIER.fullmatch(command_id) is None:
        raise ValueError("recovery command identity is invalid")
    real_directory(path.parent)
    if path.exists() or path.is_symlink():
        raise ValueError("recovery destination already exists")
    partial = path.with_name(path.name + ".partial-" + command_id)
    partial.mkdir(mode=0o700)
    _sync(partial.parent)
    return partial


def backup(profile: dict[str, Any], destination: Path, command_id: str) -> dict[str, Any]:
    """Freeze actual configured stores under all original writer leases, then publish one bundle."""
    started_ns = time.monotonic_ns()
    components = validate(profile)
    if len(canonical(profile)) > MAX_PROFILE_BYTES:
        raise ValueError("recovery profile exceeds its byte bound")
    destination = destination.absolute()
    if any(destination.is_relative_to(Path(value["root"])) for value in components):
        raise ValueError("recovery bundle cannot be written inside its own source component")
    budget = Budget(profile)
    budget.deadline_ns = started_ns + profile["budget_seconds"] * 1_000_000_000
    budget.check()
    if destination.is_dir() and not destination.is_symlink():
        with _regular(destination / "manifest.json") as handle:
            previous = handle.read(MAX_MANIFEST_BYTES + 1)
        saved = verify(destination, digest(previous), deadline_ns=budget.deadline_ns)
        budget.check()
        if saved["command_id"] != command_id or saved["profile"] != profile:
            raise ValueError("original recovery command/source identity conflicts")
        if snapshot_references.supports(profile["schema"]):
            with acquire_all(components, profile_schema=profile["schema"]):
                snapshot_references.complete(components, saved, digest(previous), budget)
                if profile["schema"] in artifact_snapshots.SUPPORTED:
                    artifact_snapshots.complete(components, saved, digest(previous), budget)
        return {
            "manifest_digest": digest(previous),
            "files": len(saved["files"]),
            "bytes": sum(row["byte_size"] for row in saved["files"]),
        }
    with acquire_all(components, profile_schema=profile["schema"]):
        budget.check()
        validate_replica_sets(profile, components)
        replica_sources = (
            replica_content_bundle.capture_sources(
                components,
                budget,
                allow_standalone=profile["schema"]
                in {
                    "roboguide.recovery-profile/v0.24",
                    "roboguide.recovery-profile/v0.25",
                    "roboguide.recovery-profile/v0.26",
                    "roboguide.recovery-profile/v0.27",
                    "roboguide.recovery-profile/v0.28",
                },
            )
            if profile["schema"]
            in {
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
            }
            else None
        )
        snapshot_operations = (
            snapshot_references.prepare(profile, components, destination, command_id, budget)
            if snapshot_references.supports(profile["schema"])
            else []
        )
        artifact_operations = (
            artifact_snapshots.prepare(profile, components, destination, command_id, budget)
            if profile["schema"] in artifact_snapshots.SUPPORTED
            else []
        )
        partial = _target(destination, command_id)
        records = []
        for component in components:
            root, identifier = Path(component["root"]), component["component_id"]
            databases, trees, _ = layout(component, profile_schema=profile["schema"])
            for name in databases:
                path = root / name
                if component["kind"] == "memory" and name != "memory.sqlite3" and not path.exists():
                    continue
                with _regular(path):
                    pass
                target = partial / "stores" / identifier / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                budget.check()
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                    page_size = source.execute("PRAGMA page_size").fetchone()[0]
                    with closing(sqlite3.connect(target)) as output:

                        def progress(
                            _status: int,
                            _remaining: int,
                            total: int,
                            original_page_size: int = page_size,
                        ) -> None:
                            """Bound each actual SQLite backup step before the next page batch."""
                            budget.check()
                            if total * original_page_size + budget.bytes > budget.max_bytes:
                                raise ValueError("SQLite backup exceeds original byte budget")

                        source.backup(output, pages=128, progress=progress, sleep=0.0)
                        if output.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                            raise ValueError(
                                "recovery SQLite snapshot still depends on a WAL sidecar"
                            )
                with target.open("rb") as handle:
                    os.fsync(handle.fileno())
                _sync(target.parent)
                checksum, size = _hash(target, budget)
                records.append(
                    {
                        "path": target.relative_to(partial).as_posix(),
                        "kind": "sqlite",
                        "content_digest": checksum,
                        "byte_size": size,
                        "sqlite": _identity(target, budget),
                    }
                )
            for name in artifact_layout.metadata_files(component, profile["schema"]):
                target = partial / "stores" / identifier / name
                checksum, size = _copy(root / name, target, budget)
                records.append(
                    {
                        "path": target.relative_to(partial).as_posix(),
                        "kind": "content",
                        "content_digest": checksum,
                        "byte_size": size,
                    }
                )
            for name in trees:
                if artifact_layout.supports(profile["schema"]):
                    (partial / "stores" / identifier / name).mkdir(
                        mode=0o700, parents=True, exist_ok=True
                    )
                if _private_root_staging(component, profile["schema"], name):
                    continue
                if profile["schema"] in archive_references.SUPPORTED:
                    for source_directory in _directories(root / name, budget):
                        (
                            partial / "stores" / identifier / source_directory.relative_to(root)
                        ).mkdir(mode=0o700, parents=True, exist_ok=True)
                for source_path in _walk(root / name, budget):
                    selected = source_path.relative_to(root)
                    target = partial / "stores" / identifier / selected
                    checksum, size = _copy(source_path, target, budget)
                    _check_cas_path(component, selected.as_posix(), checksum, profile["schema"])
                    records.append(
                        {
                            "path": target.relative_to(partial).as_posix(),
                            "kind": "content",
                            "content_digest": checksum,
                            "byte_size": size,
                        }
                    )
        record_map = {str(row["path"]): row for row in records}
        replica_content_bundle.validate_bundle(
            partial, components, record_map, budget, profile["schema"], replica_sources
        )
        for component in components:
            if component["kind"] == "artifact":
                artifact_layout.validate_bundle(partial, component, record_map, profile["schema"])
            if component["kind"] == "memory":
                _validate_memory_content_references(
                    partial, component["component_id"], record_map, budget
                )
        catalog_references.validate(partial, components, record_map, budget, profile["schema"])
        from .artifact_source_references import validate_bundle as validate_source_content

        validate_source_content(partial, components, record_map, budget, profile["schema"])
        archive_references.validate(partial, components, record_map, budget, profile["schema"])
        manifest: dict[str, Any] = {
            "schema": (
                "roboguide.recovery-bundle/v0.4"
                if profile["schema"] in archive_references.SUPPORTED
                else "roboguide.recovery-bundle/v0.3"
                if profile["schema"] in artifact_snapshots.SUPPORTED
                else "roboguide.recovery-bundle/v0.2"
                if snapshot_references.supports(profile["schema"])
                else "roboguide.recovery-bundle/v0.1"
            ),
            "command_id": command_id,
            "profile": profile,
            "profile_digest": digest(canonical(profile)),
            "files": sorted(records, key=lambda row: str(row["path"])),
            "scope": "declared-stores-only",
            "physical_continuity": "not-established",
        }
        if profile["schema"] in archive_references.SUPPORTED:
            manifest["directories"] = sorted(
                path.relative_to(partial).as_posix() for path in _directories(partial, budget)
            )
        if replica_sources is not None:
            manifest["replica_content_sources"] = replica_sources
        if snapshot_references.supports(profile["schema"]):
            manifest["snapshot_operations"] = snapshot_operations
            snapshot_references.validate_bundle(partial, manifest, budget)
        if profile["schema"] in artifact_snapshots.SUPPORTED:
            manifest["artifact_snapshot_operations"] = artifact_operations
            artifact_snapshots.validate_bundle(partial, manifest, budget)
        raw = canonical(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("complete recovery manifest exceeds its bound")
        with (partial / "manifest.json").open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        actual = {path.relative_to(partial).as_posix() for path in _walk(partial, budget)}
        if actual != {str(row["path"]) for row in records} | {"manifest.json"}:
            raise ValueError("recovery staging contains undeclared files before publication")
        _sync_tree(partial, budget)
        budget.check()
        if destination.exists() or destination.is_symlink():
            raise ValueError("recovery destination changed before publication")
        partial.rename(destination)
        _sync(destination.parent)
        if snapshot_references.supports(profile["schema"]):
            verified = verify(destination, digest(raw), deadline_ns=budget.deadline_ns)
            budget.check()
            snapshot_references.complete(components, verified, digest(raw), budget)
            if profile["schema"] in artifact_snapshots.SUPPORTED:
                artifact_snapshots.complete(components, verified, digest(raw), budget)
    return {"manifest_digest": digest(raw), "files": len(records), "bytes": budget.bytes}


def verify(bundle: Path, expected_digest: str, *, deadline_ns: int | None = None) -> dict[str, Any]:
    """Verify every declared byte and full SQLite identity against an externally pinned digest."""
    started_ns = time.monotonic_ns()
    bundle = real_directory(bundle)
    with _regular(bundle / "manifest.json") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES or digest(raw) != expected_digest:
        raise ValueError("recovery manifest differs from its original expected digest")
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != (
            {
                "schema",
                "command_id",
                "profile",
                "profile_digest",
                "files",
                "scope",
                "physical_continuity",
            }
            | (
                {"replica_content_sources"}
                if manifest.get("profile", {}).get("schema")
                in {
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
                }
                else set()
            )
            | (
                {"snapshot_operations"}
                if manifest.get("schema")
                in {
                    "roboguide.recovery-bundle/v0.2",
                    "roboguide.recovery-bundle/v0.3",
                    "roboguide.recovery-bundle/v0.4",
                }
                else set()
            )
            | (
                {"directories"}
                if manifest.get("schema") == "roboguide.recovery-bundle/v0.4"
                else set()
            )
            | (
                {"artifact_snapshot_operations"}
                if manifest.get("schema")
                in {"roboguide.recovery-bundle/v0.3", "roboguide.recovery-bundle/v0.4"}
                else set()
            )
        )
        or manifest["schema"]
        not in {
            "roboguide.recovery-bundle/v0.1",
            "roboguide.recovery-bundle/v0.2",
            "roboguide.recovery-bundle/v0.3",
            "roboguide.recovery-bundle/v0.4",
        }
    ):
        raise ValueError("recovery bundle manifest is not closed or versioned")
    components = validate(manifest["profile"])
    if (
        not isinstance(manifest["command_id"], str)
        or IDENTIFIER.fullmatch(manifest["command_id"]) is None
        or manifest["scope"] != "declared-stores-only"
        or manifest["physical_continuity"] != "not-established"
    ):
        raise ValueError("recovery bundle authority/scope identity is invalid")
    if digest(canonical(manifest["profile"])) != manifest["profile_digest"]:
        raise ValueError("recovery bundle profile identity conflicts")
    budget = Budget(manifest["profile"])
    budget.deadline_ns = started_ns + manifest["profile"]["budget_seconds"] * 1_000_000_000
    if deadline_ns is not None:
        budget.deadline_ns = min(budget.deadline_ns, deadline_ns)
    budget.check()
    files = manifest["files"]
    if not isinstance(files, list) or len(files) > budget.max_files:
        raise ValueError("recovery manifest file coverage exceeds its bound")
    identities = {value["component_id"]: value for value in components}
    paths: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or row.get("kind") not in {"sqlite", "content"}:
            raise ValueError("recovery manifest file is invalid")
        keys = {"path", "kind", "content_digest", "byte_size"}
        if set(row) != (keys | {"sqlite"} if row["kind"] == "sqlite" else keys):
            raise ValueError("recovery manifest file is not closed")
        name = relative(row["path"])
        parts = Path(name).parts
        if len(parts) < 3 or parts[0] != "stores" or parts[1] not in identities or name in paths:
            raise ValueError("recovery manifest contains duplicate or unrelated storage paths")
        paths.add(name)
        if (
            type(row["byte_size"]) is not int
            or row["byte_size"] < 0
            or not isinstance(row["content_digest"], str)
            or SHA256.fullmatch(row["content_digest"]) is None
        ):
            raise ValueError("recovery content size or digest is invalid")
        component = identities[parts[1]]
        databases, trees, _ = layout(component, profile_schema=manifest["profile"]["schema"])
        selected = "/".join(parts[2:])
        if _private_root_staging(component, manifest["profile"]["schema"], selected):
            raise ValueError("private Root8 staging payload is outside recovery content")
        if row["kind"] == "sqlite" and selected not in databases:
            raise ValueError("recovery SQLite path is not part of its original component")
        if (
            row["kind"] == "content"
            and not any(tree == "." or Path(selected).is_relative_to(tree) for tree in trees)
            and selected
            not in artifact_layout.metadata_files(component, manifest["profile"]["schema"])
        ):
            raise ValueError("recovery content path is outside its original managed trees")
        if _hash(bundle / name, budget) != (row["content_digest"], row["byte_size"]):
            raise ValueError("recovery content digest or size differs: " + name)
        if row["kind"] == "content":
            _check_cas_path(
                component, selected, row["content_digest"], manifest["profile"]["schema"]
            )
        if row["kind"] == "sqlite":
            if _identity(bundle / name, budget) != row["sqlite"]:
                raise ValueError("complete recovery SQLite identity differs: " + name)
            if component["kind"] == "controller":
                from .controller_storage import validate as validate_controller

                with closing(
                    sqlite3.connect((bundle / name).as_uri() + "?mode=ro&immutable=1", uri=True)
                ) as connection:
                    validate_controller(
                        connection, manifest["profile"]["schema"], component.get("storage_schema")
                    )
            if component["kind"] == "memory" and selected == "memory.sqlite3":
                with closing(
                    sqlite3.connect((bundle / name).as_uri() + "?mode=ro&immutable=1", uri=True)
                ) as connection:
                    validate_memory_storage(connection, manifest["profile"]["schema"])
            if component["kind"] == "replica":
                with closing(
                    sqlite3.connect((bundle / name).as_uri() + "?mode=ro&immutable=1", uri=True)
                ) as connection:
                    validate_replica_storage(connection, manifest["profile"]["schema"])
    if (manifest["profile"]["schema"] in archive_references.SUPPORTED) != (
        manifest["schema"] == "roboguide.recovery-bundle/v0.4"
    ):
        raise ValueError("Archive profile and original directory-aware bundle schema differ")
    if manifest["schema"] == "roboguide.recovery-bundle/v0.4":
        _validate_directories(bundle, manifest, budget)
    record_map = {str(row["path"]): row for row in files}
    replica_content_bundle.validate_bundle(
        bundle,
        components,
        record_map,
        budget,
        manifest["profile"]["schema"],
        manifest.get("replica_content_sources"),
    )
    if (snapshot_references.supports(manifest["profile"]["schema"])) != (
        manifest["schema"]
        in {
            "roboguide.recovery-bundle/v0.2",
            "roboguide.recovery-bundle/v0.3",
            "roboguide.recovery-bundle/v0.4",
        }
    ):
        raise ValueError("snapshot profile and bundle schema differ")
    if manifest["schema"] in {
        "roboguide.recovery-bundle/v0.2",
        "roboguide.recovery-bundle/v0.3",
        "roboguide.recovery-bundle/v0.4",
    }:
        snapshot_references.validate_bundle(bundle, manifest, budget)
    if (manifest["profile"]["schema"] in artifact_snapshots.SUPPORTED) != (
        manifest["schema"] in {"roboguide.recovery-bundle/v0.3", "roboguide.recovery-bundle/v0.4"}
    ):
        raise ValueError("Artifact Snapshot profile and bundle schema differ")
    if manifest["schema"] in {"roboguide.recovery-bundle/v0.3", "roboguide.recovery-bundle/v0.4"}:
        artifact_snapshots.validate_bundle(bundle, manifest, budget)
    for component in components:
        if component["kind"] == "artifact":
            artifact_layout.validate_bundle(
                bundle, component, record_map, manifest["profile"]["schema"]
            )
        if component["kind"] == "memory":
            _validate_memory_content_references(
                bundle, component["component_id"], record_map, budget
            )
    catalog_references.validate(
        bundle, components, record_map, budget, manifest["profile"]["schema"]
    )
    archive_references.validate(
        bundle, components, record_map, budget, manifest["profile"]["schema"]
    )
    from .artifact_source_references import validate_bundle as validate_source_content

    validate_source_content(bundle, components, record_map, budget, manifest["profile"]["schema"])
    for component in components:
        databases, _, _ = layout(component, profile_schema=manifest["profile"]["schema"])
        required = (
            databases[:1]
            if component["kind"] == "memory"
            and manifest["profile"]["schema"]
            not in {
                "roboguide.recovery-profile/v0.4",
                "roboguide.recovery-profile/v0.5",
                "roboguide.recovery-profile/v0.6",
                "roboguide.recovery-profile/v0.7",
                "roboguide.recovery-profile/v0.8",
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
            }
            else databases
        )
        if any(f"stores/{component['component_id']}/{name}" not in paths for name in required):
            raise ValueError("recovery manifest lost a required original authority database")
    actual = {
        path.relative_to(bundle).as_posix() for path in _walk(bundle, Budget(manifest["profile"]))
    }
    if actual != paths | {"manifest.json"}:
        raise ValueError("recovery bundle has missing or undeclared files")
    budget.check()
    return dict(manifest)


def reconcile_snapshots(
    profile: dict[str, Any], bundle: Path, expected_digest: str
) -> dict[str, Any]:
    """Retire original snapshot pins after verifying the complete original copy."""
    components = validate(profile)
    if not snapshot_references.supports(profile["schema"]):
        raise ValueError("snapshot reconciliation requires explicit recovery profile v0.8")
    budget = Budget(profile)
    manifest = verify(bundle, expected_digest, deadline_ns=budget.deadline_ns)
    if not snapshot_references.supports(manifest["profile"]["schema"]):
        raise ValueError("snapshot proof lacks its original explicit reference protocol")
    with acquire_all(components, profile_schema=profile["schema"]):
        receipts = snapshot_references.complete(components, manifest, expected_digest, budget)
        if manifest["profile"]["schema"] in artifact_snapshots.SUPPORTED:
            receipts += artifact_snapshots.complete(components, manifest, expected_digest, budget)
    return {
        "schema": "roboguide.snapshot-reconciliation/v0.1",
        "manifest_digest": expected_digest,
        "receipts": receipts,
        "copied_files": 0,
    }


def abort_snapshots(profile: dict[str, Any], destination: Path, command_id: str) -> dict[str, Any]:
    """Irreversibly fence an unpublished synchronous copy; retain all original partial evidence."""
    components = validate(profile)
    if (
        not snapshot_references.supports(profile["schema"])
        or IDENTIFIER.fullmatch(command_id) is None
    ):
        raise ValueError("snapshot abort requires explicit v0.8 and its original command")
    budget = Budget(profile)
    with acquire_all(components, profile_schema=profile["schema"]):
        receipts = snapshot_references.abort(
            profile, components, destination.absolute(), command_id, budget
        )
        if profile["schema"] in artifact_snapshots.SUPPORTED:
            receipts += artifact_snapshots.abort(
                profile, components, destination.absolute(), command_id, budget
            )
    return {
        "schema": "roboguide.snapshot-abort/v0.1",
        "receipts": receipts,
        "partial_evidence_preserved": True,
        "copied_files": 0,
    }


def _replica_placements(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive only explicit same-cluster finalized CAS copies from immutable original bytes."""
    placements = []
    declared = {row["path"]: row for row in manifest["files"]}
    for group in manifest["profile"].get("artifact_replica_sets", []):
        originals: dict[str, dict[str, Any]] = {}
        for row in sorted(manifest["files"], key=lambda value: str(value["path"])):
            parts = Path(row["path"]).parts
            if (
                len(parts) == 6
                and parts[1] in group
                and parts[2] in {"blobs", "objects-v1"}
                and row["kind"] == "content"
            ):
                selected = Path(*parts[2:]).as_posix()
                originals.setdefault(selected, row)
        for component in sorted(group):
            for selected, row in sorted(originals.items()):
                target = "stores/" + component + "/" + selected
                if target in declared:
                    if (
                        declared[target]["content_digest"] != row["content_digest"]
                        or declared[target]["byte_size"] != row["byte_size"]
                    ):
                        raise ValueError("same-cluster original CAS identity conflicts")
                    continue
                placements.append(
                    {
                        "source": row["path"],
                        "target": target,
                        "content_digest": row["content_digest"],
                        "byte_size": row["byte_size"],
                    }
                )
    return placements


def restore(
    bundle: Path, expected_digest: str, destination: Path, command_id: str
) -> dict[str, Any]:
    """Restore all verified stores into a new private tree; original authority fields stay exact."""
    started_ns = time.monotonic_ns()
    manifest = verify(bundle, expected_digest)
    destination = destination.absolute()
    if destination.is_relative_to(bundle.absolute()) or bundle.absolute().is_relative_to(
        destination
    ):
        raise ValueError("recovery source and destination overlap")
    budget = Budget(manifest["profile"])
    budget.deadline_ns = started_ns + manifest["profile"]["budget_seconds"] * 1_000_000_000
    budget.check()
    replica_placements = _replica_placements(manifest)
    receipt_schema = RESTORE_RECEIPT_SCHEMAS[manifest["profile"]["schema"]]
    if destination.is_dir() and not destination.is_symlink():
        receipt_path = destination / "restore-receipt.json"
        if not receipt_path.is_file() or receipt_path.is_symlink():
            raise ValueError("recovery destination already exists without its original receipt")
        with _regular(receipt_path) as handle:
            raw_receipt = handle.read(MAX_PROFILE_BYTES + 1)
        if len(raw_receipt) > MAX_PROFILE_BYTES:
            raise ValueError("original restore receipt exceeds its bound")
        previous = json.loads(raw_receipt)
        if (
            not isinstance(previous, dict)
            or previous.get("schema") != receipt_schema
            or previous.get("command_id") != command_id
            or previous.get("manifest_digest") != expected_digest
            or previous.get("profile_digest") != manifest["profile_digest"]
            or previous.get("artifact_replica_placements", []) != replica_placements
        ):
            raise ValueError("original restore command/source identity conflicts")
        adjusted = replica_content_bundle.validate_restored(
            bundle,
            destination,
            manifest,
            expected_digest,
            command_id,
            previous.get("replica_content_bindings", []),
            budget,
        )
        for row in manifest["files"]:
            if row["path"] in adjusted:
                continue
            if _hash(destination / row["path"], budget) != (
                row["content_digest"],
                row["byte_size"],
            ):
                raise ValueError(
                    "restored original bytes changed after receipt; retry is read-only"
                )
        for row in replica_placements:
            if _hash(destination / row["target"], budget) != (
                row["content_digest"],
                row["byte_size"],
            ):
                raise ValueError(
                    "original restored CAS placement is missing or changed; retry never recopies"
                )
        return dict(previous)
    partial = _target(destination, command_id)
    for component in validate(manifest["profile"]):
        root = partial / "stores" / component["component_id"]
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _, trees, _ = layout(component, profile_schema=manifest["profile"]["schema"])
        for name in trees:
            (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in manifest.get("directories", []):
        (partial / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    for row in manifest["files"]:
        target = partial / row["path"]
        if _copy(bundle / row["path"], target, budget) != (row["content_digest"], row["byte_size"]):
            raise ValueError("recovery source changed after original verification")
        if row["kind"] == "sqlite" and _identity(target, budget) != row["sqlite"]:
            raise ValueError("restored SQLite differs from its original full authority")
    for component in validate(manifest["profile"]):
        root = partial / "stores" / component["component_id"]
        _, _, locks = layout(component, profile_schema=manifest["profile"]["schema"])
        if component["kind"] == "memory" and manifest["profile"]["schema"] in {
            "roboguide.recovery-profile/v0.4",
            "roboguide.recovery-profile/v0.5",
            "roboguide.recovery-profile/v0.6",
            "roboguide.recovery-profile/v0.7",
            "roboguide.recovery-profile/v0.8",
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
        }:
            locks = [*locks, ".memory-use.lock"]
        for name in locks:
            lock = root / name
            lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not lock.exists():
                with lock.open("xb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
    for row in replica_placements:
        if _copy(bundle / row["source"], partial / row["target"], budget) != (
            row["content_digest"],
            row["byte_size"],
        ):
            raise ValueError("original CAS content changed before declared replica restoration")
    replica_bindings = replica_content_bundle.bind_restored(
        partial, manifest, expected_digest, command_id, budget
    )
    receipt = {
        "schema": receipt_schema,
        "command_id": command_id,
        "manifest_digest": expected_digest,
        "profile_digest": manifest["profile_digest"],
        "original_authority_bytes_preserved": True,
        "physical_continuity": "not-established",
        "components": [
            {
                "component_id": value["component_id"],
                "root": str(destination / "stores" / value["component_id"]),
            }
            for value in validate(manifest["profile"])
        ],
    }
    if "artifact_replica_sets" in manifest["profile"]:
        receipt["artifact_replica_placements"] = replica_placements
    if manifest["profile"]["schema"] in {
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
        receipt["original_authority_bytes_preserved"] = False
        receipt["original_operational_rows_preserved"] = True
        receipt["replica_content_bindings"] = replica_bindings
        replica_content_bundle.validate_restored(
            bundle, partial, manifest, expected_digest, command_id, replica_bindings, budget
        )
    with (partial / "restore-receipt.json").open("xb") as handle:
        handle.write(canonical(receipt))
        handle.flush()
        os.fsync(handle.fileno())
    _sync_tree(partial, budget)
    budget.check()
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore destination changed before publication")
    partial.rename(destination)
    _sync(destination.parent)
    return receipt
