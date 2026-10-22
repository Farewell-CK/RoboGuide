"""Copy real immutable files and answer readonly queries from original durable evidence."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from .storage import DATABASES, SNAPSHOT_WRITER_SCHEMA, WRITER_SCHEMA, ProviderStorage

MAX_METADATA_BYTES = 600 * 1024
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def canonical(value: object) -> bytes:
    """Encode deterministic metadata bytes without including credentials or payload content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def content_digest(value: object) -> str:
    """Hash canonical immutable metadata with an explicit SHA-256 algorithm identity."""
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def original_content_digest(invocation: dict[str, Any]) -> str:
    """Reproduce the original formal content digest without a replaceable leader epoch."""
    keys = (
        "import_id",
        "command_id",
        "consumer_provider_id",
        "manifest_json",
        "mission_id",
        "group_id",
        "task_id",
        "role_id",
        "execution_id",
    )
    value = {key: invocation[key] for key in keys}
    if any(not isinstance(item, str) or not item for item in value.values()):
        raise ValueError("original import tuple is incomplete")
    value.update(schema="roboguide.memory-import/v0.1", controller_epoch="")
    legacy = content_digest(value)
    preparation = invocation.get("memory_preparation_id", "")
    typed_map = invocation.get("typed_map_manifest_json", "")
    if not isinstance(preparation, str) or not isinstance(typed_map, str):
        raise ValueError("prepared import provenance must retain exact encoded strings")
    if not preparation and not typed_map:
        return legacy
    if not preparation:
        raise ValueError("typed-map import requires its original prepared context")
    return content_digest(
        {
            "schema": "roboguide.prepared-memory-import/v0.1",
            "original_digest": legacy,
            "preparation_id": preparation,
            "typed_map_manifest_json": typed_map,
        }
    )


def sync_directory(path: Path) -> None:
    """Persist directory entries before a durable receipt can acknowledge their publication."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class MemoryStore:
    """Own one deployment-configured file backend, not Control, device or Task authority."""

    def __init__(
        self,
        root: Path,
        provider_id: str,
        input_roots: tuple[Path, ...],
        max_bytes: int,
        export_config: Path | None = None,
        export_directory: Path | None = None,
        enable_snapshot_references: bool = False,
    ) -> None:
        """Open a single-writer durable backend with a persistent observed instance identity."""
        if not provider_id or len(provider_id) > 128 or not 0 < max_bytes <= 1024**3:
            raise ValueError("provider identity or byte bound is invalid")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ValueError("backend root cannot be a symlink")
        self.root = root.resolve(strict=True)
        self.provider_id = provider_id
        self.input_roots = tuple(path.resolve(strict=True) for path in input_roots)
        if not self.input_roots or any(not path.is_dir() for path in self.input_roots):
            raise ValueError("explicit existing readonly staging roots are required")
        self.max_bytes = max_bytes
        self.snapshot_references_enabled = enable_snapshot_references
        if (export_config is None) != (export_directory is None):
            raise ValueError("export config and controlled handoff directory must be paired")
        self._export_directory = export_directory.resolve(strict=True) if export_directory else None
        self._exports: list[dict[str, Any]] = []
        if export_config is not None:
            if export_config.stat().st_size > MAX_METADATA_BYTES:
                raise ValueError("export catalog exceeds its metadata bound")
            exports = json.loads(export_config.read_bytes())
            if not isinstance(exports, list) or len(exports) > 128:
                raise ValueError("export catalog must contain at most 128 explicit bindings")
            for binding in exports:
                if not isinstance(binding, dict) or set(binding) != {"manifest", "artifact_path"}:
                    raise ValueError("export catalog binding has invalid fields")
                name = binding["artifact_path"]
                if (
                    not isinstance(name, str)
                    or not name
                    or Path(name).is_absolute()
                    or ".." in Path(name).parts
                ):
                    raise ValueError("export handoff requires a bounded relative path")
                self._exports.append(binding)
        self._mutex = threading.RLock()
        self._storage = ProviderStorage(self.root)
        try:
            self._db = self._storage.connect("memory.sqlite3")
            self._initialize_storage()
            instance, saved_provider = self._db.execute(
                "SELECT instance,provider FROM identity WHERE singleton=1"
            ).fetchone()
            if saved_provider != provider_id:
                raise ValueError("stored provider ownership differs from configuration")
            self.instance_id = str(instance)
            if enable_snapshot_references:
                self._enable_snapshot_references()
            self._content = self.root / "content"
            self._content.mkdir(mode=0o700, exist_ok=True)
            if self._content.is_symlink():
                raise ValueError("content root cannot be a symlink")
            sync_directory(self.root)
            from .replica_references import ReplicaReferences

            self.replica_references = ReplicaReferences(self)
            self.replica_references.restore()
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._storage.close_unopened()
            raise

    def _initialize_storage(self) -> None:
        """Migrate only quiescent original SQL and durably fence older provider writers."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            raise ValueError("unsupported Memory backend schema")
        if version in (3, 4):
            if version == 4 and not self.snapshot_references_enabled:
                raise ValueError("Memory v4 requires explicit snapshot references configuration")
            marker = self._db.execute(
                "SELECT schema,databases_json FROM provider_writer_format"
            ).fetchall()
            writer_schema = SNAPSHOT_WRITER_SCHEMA if version == 4 else WRITER_SCHEMA
            if marker != [(writer_schema, canonical(list(DATABASES)).decode())]:
                raise ValueError("original provider writer marker is unavailable")
            required = {
                "identity",
                "imports",
                "content_tombstones",
                "reclamation_commands",
                "retired_imports",
                "content_references",
                "provider_writer_format",
            }
            if version == 4:
                required.add("snapshot_operations")
            actual = {
                row[0]
                for row in self._db.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            if not required <= actual:
                raise ValueError("original provider authority table is unavailable")
            if version == 4:
                columns = {
                    row[1] for row in self._db.execute("PRAGMA table_info(snapshot_operations)")
                }
                if columns != {
                    "operation_id",
                    "command_id",
                    "request_json",
                    "state",
                    "receipt_json",
                }:
                    raise ValueError(
                        "original snapshot operation authority columns are unavailable"
                    )
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            return
        if version == 2:
            original = self._db.execute(
                "SELECT provider FROM identity WHERE singleton=1"
            ).fetchone()
            if original != (self.provider_id,):
                raise ValueError("stored provider ownership differs from configuration")
        # SQLite itself requires every legacy connection to relinquish its DB locks. This also
        # fences old aliases that did not implement the new inode lease before any format write.
        if self._db.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise ValueError("legacy provider connections did not become quiescent")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("BEGIN EXCLUSIVE")
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY "
                "CHECK(singleton=1), instance TEXT NOT NULL, provider TEXT NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS imports (import_id TEXT PRIMARY KEY, "
                "original_json BLOB NOT NULL, manifest_json BLOB NOT NULL, "
                "content_digest TEXT NOT NULL, byte_size INTEGER NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN ('pending','completed')))"
            )
            self._db.execute(
                "INSERT OR IGNORE INTO identity VALUES (1,?,?)",
                (str(uuid.uuid4()), self.provider_id),
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS content_tombstones (content_digest TEXT PRIMARY KEY, "
                "command_id TEXT UNIQUE NOT NULL, state TEXT NOT NULL "
                "CHECK(state IN ('tombstoned','deleted')))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS reclamation_commands (command_id TEXT PRIMARY KEY, "
                "request_json BLOB NOT NULL, imports_json BLOB NOT NULL, state TEXT NOT NULL, "
                "file_json BLOB, receipt_json BLOB)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS retired_imports (import_id TEXT PRIMARY KEY, "
                "command_id TEXT NOT NULL, completion_json BLOB NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS content_references (reference_id TEXT PRIMARY KEY, "
                "content_digest TEXT NOT NULL, kind TEXT NOT NULL, owner TEXT NOT NULL, "
                "generation INTEGER NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)))"
            )
            self._db.execute(
                "CREATE TABLE provider_writer_format "
                "(schema TEXT PRIMARY KEY, databases_json TEXT NOT NULL)"
            )
            self._db.execute(
                "INSERT INTO provider_writer_format VALUES (?,?)",
                (WRITER_SCHEMA, canonical(list(DATABASES)).decode()),
            )
            self._db.execute("PRAGMA user_version=3")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        # DELETE-mode Commit places the format/old-writer fence in the actual main file, so an
        # older alias cannot miss it by choosing a different WAL filename.
        self._db.execute("PRAGMA journal_mode=WAL")

    def _enable_snapshot_references(self) -> None:
        """Upgrade only an explicitly selected and already validated original v3 authority."""
        if self._db.execute("PRAGMA user_version").fetchone()[0] == 4:
            return
        if self._db.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise ValueError("original provider snapshot migration requires drained connections")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("BEGIN EXCLUSIVE")
        try:
            self._db.execute(
                "CREATE TABLE snapshot_operations (operation_id TEXT PRIMARY KEY, "
                "command_id TEXT UNIQUE NOT NULL, "
                "request_json BLOB NOT NULL, state TEXT NOT NULL "
                "CHECK(state IN ('active','released','aborted')), receipt_json BLOB)"
            )
            self._db.execute(
                "UPDATE provider_writer_format SET schema=? WHERE schema=?",
                (SNAPSHOT_WRITER_SCHEMA, WRITER_SCHEMA),
            )
            self._db.execute("PRAGMA user_version=4")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        self._db.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        """Close this connection; sibling writers retain every original root/inode lease."""
        with self._mutex:
            self._db.close()

    def identity(self) -> dict[str, str]:
        """Return the persistent identity actually read from this backend."""
        return {"instance_id": self.instance_id, "provider_id": self.provider_id}

    def require_content_live(self, digest: str) -> None:
        """Fence a local tombstone independently of new import IDs, leases or retained history."""
        with self._mutex:
            if self._db.execute(
                "SELECT 1 FROM content_tombstones WHERE content_digest=?", (digest,)
            ).fetchone():
                raise ValueError("original provider content is durably tombstoned")

    def _export_path(self, binding: dict[str, Any]) -> Path:
        """Resolve and rehash one configured immutable export from its controlled handoff root."""
        if self._export_directory is None:
            raise ValueError("provider has no configured publish authorization")
        path = self._export_directory / str(binding["artifact_path"])
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self._export_directory):
            raise ValueError("configured export escaped the controlled handoff root")
        digest, size = self._artifact(binding["manifest"])
        with self._open_staged(str(path)) as source:
            checksum = hashlib.sha256()
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > size:
                    raise ValueError("configured export grew beyond its original byte bound")
                checksum.update(chunk)
            if (
                os.fstat(source.fileno()).st_size != size
                or copied != size
                or checksum.hexdigest() != digest[7:]
            ):
                raise ValueError("configured export bytes differ from their immutable manifest")
        return path

    def discover(self) -> list[dict[str, Any]]:
        """Publish only explicit startup-authorized manifests backed by actual matching files."""
        with self._mutex:
            manifests = []
            for binding in self._exports:
                self._export_path(binding)
                manifests.append(dict(binding["manifest"]))
            return manifests

    def export(self, manifest: dict[str, Any]) -> str:
        """Return a controlled relative handoff path only for an exact authorized manifest."""
        with self._mutex:
            matches = [binding for binding in self._exports if binding["manifest"] == manifest]
            if len(matches) != 1:
                raise ValueError("manifest is not uniquely authorized for local export")
            self._export_path(matches[0])
            return str(matches[0]["artifact_path"])

    def _validate_original(self, original: dict[str, Any]) -> dict[str, Any]:
        """Check the exact original immutable tuple and this actual backend instance."""
        expected = {
            "import_id",
            "command_id",
            "provider_id",
            "identity_digest",
            "original_content_digest",
            "provider_instance_id",
            "provider_contract_digest",
            "original_invocation",
            "admitted_at_ms",
        }
        if set(original) != expected or len(canonical(original)) > MAX_METADATA_BYTES:
            raise ValueError("complete original continuity metadata is required")
        invocation = original["original_invocation"]
        if not isinstance(invocation, dict):
            raise ValueError("original invocation is unavailable")
        if (
            original["provider_id"] != self.provider_id
            or original["provider_instance_id"] != self.instance_id
            or original["import_id"] != invocation.get("import_id")
            or original["command_id"] != f"memory-import-{original['import_id']}"
            or invocation.get("command_id") != original["command_id"]
            or invocation.get("consumer_provider_id") != self.provider_id
            or original["original_content_digest"] != original_content_digest(invocation)
            or not isinstance(original["identity_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", original["identity_digest"]) is None
            or not isinstance(original["provider_contract_digest"], str)
            or DIGEST.fullmatch(original["provider_contract_digest"]) is None
        ):
            raise ValueError("original import/provider/instance identity differs")
        manifest = json.loads(invocation["manifest_json"])
        if not isinstance(manifest, dict):
            raise ValueError("original immutable manifest is unavailable")
        return manifest

    def _artifact(self, manifest: dict[str, Any]) -> tuple[str, int]:
        """Extract an exact digest and bounded byte count from actual manifest metadata."""
        artifact = manifest.get("artifact")
        if not isinstance(artifact, dict) or set(artifact) != {"content_digest", "byte_size"}:
            raise ValueError("an immutable CAS artifact is required")
        digest, size = artifact["content_digest"], artifact["byte_size"]
        if (
            not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or type(size) is not int
            or not 0 <= size <= self.max_bytes
        ):
            raise ValueError("artifact digest or configured byte bound is invalid")
        return digest, size

    def _open_staged(self, name: str) -> BinaryIO:
        """Open an actual regular staged file below explicit roots without symlink traversal."""
        path = Path(name)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("staged artifact requires an absolute non-symlink file")
        root = next((root for root in self.input_roots if path.is_relative_to(root)), None)
        if root is None:
            raise ValueError("staged artifact is outside configured readonly input roots")
        relative = path.relative_to(root)
        if not relative.parts or any(part in (".", "..") for part in relative.parts):
            raise ValueError("invalid staged artifact path")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            descriptor = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("staged artifact is not a regular file")
        return os.fdopen(descriptor, "rb")

    def _verify_content(self, digest: str, size: int) -> bool:
        """Rehash current backend bytes; catalog Imported history alone never proves presence."""
        try:
            self.require_content_live(digest)
        except ValueError:
            return False
        try:
            descriptor = os.open(self._content / digest[7:], os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return False
            observed = hashlib.file_digest(source, "sha256").hexdigest()
            after = os.fstat(source.fileno())
        return observed == digest[7:] and (before.st_size, before.st_mtime_ns) == (
            after.st_size,
            after.st_mtime_ns,
        )

    def import_artifact(
        self, original: dict[str, Any], manifest: dict[str, Any], staged_artifact: str
    ) -> dict[str, object]:
        """Persist identity before copying real bytes; retry never replaces or repeats an import."""
        with self._mutex:
            expected_manifest = self._validate_original(original)
            if manifest != expected_manifest:
                raise ValueError("import manifest differs from original authorization")
            digest, size = self._artifact(manifest)
            self.require_content_live(digest)
            identity, manifest_bytes = canonical(original), canonical(manifest)
            existing = self._db.execute(
                "SELECT original_json,manifest_json FROM imports WHERE import_id=?",
                (original["import_id"],),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (identity, manifest_bytes):
                    raise ValueError("original import identity conflict")
                result = self.query(original)
                if result["outcome"] != "completed":
                    raise ValueError("original import remains ambiguous; retry cannot copy again")
                return {"result": result, "replayed": True}
            with self._db:
                self._db.execute(
                    "INSERT INTO imports VALUES (?,?,?,?,?,'pending')",
                    (original["import_id"], identity, manifest_bytes, digest, size),
                )
                self.replica_references.acquire(original)
            target = self._content / digest[7:]
            temporary: str | None = None
            try:
                with self._open_staged(staged_artifact) as source:
                    before = os.fstat(source.fileno())
                    if before.st_size != size:
                        raise ValueError("staged artifact byte count differs")
                    descriptor, temporary = tempfile.mkstemp(prefix=".import-", dir=self._content)
                    with os.fdopen(descriptor, "wb") as output:
                        copied = 0
                        checksum = hashlib.sha256()
                        while chunk := source.read(min(1024 * 1024, size - copied + 1)):
                            copied += len(chunk)
                            if copied > size:
                                raise ValueError("staged artifact grew beyond its original bound")
                            checksum.update(chunk)
                            output.write(chunk)
                        after = os.fstat(source.fileno())
                        if (
                            copied != size
                            or checksum.hexdigest() != digest[7:]
                            or (before.st_size, before.st_mtime_ns)
                            != (after.st_size, after.st_mtime_ns)
                        ):
                            raise ValueError("staged content changed or fails its original digest")
                        output.flush()
                        os.fsync(output.fileno())
                    os.chmod(temporary, 0o400)
                    # Hard-link publication is atomic and cannot overwrite an existing CAS object.
                    try:
                        os.link(temporary, target, follow_symlinks=False)
                    except FileExistsError:
                        if not self._verify_content(digest, size):
                            raise ValueError("existing backend CAS content is corrupt") from None
                    sync_directory(self._content)
                with self._db:
                    self._db.execute(
                        "UPDATE imports SET state='completed' WHERE import_id=?",
                        (original["import_id"],),
                    )
            finally:
                if temporary is not None:
                    with contextlib.suppress(FileNotFoundError):
                        Path(temporary).unlink()
            result = self.query(original)
            if result["outcome"] != "completed":
                raise ValueError("current original import content is unavailable after publication")
            return {"result": result, "replayed": False}

    def query(self, original: dict[str, Any]) -> dict[str, object]:
        """Read original evidence and current bytes without SQL writes or reimport."""
        with self._mutex:
            manifest = self._validate_original(original)
            digest, size = self._artifact(manifest)
            row = self._db.execute(
                "SELECT original_json,manifest_json,content_digest,byte_size,state "
                "FROM imports WHERE import_id=?",
                (original["import_id"],),
            ).fetchone()
            if row is not None and tuple(row[:4]) != (
                canonical(original),
                canonical(manifest),
                digest,
                size,
            ):
                raise ValueError("stored original-import evidence conflicts")
            retired = self._db.execute(
                "SELECT completion_json FROM retired_imports WHERE import_id=?",
                (original["import_id"],),
            ).fetchone()
            if retired is not None:
                # Original completion remains history; availability independently fences tombstones.
                return dict(json.loads(retired[0]))
            # For this file backend the entire semantic effect is exact immutable content plus
            # its durable original manifest row. A pending intent with matching bytes therefore
            # proves that effect, including a crash after fsync and before the final SQL update.
            complete = row is not None and self._verify_content(digest, size)
            keys = (
                "import_id",
                "command_id",
                "provider_id",
                "identity_digest",
                "original_content_digest",
                "provider_instance_id",
                "provider_contract_digest",
            )
            return {
                "schema": "roboguide.memory-import-query-result/v0.1",
                **{key: original[key] for key in keys},
                "outcome": "completed" if complete else "unknown",
                "durable": complete,
                "reason": ""
                if complete
                else "original operation or current content is unavailable",
            }

    def availability(self, original: dict[str, Any]) -> dict[str, object]:
        """Read actual full bytes under original identity without writes or lease renewal."""
        with self._mutex:
            manifest = self._validate_original(original)
            digest, size = self._artifact(manifest)
            observed = self.query(original)
            available = (
                observed["outcome"] == "completed"
                and observed["durable"] is True
                and self._db.execute(
                    "SELECT 1 FROM content_tombstones WHERE content_digest=?", (digest,)
                ).fetchone()
                is None
            )
            return {
                "schema": "roboguide.memory-provider-availability/v0.1",
                "import_id": original["import_id"],
                "identity_digest": original["identity_digest"],
                "provider_instance_id": self.instance_id,
                "provider_contract_digest": original["provider_contract_digest"],
                "manifest_digest": content_digest(manifest),
                "content_digest": digest,
                "byte_size": size,
                "available": available,
                "reason": "" if available else "original current bytes are unavailable",
            }
