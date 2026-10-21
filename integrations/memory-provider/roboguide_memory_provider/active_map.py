"""Durable Local EAIOS active-map selection, query, handoff and GC gates."""

# Bounded SQL statements remain one statement per transaction for audit review.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import stat
import time
import zipfile
from pathlib import Path
from typing import Any, Protocol, cast

from .store import MemoryStore, canonical

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TILES = 256
MAX_QUERIES = 4096


class ActiveMapError(ValueError):
    """Reject an active-map request without changing the current selection or bytes."""


class LeaseGuard(Protocol):
    """Structural current-use lease interface required by active-map effects."""

    def guard(self, lease_id: str, execution_id: str) -> None:
        """Reject an expired, revoked or mismatched current-use context."""

    def guard_map(self, context: dict[str, Any]) -> None:
        """Bind the original map and complete Node/Group/attempt owner before effects."""


def _digest_bytes(value: bytes) -> str:
    """Return the canonical digest used by CAS and immutable map evidence."""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _text(value: object, name: str, maximum: int = 256) -> str:
    """Require bounded identity text without accepting paths or control characters."""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode()) > maximum
        or value != value.strip()
        or any(character in value for character in "\x00\r\n")
    ):
        raise ActiveMapError(f"{name} is empty, unbounded or contains control characters")
    return value


def _digest(value: object, name: str) -> str:
    """Require one lowercase SHA-256 identity in external active-map metadata."""
    text = _text(value, name, 71)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ActiveMapError(f"{name} is not a SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in text[7:]):
        raise ActiveMapError(f"{name} is not a lowercase SHA-256 digest")
    return text


def _point(value: object, name: str) -> float:
    """Require one finite coordinate or radius from a bounded semantic query."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveMapError(f"{name} is not numeric")
    number = float(value)
    if not number == number or number in (float("inf"), float("-inf")):
        raise ActiveMapError(f"{name} is not finite")
    return number


def _bundle_path(store: MemoryStore, digest: str) -> Path:
    """Resolve only a digest-derived CAS path, never a caller-supplied filesystem path."""
    path = store._content / digest[7:]
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ActiveMapError("active-map CAS object is not a regular file")
    if metadata.st_size > MAX_BUNDLE_BYTES:
        raise ActiveMapError("active-map bundle exceeds its bounded size")
    return path


def _load_bundle(store: MemoryStore, request: dict[str, Any]) -> dict[str, Any]:
    """Verify actual ZIP bytes, manifest identity, frame metadata and every tile digest."""
    bundle_digest = _digest(request.get("content_digest"), "content_digest")
    byte_size = request.get("byte_size")
    if type(byte_size) is not int or not 0 < byte_size <= MAX_BUNDLE_BYTES:
        raise ActiveMapError("active-map byte_size is outside its bound")
    path = _bundle_path(store, bundle_digest)
    if path.stat().st_size != byte_size:
        raise ActiveMapError("active-map CAS byte count differs from its immutable reference")
    with path.open("rb") as stream:
        checksum = hashlib.sha256()
        copied = 0
        while chunk := stream.read(1024 * 1024):
            copied += len(chunk)
            if copied > MAX_BUNDLE_BYTES:
                raise ActiveMapError("active-map bundle exceeds its bounded size")
            checksum.update(chunk)
        actual_digest = "sha256:" + checksum.hexdigest()
    if actual_digest != bundle_digest:
        raise ActiveMapError("active-map CAS bytes fail their immutable digest")
    with zipfile.ZipFile(path) as bundle:
        entries = bundle.infolist()
        if not entries or len(entries) > MAX_TILES + 1:
            raise ActiveMapError("active-map bundle has an invalid entry count")
        names: set[str] = set()
        for entry in entries:
            name = entry.filename
            if (
                name in names
                or not name
                or name.startswith("/")
                or ".." in Path(name).parts
                or entry.compress_type != zipfile.ZIP_STORED
                or entry.flag_bits & 0x1
                or entry.file_size > MAX_BUNDLE_BYTES
            ):
                raise ActiveMapError("active-map bundle contains an unsafe or duplicate entry")
            names.add(name)
        if "manifest.json" not in names:
            raise ActiveMapError("active-map bundle has no manifest.json")
        raw = bundle.read("manifest.json")
        if len(raw) > MAX_METADATA_BYTES:
            raise ActiveMapError("active-map manifest exceeds its bound")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ActiveMapError("active-map manifest is not an object")
        required = {
            "schema_version",
            "revision_id",
            "frame_id",
            "units",
            "voxel_size_m",
            "tiles",
            "sources",
            "unobserved_space",
        }
        if (
            not required <= set(manifest)
            or manifest["schema_version"] != "roboguide.spatial-file-map/v0.1"
        ):
            raise ActiveMapError("active-map manifest schema is unsupported")
        revision = _text(manifest["revision_id"], "revision_id")
        if revision != _text(request.get("revision_id"), "revision_id"):
            raise ActiveMapError("active-map revision differs from the selected revision")
        frame = _text(manifest["frame_id"], "frame_id")
        if frame != _text(request.get("frame_id"), "frame_id"):
            raise ActiveMapError("active-map frame differs from the selected frame")
        if manifest["units"] != "m" or _point(manifest["voxel_size_m"], "voxel_size_m") <= 0:
            raise ActiveMapError("active-map units or resolution is invalid")
        tiles = manifest["tiles"]
        if not isinstance(tiles, list) or not 1 <= len(tiles) <= MAX_TILES:
            raise ActiveMapError("active-map tile index is outside its bound")
        tile_digests: list[str] = []
        total_points = 0
        for tile in tiles:
            if not isinstance(tile, dict) or not {
                "path",
                "content_digest",
                "point_count",
                "bounds",
            } <= set(tile):
                raise ActiveMapError("active-map tile metadata is incomplete")
            tile_path = _text(tile["path"], "tile.path", 512)
            if tile_path not in names or not tile_path.startswith("tiles/"):
                raise ActiveMapError("active-map tile path is outside the bundle")
            tile_digest = _digest(tile["content_digest"], "tile.content_digest")
            point_count = tile["point_count"]
            if type(point_count) is not int or not 1 <= point_count <= 2_000_000:
                raise ActiveMapError("active-map tile point count is invalid")
            bounds = tile["bounds"]
            if (
                not isinstance(bounds, dict)
                or set(bounds) != {"min", "max"}
                or not isinstance(bounds["min"], list)
                or not isinstance(bounds["max"], list)
                or len(bounds["min"]) != 3
                or len(bounds["max"]) != 3
            ):
                raise ActiveMapError("active-map tile bounds are invalid")
            for value in bounds["min"] + bounds["max"]:
                _point(value, "tile.bound")
            if any(
                float(low) > float(high)
                for low, high in zip(bounds["min"], bounds["max"], strict=True)
            ):
                raise ActiveMapError("active-map tile bounds are invalid")
            if _digest_bytes(bundle.read(tile_path)) != tile_digest:
                raise ActiveMapError("active-map tile bytes differ from their manifest digest")
            tile_digests.append(tile_digest)
            total_points += point_count
        sources = manifest["sources"]
        if not isinstance(sources, list) or not sources or len(sources) > MAX_TILES:
            raise ActiveMapError("active-map raw source lineage is unavailable")
        for source in sources:
            if not isinstance(source, dict) or not {
                "source_id",
                "content_digest",
                "byte_size",
                "frame_id",
                "units",
                "format",
                "point_count",
            } <= set(source):
                raise ActiveMapError("active-map raw source lineage is incomplete")
            _text(source["source_id"], "source_id")
            _digest(source["content_digest"], "source.content_digest")
            if type(source["byte_size"]) is not int or source["byte_size"] <= 0:
                raise ActiveMapError("active-map raw source byte count is invalid")
            if source["units"] != "m" or not _text(source["frame_id"], "source.frame_id"):
                raise ActiveMapError("active-map raw source frame or units differ")
            if type(source["point_count"]) is not int or source["point_count"] <= 0:
                raise ActiveMapError("active-map raw source point count is invalid")
        if manifest["unobserved_space"] != "unknown":
            raise ActiveMapError("active-map must preserve unobserved space as unknown")
    return {
        "bundle_digest": bundle_digest,
        "byte_size": byte_size,
        "revision_id": revision,
        "frame_id": frame,
        "tile_digests": tile_digests,
        "tile_count": len(tile_digests),
        "point_count": total_points,
        "source_count": len(sources),
        "assurance": "software-file-map",
    }


class ActiveMapLedger:
    """Own durable active-map selection while leaving Control and physical localization authoritative."""

    def __init__(self, root: Path, store: MemoryStore, leases: LeaseGuard | None) -> None:
        """Open a private WAL ledger and fence every active selection after a process restart."""
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.resolve() != store.root / "active-map":
            raise ActiveMapError("active-map SQL must use its original provider managed directory")
        self.store = store
        self.leases = leases
        self._lock = store._mutex
        self._db = store._storage.connect("active-map/active-map.sqlite3")
        try:
            self._initialize_database()
        except BaseException:
            self._db.close()
            raise

    def _initialize_database(self) -> None:
        """Open only this connection's original schema; failed construction retains no writer."""
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self._db.close()
            raise ActiveMapError("unsupported active-map ledger schema")
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS heads(selector TEXT PRIMARY KEY, generation INTEGER NOT NULL, state TEXT NOT NULL, context_json BLOB NOT NULL, proof_json BLOB NOT NULL, updated_at_ms INTEGER NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS queries(query_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, result_json BLOB NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS handoffs(barrier_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, state TEXT NOT NULL, request_json BLOB NOT NULL, updated_at_ms INTEGER NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS refs(ref_id TEXT PRIMARY KEY, selector TEXT NOT NULL, digest TEXT NOT NULL, ref_kind TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS history(selector TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, context_json BLOB NOT NULL, proof_json BLOB NOT NULL, updated_at_ms INTEGER NOT NULL, PRIMARY KEY(selector,generation))"
            )
            self._db.execute("UPDATE heads SET state='stale_after_restart' WHERE state='active'")
            self._db.execute("UPDATE refs SET active=0 WHERE ref_kind='active_map'")
            self._db.execute("PRAGMA user_version=2")

    def close(self) -> None:
        """Close the durable active-map ledger without deleting historical selection evidence."""
        with self._lock:
            self._db.close()

    def _lease(self, request: dict[str, Any]) -> None:
        """Require an exact current use lease before selecting or querying a map."""
        if self.leases is None:
            raise ActiveMapError("active-map requires the explicit current-use lease profile")
        lease_id = _text(request.get("use_lease_id"), "use_lease_id")
        execution_id = _text(request.get("execution_id"), "execution_id", 1024)
        try:
            self.leases.guard(lease_id, execution_id)
            self.leases.guard_map(request)
            self.store.require_content_live(request["content_digest"])
        except Exception as error:
            raise ActiveMapError(str(error)) from error

    def _closed_context(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate immutable attempt, node, group, map and lease context shared by all commands."""
        required = {
            "schema",
            "map_id",
            "revision_id",
            "content_digest",
            "byte_size",
            "frame_id",
            "spatial_anchor_id",
            "attempt_id",
            "execution_id",
            "use_lease_id",
            "generation",
            "node_id",
            "group_id",
        }
        if set(request) != required:
            raise ActiveMapError("active-map request is not closed")
        if request["schema"] != "roboguide.active-map/v0.1":
            raise ActiveMapError("unsupported active-map request schema")
        values: dict[str, Any] = {
            "map_id": _text(request["map_id"], "map_id"),
            "revision_id": _text(request["revision_id"], "revision_id"),
            "content_digest": _digest(request["content_digest"], "content_digest"),
            "frame_id": _text(request["frame_id"], "frame_id"),
            "spatial_anchor_id": _text(request["spatial_anchor_id"], "spatial_anchor_id"),
            "attempt_id": _text(request["attempt_id"], "attempt_id", 1024),
            "execution_id": _text(request["execution_id"], "execution_id", 1024),
            "use_lease_id": _text(request["use_lease_id"], "use_lease_id"),
            "node_id": _text(request["node_id"], "node_id"),
            "group_id": _text(request["group_id"], "group_id"),
        }
        if type(request["byte_size"]) is not int or type(request["generation"]) is not int:
            raise ActiveMapError("active-map byte_size/generation must be integers")
        if request["generation"] < 1 or request["generation"] >= 2**63:
            raise ActiveMapError("active-map generation is outside its bound")
        values.update(byte_size=request["byte_size"], generation=request["generation"])
        return values

    def select(self, request: dict[str, Any]) -> dict[str, Any]:
        """Serialize actual map validation, current authorization and installation with reclamation."""
        with self._lock:
            return self._select(request)

    def _select(self, request: dict[str, Any]) -> dict[str, Any]:
        """Select one verified immutable map revision with generation CAS and current lease proof."""
        context = self._closed_context(request)
        self._lease(context)
        proof = _load_bundle(self.store, context)
        selector = f"{context['map_id']}:{context['revision_id']}"
        now = time.time_ns() // 1_000_000
        with self._lock, self._db:
            self._lease(context)
            row = self._db.execute(
                "SELECT generation,state,context_json,proof_json,updated_at_ms FROM heads WHERE selector=?",
                (selector,),
            ).fetchone()
            expected = 1 if row is None else int(row[0]) + 1
            if context["generation"] != expected:
                raise ActiveMapError("active-map generation CAS failed")
            context_json = canonical(context)
            proof = {**proof, "selector": selector, "context_digest": _digest_bytes(context_json)}
            if row is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)",
                    (selector, row[0], row[1], row[2], row[3], row[4]),
                )
            self._db.execute(
                "UPDATE refs SET active=0 WHERE selector=? AND ref_kind='active_map'", (selector,)
            )
            self._db.execute(
                "INSERT INTO heads VALUES (?,?,?,?,?,?) ON CONFLICT(selector) DO UPDATE SET generation=excluded.generation,state=excluded.state,context_json=excluded.context_json,proof_json=excluded.proof_json,updated_at_ms=excluded.updated_at_ms",
                (selector, context["generation"], "active", context_json, canonical(proof), now),
            )
            ref_id = f"active-map:{selector}:{context['generation']}"
            self._db.execute(
                "INSERT OR REPLACE INTO refs VALUES (?,?,?,?,1)",
                (ref_id, selector, context["content_digest"], "active_map"),
            )
        return {
            "schema": "roboguide.active-map-selection/v0.1",
            "state": "active",
            **context,
            "proof": proof,
        }

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        """Hold the actual content ordering through all bytes and the final permission recheck."""
        with self._lock:
            return self._query(request)

    def _query(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run a bounded semantic tile query against the selected file map, never localization."""
        base = self._closed_context(
            {
                key: request[key]
                for key in (
                    "schema",
                    "map_id",
                    "revision_id",
                    "content_digest",
                    "byte_size",
                    "frame_id",
                    "spatial_anchor_id",
                    "attempt_id",
                    "execution_id",
                    "use_lease_id",
                    "generation",
                    "node_id",
                    "group_id",
                )
            }
        )
        self._lease(base)
        query_id = _text(request.get("query_id"), "query_id")
        point = [_point(request.get(axis), axis) for axis in ("x", "y", "z")]
        radius = _point(request.get("radius", 0.0), "radius")
        if radius < 0 or radius > 1000:
            raise ActiveMapError("active-map query radius is outside its bound")
        selector = f"{base['map_id']}:{base['revision_id']}"
        with self._lock:
            row = self._db.execute(
                "SELECT generation,state,context_json,proof_json FROM heads WHERE selector=?",
                (selector,),
            ).fetchone()
            if row is None or row[1] != "active" or int(row[0]) != base["generation"]:
                raise ActiveMapError("active-map selection is not current after restart or release")
            if json.loads(row[2]) != base:
                raise ActiveMapError("active-map query context differs from selection")
            previous = self._db.execute(
                "SELECT request_digest,result_json FROM queries WHERE query_id=?", (query_id,)
            ).fetchone()
            request_digest = _digest_bytes(canonical(request))
            if previous is not None:
                if previous[0] != request_digest:
                    raise ActiveMapError(
                        "active-map query identity was reused with different inputs"
                    )
                return cast(dict[str, Any], json.loads(previous[1]))
        bundle = _load_bundle(self.store, base)
        path = _bundle_path(self.store, base["content_digest"])
        hits: list[dict[str, Any]] = []
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for tile in manifest["tiles"]:
                bounds = tile["bounds"]
                distance = (
                    sum(
                        max(
                            float(bounds["min"][index]) - point[index],
                            0.0,
                            point[index] - float(bounds["max"][index]),
                        )
                        ** 2
                        for index in range(3)
                    )
                    ** 0.5
                )
                if distance <= radius:
                    hits.append(
                        {
                            "tile_digest": tile["content_digest"],
                            "point_count": tile["point_count"],
                            "distance_m": distance,
                        }
                    )
        result = {
            "schema": "roboguide.active-map-query/v0.1",
            "query_id": query_id,
            "selector": selector,
            "generation": base["generation"],
            "hits": hits,
            "unobserved_space": "unknown",
            "localization": None,
            "proof": bundle,
        }
        with self._lock, self._db:
            self._lease(base)
            if self._db.execute("SELECT COUNT(*) FROM queries").fetchone()[0] >= MAX_QUERIES:
                raise ActiveMapError("active-map query history is full")
            self._db.execute(
                "INSERT INTO queries VALUES (?,?,?)", (query_id, request_digest, canonical(result))
            )
        return result

    def release(self, request: dict[str, Any]) -> dict[str, Any]:
        """Release one current selection while retaining immutable history and CAS bytes."""
        context = self._closed_context(request)
        self._lease(context)
        selector = f"{context['map_id']}:{context['revision_id']}"
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT generation,state,context_json FROM heads WHERE selector=?", (selector,)
            ).fetchone()
            if (
                row is None
                or row[1] != "active"
                or int(row[0]) != context["generation"]
                or json.loads(row[2]) != context
            ):
                raise ActiveMapError("active-map release context is not current")
            self._db.execute(
                "UPDATE heads SET state='released',updated_at_ms=? WHERE selector=?",
                (time.time_ns() // 1_000_000, selector),
            )
            released = self._db.execute(
                "SELECT selector,generation,state,context_json,proof_json,updated_at_ms FROM heads WHERE selector=?",
                (selector,),
            ).fetchone()
            if released is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)",
                    released,
                )
            self._db.execute(
                "UPDATE refs SET active=0 WHERE selector=? AND ref_kind='active_map'", (selector,)
            )
        return {
            "schema": "roboguide.active-map-release/v0.1",
            "state": "released",
            "selector": selector,
            "generation": context["generation"],
        }

    def state(self) -> dict[str, Any]:
        """Return current and historical selections without treating history as active availability."""
        with self._lock:
            rows = self._db.execute(
                "SELECT selector,generation,state,proof_json FROM heads ORDER BY selector"
            ).fetchall()
            history = self._db.execute(
                "SELECT selector,generation,state,proof_json FROM history ORDER BY selector,generation"
            ).fetchall()
        return {
            "schema": "roboguide.active-map-state/v0.1",
            "instance_id": self.store.instance_id,
            "heads": [
                {
                    "selector": row[0],
                    "generation": row[1],
                    "state": row[2],
                    "proof": json.loads(row[3]),
                }
                for row in rows
            ],
            "history": [
                {
                    "selector": row[0],
                    "generation": row[1],
                    "state": row[2],
                    "proof": json.loads(row[3]),
                }
                for row in history
            ],
        }

    def prepare_handoff(self, request: dict[str, Any]) -> dict[str, Any]:
        """Serialize the frozen source/target purpose with original content and use authority."""
        with self._lock:
            return self._prepare_handoff(request)

    def _prepare_handoff(self, request: dict[str, Any]) -> dict[str, Any]:
        """Persist a whole-group software stop barrier before any target map rebind."""
        required = {"schema", "barrier_id", "group_id", "source", "target", "stop_barrier"}
        if set(request) != required or request["schema"] != "roboguide.active-map-handoff/v0.1":
            raise ActiveMapError("active-map handoff request is not closed")
        barrier_id = _text(request["barrier_id"], "barrier_id")
        group_id = _text(request["group_id"], "group_id")
        source = dict(request["source"])
        target = dict(request["target"])
        source["schema"] = target["schema"] = "roboguide.active-map/v0.1"
        source_context = self._closed_context(source)
        target_context = self._closed_context(target)
        if source_context["group_id"] != group_id or target_context["group_id"] != group_id:
            raise ActiveMapError("active-map handoff crosses the source Group")
        if (
            target_context["generation"] != source_context["generation"] + 1
            or source_context["map_id"] != target_context["map_id"]
            or source_context["revision_id"] != target_context["revision_id"]
            or source_context["content_digest"] != target_context["content_digest"]
        ):
            raise ActiveMapError("active-map handoff generation is not contiguous")
        stop_barrier = request["stop_barrier"]
        if (
            not isinstance(stop_barrier, dict)
            or set(stop_barrier) != {"schema", "status", "attempts", "confirmed_attempts"}
            or stop_barrier["schema"] != "roboguide.software-stop-barrier/v0.1"
            or stop_barrier["status"] != "software_stopped"
            or not isinstance(stop_barrier["attempts"], list)
            or not isinstance(stop_barrier["confirmed_attempts"], list)
            or sorted(stop_barrier["attempts"]) != sorted(stop_barrier["confirmed_attempts"])
            or len(set(stop_barrier["attempts"])) != len(stop_barrier["attempts"])
            or not stop_barrier["attempts"]
            or source_context["execution_id"] not in stop_barrier["attempts"]
        ):
            raise ActiveMapError("handoff requires a complete software stop barrier")
        self._lease(source_context)
        selector = f"{source_context['map_id']}:{source_context['revision_id']}"
        now = time.time_ns() // 1_000_000
        with self._lock, self._db:
            self._lease(source_context)
            row = self._db.execute(
                "SELECT generation,state,context_json FROM heads WHERE selector=?", (selector,)
            ).fetchone()
            if (
                row is None
                or row[1] != "active"
                or int(row[0]) != source_context["generation"]
                or json.loads(row[2]) != source_context
            ):
                raise ActiveMapError("handoff source selection is not current")
            self._db.execute(
                "INSERT INTO handoffs VALUES (?,?,?,?,?)",
                (barrier_id, group_id, "prepared", canonical(request), now),
            )
        return {
            "schema": "roboguide.active-map-handoff/v0.1",
            "barrier_id": barrier_id,
            "state": "prepared",
            "software_stop_only": True,
        }

    def commit_handoff(self, request: dict[str, Any]) -> dict[str, Any]:
        """Commit only the frozen source/target under the same actual local-content ordering."""
        with self._lock:
            return self._commit_handoff(request)

    def _commit_handoff(self, request: dict[str, Any]) -> dict[str, Any]:
        """Atomically bind the verified target revision after the durable stop barrier."""
        required = {"schema", "barrier_id", "target"}
        if (
            set(request) != required
            or request["schema"] != "roboguide.active-map-handoff-commit/v0.1"
        ):
            raise ActiveMapError("active-map handoff commit is not closed")
        barrier_id = _text(request["barrier_id"], "barrier_id")
        target = dict(request["target"])
        target["schema"] = "roboguide.active-map/v0.1"
        target_context = self._closed_context(target)
        self._lease(target_context)
        with self._lock:
            barrier_row = self._db.execute(
                "SELECT group_id,state,request_json FROM handoffs WHERE barrier_id=?", (barrier_id,)
            ).fetchone()
            if barrier_row is None or barrier_row[1] != "prepared":
                raise ActiveMapError("handoff barrier is absent or already consumed")
            prepared = json.loads(barrier_row[2])
            source = dict(prepared["source"])
            source["schema"] = "roboguide.active-map/v0.1"
            source_context = self._closed_context(source)
            prepared_target = dict(prepared["target"])
            prepared_target["schema"] = "roboguide.active-map/v0.1"
            if (
                target_context["group_id"] != barrier_row[0]
                or target_context["generation"] != source_context["generation"] + 1
                or target_context != self._closed_context(prepared_target)
            ):
                raise ActiveMapError("handoff target differs from the prepared source barrier")
            proof = _load_bundle(self.store, target_context)
            source_selector = f"{source_context['map_id']}:{source_context['revision_id']}"
            target_selector = f"{target_context['map_id']}:{target_context['revision_id']}"
            now = time.time_ns() // 1_000_000
            target_proof = {
                **proof,
                "selector": target_selector,
                "handoff_barrier_id": barrier_id,
                "context_digest": _digest_bytes(canonical(target_context)),
            }
            with self._db:
                self._lease(target_context)
                source_head = self._db.execute(
                    "SELECT selector,generation,state,context_json,proof_json,updated_at_ms FROM heads WHERE selector=?",
                    (source_selector,),
                ).fetchone()
                if (
                    source_head is None
                    or source_head[1] != source_context["generation"]
                    or source_head[2] != "active"
                    or json.loads(source_head[3]) != source_context
                ):
                    raise ActiveMapError("handoff source selection changed after preparation")
                if source_selector != target_selector:
                    raise ActiveMapError("handoff cannot overwrite an unrelated map selection")
                self._db.execute(
                    "INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)",
                    (
                        source_head[0],
                        source_head[1],
                        "handed_off",
                        source_head[3],
                        source_head[4],
                        now,
                    ),
                )
                self._db.execute(
                    "UPDATE heads SET state='handed_off',updated_at_ms=? WHERE selector=? AND generation=?",
                    (now, source_selector, source_context["generation"]),
                )
                self._db.execute(
                    "INSERT OR REPLACE INTO heads VALUES (?,?,?,?,?,?)",
                    (
                        target_selector,
                        target_context["generation"],
                        "active",
                        canonical(target_context),
                        canonical(target_proof),
                        now,
                    ),
                )
                self._db.execute(
                    "UPDATE refs SET active=0 WHERE selector=? AND ref_kind='active_map'",
                    (source_selector,),
                )
                self._db.execute(
                    "INSERT OR REPLACE INTO refs VALUES (?,?,?,?,1)",
                    (
                        f"active-map:{target_selector}:{target_context['generation']}",
                        target_selector,
                        target_context["content_digest"],
                        "active_map",
                    ),
                )
                self._db.execute(
                    "UPDATE handoffs SET state='committed',updated_at_ms=? WHERE barrier_id=?",
                    (now, barrier_id),
                )
        return {
            "schema": "roboguide.active-map-handoff-commit/v0.1",
            "barrier_id": barrier_id,
            "state": "committed",
            "target": target_selector,
            "software_stop_only": True,
        }

    def gc(self, request: dict[str, Any]) -> dict[str, Any]:
        """Evaluate actual local references before ContentReclamation may unlink local bytes."""
        if request.get("schema") == "roboguide.active-map-gc/v0.2":
            required = {"schema", "content_digest", "selector"}
            if set(request) != required:
                raise ActiveMapError("active-map GC v0.2 request is not closed")
            digest = _digest(request["content_digest"], "content_digest")
            selector = _text(request["selector"], "selector")
            blockers = self.reclamation_blockers(digest)
            with self._lock:
                refs = [
                    f"{kind}:{owner}:{reference}"
                    for reference, kind, owner in self.store._db.execute(
                        "SELECT reference_id,kind,owner FROM content_references "
                        "WHERE content_digest=? AND active=1 ORDER BY reference_id",
                        (digest,),
                    )
                ]
                blockers.extend(refs)
                if self._db.execute(
                    "SELECT 1 FROM heads WHERE selector=? AND state='active' "
                    "AND json_extract(context_json,'$.content_digest')=?",
                    (selector, digest),
                ).fetchone():
                    blockers.append("active_map:" + selector)
                tombstone = self.store._db.execute(
                    "SELECT state FROM content_tombstones WHERE content_digest=?", (digest,)
                ).fetchone()
            blockers = sorted(set(blockers))
            return {
                "schema": "roboguide.active-map-gc/v0.2",
                "state": "blocked" if blockers else "eligible",
                "digest": digest,
                "selector": selector,
                "reasons": blockers,
                "delete_authority": "ContentReclamation.collect" if not blockers else None,
                "tombstone_state": tombstone[0] if tombstone is not None else "absent",
            }
        required = {"schema", "content_digest", "selector", "refs"}
        if set(request) != required or request["schema"] != "roboguide.active-map-gc/v0.1":
            raise ActiveMapError("active-map GC request is not closed")
        digest = _digest(request["content_digest"], "content_digest")
        selector = _text(request["selector"], "selector")
        refs = request["refs"]
        kinds = {
            "execution",
            "import",
            "use_lease",
            "active_map",
            "lineage",
            "replica",
            "snapshot",
            "archive",
        }
        if (
            not isinstance(refs, dict)
            or set(refs) != kinds
            or any(value is not True for value in refs.values())
        ):
            return {
                "schema": "roboguide.active-map-gc/v0.1",
                "state": "blocked",
                "digest": digest,
                "selector": selector,
                "reasons": sorted(
                    kinds - {key for key, value in refs.items() if value is True}
                    if isinstance(refs, dict)
                    else kinds
                ),
            }
        return {
            "schema": "roboguide.active-map-gc/v0.1",
            "state": "blocked",
            "digest": digest,
            "selector": selector,
            "reasons": ["caller_booleans_are_not_reference_authority"],
        }

    def retire_content(self, digest: str) -> None:
        """Retire only local active-map heads after the owner has verified all original revokes."""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT selector,generation,state,context_json,proof_json,updated_at_ms FROM heads"
            ).fetchall()
            for row in rows:
                if json.loads(row[3])["content_digest"] == digest and row[2] == "active":
                    retired = (*row[:2], "revoked", *row[3:])
                    self._db.execute("INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?)", retired)
                    self._db.execute("UPDATE heads SET state='revoked' WHERE selector=?", (row[0],))
                    self._db.execute(
                        "UPDATE refs SET active=0 WHERE selector=? AND ref_kind='active_map'",
                        (row[0],),
                    )

    def reclamation_blockers(self, digest: str) -> list[str]:
        """Read actual local map/handoff references without accepting external all-clear flags."""
        with self._lock:
            blockers = [
                "active_map:" + row[0]
                for row in self._db.execute(
                    "SELECT ref_id FROM refs WHERE digest=? AND active=1 ORDER BY ref_id", (digest,)
                )
            ]
            for barrier, encoded in self._db.execute(
                "SELECT barrier_id,request_json FROM handoffs WHERE state='prepared' ORDER BY barrier_id"
            ):
                purpose = json.loads(encoded)
                if any(purpose[key]["content_digest"] == digest for key in ("source", "target")):
                    blockers.append("pending_handoff:" + barrier)
            return blockers
