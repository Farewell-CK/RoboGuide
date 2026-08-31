#!/usr/bin/env python3
"""Canonical local Robonix adapter for the RoboGuide Node Service.

The adapter is intentionally a small, dependency-free Local EAIOS boundary. It
does not own RoboGuide execution state, artifact catalog state, or mission
semantics. It accepts the fixed ``/v1/executions`` workflow used by the node
configuration, invokes the Robonix Mapping WebUI on loopback, and persists only
the local handle/status needed for restart and status reconciliation.

The four supported operations are ``build-map``, ``publish-map``, ``import-map``,
and ``verify-localization``. The central artifact service remains the authority
for immutable map identity, digest, revision lifecycle, and replica evidence.
``source_map_id`` and ``local_map_id`` are optional deployment/test parameters:
the former packages an already saved local map, while the latter separates a
local Robonix directory name from the canonical map identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, cast

LOG = logging.getLogger("roboguide.robonix_adapter")
MAP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REQUIRED_MAP_FILES = frozenset({"rtabmap.db", "occupancy.pgm", "occupancy.yaml", "meta.yaml"})
DEFAULT_MAP_ROOT = Path(
    "/home/nvidia/Desktop/robot-deeprobotics-lite3/rbnx-boot/cache/service-map-rbnx/maps"
)
DEFAULT_ROBONIX_ENDPOINT = "http://127.0.0.1:8092"
DEFAULT_ARTIFACT_ROOT = Path("/home/nvidia/roboguide/artifact-cache")
DEFAULT_STATE_DB = Path("/home/nvidia/roboguide/map-adapter.sqlite3")
DEFAULT_ROS_SERVICE_LIST_COMMAND = "docker exec robonix_mapping ros2 service list"
MAX_REQUEST_BYTES = 1 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
CHUNK_SIZE = 1024 * 1024
READINESS_CACHE_TTL_S = 1.0
MAPPING_MODE_SERVICE = "/rtabmap/set_mode_mapping"
LOCALIZATION_MODE_SERVICE = "/rtabmap/set_mode_localization"


class AdapterError(RuntimeError):
    """Reports a deterministic local adapter failure."""


@dataclass(frozen=True)
class AdapterConfig:
    """Fixed local paths and endpoint settings owned by one deployment."""

    map_root: Path
    artifact_root: Path
    state_db: Path
    robonix_endpoint: str
    request_timeout_s: float
    max_archive_bytes: int
    ros_service_list_command: tuple[str, ...]
    ros_discovery_timeout_s: float


class ExecutionStore:
    """Durable SQLite store for local execution handles and status facts."""

    def __init__(self, database: Path) -> None:
        """Create the parent directory and initialize the status table."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    operation TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    invocation_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS imported_maps (
                    local_map_id TEXT PRIMARY KEY,
                    artifact_digest TEXT NOT NULL,
                    imported_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one short-lived connection with foreign-key checks enabled."""
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def create_or_get(
        self,
        request_key: str,
        operation: str,
        artifact_path: Path,
        invocation: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Return an idempotent execution row and whether a worker is new."""
        now = time.time()
        invocation_json = json.dumps(invocation, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE request_key = ?", (request_key,)
            ).fetchone()
            if row is not None:
                return dict(row), False
            execution_id = f"rgx-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO executions(
                    execution_id, request_key, operation, artifact_path,
                    invocation_json, state, detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'ACCEPTED', ?, ?, ?)
                """,
                (
                    execution_id,
                    request_key,
                    operation,
                    str(artifact_path),
                    invocation_json,
                    "accepted by local Robonix adapter",
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                raise AdapterError("execution row disappeared after creation")
            return dict(row), True

    def get(self, execution_id: str) -> dict[str, Any] | None:
        """Read one execution row without changing its state."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def update(self, execution_id: str, state: str, detail: str) -> None:
        """Persist one non-secret local lifecycle fact."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE executions
                   SET state = ?, detail = ?, updated_at = ?
                 WHERE execution_id = ?
                   AND state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
                """,
                (state, detail[:2_000], time.time(), execution_id),
            )

    def request_cancel(self, execution_id: str) -> dict[str, Any] | None:
        """Mark a not-yet-terminal execution for cooperative cancellation."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                return None
            if row["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                connection.execute(
                    """
                    UPDATE executions
                       SET cancel_requested = 1, state = 'CANCELLED',
                           detail = 'cancellation requested before local completion',
                           updated_at = ?
                     WHERE execution_id = ?
                    """,
                    (time.time(), execution_id),
                )
            updated = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return dict(updated) if updated is not None else None

    def imported_digest(self, local_map_id: str) -> str | None:
        """Return the proven artifact digest for one adapter-owned local import."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_digest FROM imported_maps WHERE local_map_id = ?",
                (local_map_id,),
            ).fetchone()
            return str(row["artifact_digest"]) if row is not None else None

    def record_import(self, local_map_id: str, artifact_digest: str) -> None:
        """Persist immutable local import provenance or reject a conflicting identity."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_digest FROM imported_maps WHERE local_map_id = ?",
                (local_map_id,),
            ).fetchone()
            if row is not None:
                if str(row["artifact_digest"]) != artifact_digest:
                    raise AdapterError(
                        f"local map {local_map_id!r} already records a different artifact digest"
                    )
                return
            connection.execute(
                """
                INSERT INTO imported_maps(local_map_id, artifact_digest, imported_at)
                VALUES (?, ?, ?)
                """,
                (local_map_id, artifact_digest, time.time()),
            )


class RobonixClient:
    """Small JSON client for the loopback Robonix Mapping WebUI."""

    def __init__(self, endpoint: str, timeout_s: float) -> None:
        """Store the fixed WebUI endpoint and request timeout."""
        self._endpoint = endpoint.rstrip("/")
        self._timeout_s = timeout_s

    def get_json(self, path: str) -> dict[str, Any]:
        """GET one JSON endpoint and require a successful object response."""
        return self._request("GET", path, None)

    def post_json(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST one JSON body and require a successful object response."""
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None) -> dict[str, Any]:
        """Perform one bounded request without redirects or external routing."""
        if not path.startswith("/") or "?" in path or "#" in path:
            raise AdapterError("invalid fixed Robonix path")
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._endpoint}{path}", data=payload, headers=headers, method=method
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=self._timeout_s) as response:
                raw = response.read(MAX_REQUEST_BYTES + 1)
                if len(raw) > MAX_REQUEST_BYTES:
                    raise AdapterError("Robonix response exceeds local limit")
                status = response.status
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise AdapterError(f"Robonix request failed: {error}") from error
        if status < 200 or status >= 300:
            raise AdapterError(f"Robonix returned HTTP {status} for {path}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdapterError(f"Robonix returned invalid JSON for {path}") from error
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise AdapterError(f"Robonix returned a non-object response for {path}")
        return {cast(str, key): item for key, item in value.items()}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject server-selected redirect targets at the local adapter boundary."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Never follow a redirect returned by the Robonix WebUI."""
        raise AdapterError(f"Robonix returned redirect HTTP {code} for {request.full_url}")


class LocalAdapter:
    """Coordinates durable local handles and controlled Robonix operations."""

    def __init__(self, config: AdapterConfig) -> None:
        """Initialize paths, state, a fixed Robonix client, and workers."""
        self._config = config
        self._config.map_root.mkdir(parents=True, exist_ok=True)
        self._config.artifact_root.mkdir(parents=True, exist_ok=True)
        self._store = ExecutionStore(config.state_db)
        self._robonix = RobonixClient(config.robonix_endpoint, config.request_timeout_s)
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="robonix-map")
        self._futures: dict[str, Future[None]] = {}
        self._future_lock = threading.Lock()
        self._map_lock = threading.RLock()
        self._readiness_lock = threading.Lock()
        self._readiness_cache: tuple[float, set[str], str] | None = None

    def close(self) -> None:
        """Stop accepting local work and wait for already submitted operations to finish."""
        self._workers.shutdown(wait=True, cancel_futures=False)

    def health(self) -> dict[str, Any]:
        """Report the local Robonix health used by Node Service heartbeat."""
        try:
            state = self._robonix.get_json("/api/state")
            mode = state.get("mode", "unknown")
            return {"state": "ONLINE", "detail": f"Robonix mapping reachable; mode={mode}"}
        except AdapterError as error:
            return {"state": "OFFLINE", "detail": str(error)}

    def readiness(self) -> dict[str, Any]:
        """Observe exact local capabilities without changing Robonix execution state."""
        services, discovery_detail = self._ros_discovery_snapshot()
        mapping_service = self._service_readiness(services, MAPPING_MODE_SERVICE, discovery_detail)
        localization_service = self._service_readiness(
            services, LOCALIZATION_MODE_SERVICE, discovery_detail
        )
        map_storage = self._storage_readiness("map", self._config.map_root)
        artifact_storage = self._storage_readiness("artifact", self._config.artifact_root)
        return {
            "capabilities": {
                "spatial.map.build@v0": self._combined_readiness(
                    mapping_service, map_storage, artifact_storage
                ),
                "spatial.map.publish@v0": self._combined_readiness(artifact_storage),
                "spatial.map.import@v0": self._combined_readiness(artifact_storage, map_storage),
                "spatial.localization.verify@v0": self._combined_readiness(
                    localization_service, map_storage
                ),
            }
        }

    def submit(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Validate one canonical operation and enqueue it exactly once."""
        operation = body.get("operation")
        invocation = body.get("invocation", {})
        artifact_path_value = body.get("artifact_path", "")
        if operation not in {
            "build-map",
            "publish-map",
            "import-map",
            "verify-localization",
        }:
            raise AdapterError(
                "operation must be build-map/publish-map/import-map/verify-localization"
            )
        if not isinstance(invocation, dict):
            raise AdapterError("invocation must be a JSON object")
        if not isinstance(artifact_path_value, str) or not artifact_path_value:
            raise AdapterError("artifact_path must be a non-empty string")
        artifact_path = self._controlled_artifact_path(artifact_path_value)
        self._validate_invocation(invocation)
        request_key = self._request_key(operation, invocation, artifact_path)
        row, is_new = self._store.create_or_get(request_key, operation, artifact_path, invocation)
        if is_new:
            execution_id = str(row["execution_id"])
            with self._future_lock:
                self._futures[execution_id] = self._workers.submit(
                    self._run, execution_id, operation, artifact_path, invocation
                )
        return {"execution_id": row["execution_id"], "state": row["state"]}

    def status(self, execution_id: str) -> dict[str, Any]:
        """Return the durable local state for one execution handle."""
        row = self._store.get(execution_id)
        if row is None:
            raise KeyError(execution_id)
        return {"state": row["state"], "detail": row["detail"]}

    def cancel(self, execution_id: str) -> dict[str, Any]:
        """Record a cooperative cancellation request without fabricating success."""
        row = self._store.request_cancel(execution_id)
        if row is None:
            raise KeyError(execution_id)
        return {"state": row["state"], "detail": row["detail"]}

    def _run(
        self,
        execution_id: str,
        operation: str,
        artifact_path: Path,
        invocation: Mapping[str, Any],
    ) -> None:
        """Run one operation and reduce its result to a durable terminal fact."""
        self._store.update(execution_id, "RUNNING", f"running {operation}")
        try:
            if self._is_cancelled(execution_id):
                return
            if operation == "build-map":
                detail = self._build_map(artifact_path, invocation)
            elif operation == "publish-map":
                detail = self._validate_bundle(artifact_path)
            elif operation == "import-map":
                detail = self._import_map(artifact_path, invocation)
            else:
                detail = self._verify_localization(invocation)
            self._store.update(execution_id, "COMPLETED", detail)
            LOG.info("execution=%s operation=%s completed", execution_id, operation)
        except AdapterError as error:
            self._store.update(execution_id, "FAILED", str(error))
            LOG.warning("execution=%s operation=%s failed: %s", execution_id, operation, error)
        except Exception as error:  # pragma: no cover - defensive process boundary
            self._store.update(execution_id, "FAILED", f"unexpected adapter failure: {error}")
            LOG.exception("execution=%s operation=%s failed unexpectedly", execution_id, operation)

    def _build_map(self, artifact_path: Path, invocation: Mapping[str, Any]) -> str:
        """Save a live map or package an explicitly selected existing map."""
        parameters = self._parameters(invocation)
        map_id = self._map_id(parameters, "map_id")
        source_map_id = parameters.get("source_map_id")
        if source_map_id is None:
            local_map_id = self._map_id(parameters, "local_map_id", default=map_id)
            response = self._robonix.post_json("/api/save", {"map_id": local_map_id})
            if response.get("ok") is not True:
                raise AdapterError(str(response.get("detail", "Robonix save failed")))
            source_map_id = local_map_id
        else:
            source_map_id = self._checked_map_id(source_map_id, "source_map_id")
        source_dir = self._map_directory(source_map_id)
        self._validate_map_directory(source_dir)
        self._pack_map(source_dir, artifact_path)
        size = artifact_path.stat().st_size
        return f"prepared map bundle from local map {source_map_id} ({size} bytes)"

    def _validate_bundle(self, artifact_path: Path) -> str:
        """Validate a prepared archive without mutating the Robonix map library."""
        members = self._archive_members(artifact_path)
        missing = sorted(REQUIRED_MAP_FILES - members)
        if missing:
            raise AdapterError(f"map bundle is missing required files: {', '.join(missing)}")
        return f"validated map bundle ({artifact_path.stat().st_size} bytes)"

    def _import_map(self, artifact_path: Path, invocation: Mapping[str, Any]) -> str:
        """Import one immutable artifact or prove an existing import is identical."""
        parameters = self._parameters(invocation)
        target_id = self._map_id(
            parameters, "local_map_id", default=self._map_id(parameters, "map_id")
        )
        target = self._map_directory(target_id)
        artifact_digest = self._sha256_file(artifact_path)
        with self._map_lock:
            if target.exists():
                recorded_digest = self._store.imported_digest(target_id)
                if recorded_digest != artifact_digest:
                    detail = "has no adapter provenance"
                    if recorded_digest is not None:
                        detail = "records a different artifact digest"
                    raise AdapterError(
                        f"local map {target_id!r} already exists and {detail}; "
                        "immutable import refused"
                    )
                self._validate_map_directory(target)
                return f"local map {target_id} already contains artifact {artifact_digest}"
            members = self._archive_members(artifact_path)
            missing = sorted(REQUIRED_MAP_FILES - members)
            if missing:
                raise AdapterError(f"map bundle is missing required files: {', '.join(missing)}")
            staging = self._config.map_root / f".{target_id}.staging-{uuid.uuid4().hex}"
            try:
                staging.mkdir()
                self._extract_archive(artifact_path, staging)
                self._validate_map_directory(staging)
                os.replace(staging, target)
                self._store.record_import(target_id, artifact_digest)
            finally:
                if staging.exists():
                    self._remove_tree(staging)
        return f"imported map bundle as local map {target_id}"

    def _verify_localization(self, invocation: Mapping[str, Any]) -> str:
        """Load the selected local map through Robonix and verify its state."""
        parameters = self._parameters(invocation)
        target_id = self._map_id(
            parameters, "local_map_id", default=self._map_id(parameters, "map_id")
        )
        target = self._map_directory(target_id)
        self._validate_map_directory(target)
        response = self._robonix.post_json(
            "/api/load", {"map_id": target_id, "mode": "localization"}
        )
        if response.get("ok") is not True:
            raise AdapterError(str(response.get("detail", "Robonix localization load failed")))
        state = self._robonix.get_json("/api/state")
        if state.get("has_map") is not True:
            raise AdapterError("Robonix loaded the map but reports no active map")
        return f"localization verified for local map {target_id}"

    def _ros_services(self) -> set[str]:
        """Return exact services visible through the deployment's fixed ROS discovery command."""
        if not self._config.ros_service_list_command:
            raise AdapterError("ROS service discovery command is not configured")
        try:
            result = subprocess.run(
                self._config.ros_service_list_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._config.ros_discovery_timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdapterError(f"ROS service discovery failed: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise AdapterError(
                f"ROS service discovery exited with {result.returncode}: {detail[:500]}"
            )
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _ros_discovery_snapshot(self) -> tuple[set[str], str]:
        """Share one bounded discovery result across a burst of exact-contract probes."""
        with self._readiness_lock:
            now = time.monotonic()
            if self._readiness_cache is not None:
                observed_at, services, detail = self._readiness_cache
                if now - observed_at <= READINESS_CACHE_TTL_S:
                    return set(services), detail
            try:
                services = self._ros_services()
                detail = f"discovered {len(services)} ROS services"
            except AdapterError as error:
                services = set()
                detail = str(error)
            self._readiness_cache = (time.monotonic(), services, detail)
            return set(services), detail

    @staticmethod
    def _storage_readiness(kind: str, path: Path) -> tuple[bool, str]:
        """Check one adapter-owned root without creating or changing probe artifacts."""
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return False, f"adapter {kind} storage is not accessible: {path}"
        return True, f"adapter {kind} storage is accessible"

    @staticmethod
    def _readiness_fact(ready: bool, detail: str) -> dict[str, str]:
        """Map one observed deployment fact into the fixed adapter readiness vocabulary."""
        return {"state": "READY" if ready else "UNAVAILABLE", "detail": detail}

    @staticmethod
    def _service_readiness(
        services: set[str], required: str, discovery_detail: str
    ) -> tuple[bool, str]:
        """Require one exact ROS service discovered through the configured middleware context."""
        if required in services:
            return True, f"discovered required ROS service {required}"
        return False, f"required ROS service {required} is unavailable; {discovery_detail}"

    @classmethod
    def _combined_readiness(cls, *facts: tuple[bool, str]) -> dict[str, str]:
        """Require every local dependency fact and retain their diagnostics in order."""
        return cls._readiness_fact(
            all(ready for ready, _detail in facts),
            "; ".join(detail for _ready, detail in facts),
        )

    def _pack_map(self, source_dir: Path, destination: Path) -> None:
        """Create a gzip tar archive from regular files using an atomic rename."""
        if destination.exists():
            raise AdapterError(f"artifact destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
        try:
            with tarfile.open(temporary, mode="w:gz") as archive:
                for path in sorted(source_dir.rglob("*")):
                    relative = path.relative_to(source_dir)
                    if path.is_symlink():
                        raise AdapterError(f"map contains unsupported symlink: {relative}")
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        raise AdapterError(f"map contains unsupported entry: {relative}")
                    archive.add(path, arcname=PurePosixPath(relative.as_posix()))
            self._check_size(temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _archive_members(self, archive_path: Path) -> set[str]:
        """Read and validate archive member names, types, and declared sizes."""
        self._check_size(archive_path)
        names: set[str] = set()
        total_size = 0
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                members = archive.getmembers()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise AdapterError("map bundle contains too many members")
                for member in members:
                    relative = self._safe_member_path(member.name)
                    if relative == ".":
                        continue
                    if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                        raise AdapterError(f"map bundle contains unsupported entry: {member.name}")
                    if member.isfile():
                        total_size += member.size
                        if total_size > self._config.max_archive_bytes:
                            raise AdapterError("map bundle expands beyond local size limit")
                        names.add(relative)
        except (tarfile.TarError, OSError) as error:
            raise AdapterError(f"invalid map bundle: {error}") from error
        return names

    def _extract_archive(self, archive_path: Path, staging: Path) -> None:
        """Extract validated regular files while bounding bytes and path depth."""
        total_size = 0
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                for member in archive.getmembers():
                    relative = self._safe_member_path(member.name)
                    if relative == ".":
                        continue
                    target = staging.joinpath(*PurePosixPath(relative).parts)
                    if not self._within(staging, target):
                        raise AdapterError(f"archive member escapes staging root: {member.name}")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise AdapterError(f"archive member is not a regular file: {member.name}")
                    total_size += member.size
                    if total_size > self._config.max_archive_bytes:
                        raise AdapterError("map bundle expands beyond local size limit")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise AdapterError(f"archive member cannot be read: {member.name}")
                    with source, target.open("xb") as output:
                        while True:
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            output.write(chunk)
                    os.chmod(target, member.mode & 0o777)
        except (tarfile.TarError, OSError) as error:
            raise AdapterError(f"map bundle extraction failed: {error}") from error

    def _validate_map_directory(self, directory: Path) -> None:
        """Require a regular, readable Robonix map directory and SQLite database."""
        if not directory.is_dir() or directory.is_symlink():
            raise AdapterError(f"local map directory does not exist: {directory.name}")
        for name in REQUIRED_MAP_FILES:
            path = directory / name
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"local map is missing required file: {name}")
        database = directory / "rtabmap.db"
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10.0) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as error:
            raise AdapterError(f"rtabmap database integrity check failed: {error}") from error
        if not result or str(result[0]).lower() != "ok":
            raise AdapterError(f"rtabmap database integrity check failed: {result}")

    def _controlled_artifact_path(self, value: str) -> Path:
        """Resolve one Node-owned artifact path and enforce the cache root."""
        candidate = Path(value)
        if not candidate.is_absolute():
            raise AdapterError("artifact_path must be absolute")
        resolved = candidate.resolve(strict=False)
        if not self._within(self._config.artifact_root, resolved):
            raise AdapterError("artifact_path is outside the deployment artifact root")
        if candidate.exists() and candidate.is_symlink():
            raise AdapterError("artifact_path must not be a symlink")
        return resolved

    def _map_directory(self, map_id: str) -> Path:
        """Return a path-safe local Robonix map directory."""
        directory = (self._config.map_root / map_id).resolve(strict=False)
        if not self._within(self._config.map_root, directory):
            raise AdapterError("map identifier escapes the Robonix map root")
        return directory

    def _parameters(self, invocation: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return canonical scalar parameters and reject malformed invocation data."""
        parameters = invocation.get("parameters", {})
        if not isinstance(parameters, dict):
            raise AdapterError("invocation.parameters must be an object")
        return cast(Mapping[str, Any], parameters)

    def _map_id(self, parameters: Mapping[str, Any], key: str, default: str | None = None) -> str:
        """Read one required or defaulted path-safe map identifier."""
        value = parameters.get(key, default)
        return self._checked_map_id(value, key)

    @staticmethod
    def _checked_map_id(value: Any, field: str) -> str:
        """Validate one map identifier without sanitizing or changing its identity."""
        if not isinstance(value, str) or not MAP_ID_PATTERN.fullmatch(value):
            raise AdapterError(f"{field} must match {MAP_ID_PATTERN.pattern}")
        return value

    @staticmethod
    def _validate_invocation(invocation: Mapping[str, Any]) -> None:
        """Require the identity fields needed for a traceable local execution."""
        for field in ("mission_id", "task_id", "group_id", "role_id", "capability_contract"):
            value = invocation.get(field)
            if not isinstance(value, str) or not value.strip():
                raise AdapterError(f"invocation.{field} must be a non-empty string")

    @staticmethod
    def _request_key(operation: str, invocation: Mapping[str, Any], artifact_path: Path) -> str:
        """Compute a stable idempotency key for an immutable workflow request."""
        payload = json.dumps(
            {"operation": operation, "invocation": invocation, "artifact_path": str(artifact_path)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _is_cancelled(self, execution_id: str) -> bool:
        """Check the durable cancellation fence before invoking Robonix."""
        row = self._store.get(execution_id)
        return row is not None and bool(row["cancel_requested"])

    def _check_size(self, path: Path) -> None:
        """Reject missing, non-regular, oversized, or symlinked archive paths."""
        if not path.is_file() or path.is_symlink():
            raise AdapterError(f"artifact is not a regular file: {path}")
        if path.stat().st_size > self._config.max_archive_bytes:
            raise AdapterError("artifact exceeds local size limit")

    def _sha256_file(self, path: Path) -> str:
        """Return the lowercase SHA-256 identity of one bounded regular artifact file."""
        self._check_size(path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _safe_member_path(name: str) -> str:
        """Normalize one tar path and reject absolute or parent-traversing names."""
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise AdapterError(f"archive member has unsafe path: {name}")
        parts = [part for part in path.parts if part not in ("", ".")]
        return "/".join(parts) or "."

    @staticmethod
    def _within(root: Path, candidate: Path) -> bool:
        """Return whether a resolved candidate stays below a resolved root."""
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            return False
        return True

    @staticmethod
    def _remove_tree(path: Path) -> None:
        """Remove only a service-owned staging directory after a failed import."""
        if path.is_dir() and not path.is_symlink():
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    LocalAdapter._remove_tree(child)
                else:
                    child.unlink(missing_ok=True)
            path.rmdir()


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP surface matching the Node Service's fixed local workflow."""

    server: AdapterHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        """Route access logs through the adapter logger without request bodies."""
        LOG.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        """Handle loopback-only health and exact-capability readiness observations."""
        if self.path == "/v1/health":
            self._send_json(HTTPStatus.OK, self.server.adapter.health())
        elif self.path == "/v1/readiness":
            self._send_json(HTTPStatus.OK, self.server.adapter.readiness())
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:
        """Handle execute, status, and cancellation workflow calls."""
        try:
            body = self._read_json()
            if self.path == "/v1/executions":
                response = self.server.adapter.submit(body)
                self._send_json(HTTPStatus.OK, response)
            elif self.path == "/v1/executions/status":
                execution_id = self._required_execution_id(body)
                try:
                    response = self.server.adapter.status(execution_id)
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"detail": "execution not found"})
                else:
                    self._send_json(HTTPStatus.OK, response)
            elif self.path == "/v1/executions/cancel":
                execution_id = self._required_execution_id(body)
                try:
                    response = self.server.adapter.cancel(execution_id)
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"detail": "execution not found"})
                else:
                    self._send_json(HTTPStatus.OK, response)
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
        except AdapterError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": f"invalid JSON: {error}"})
        except Exception as error:  # pragma: no cover - defensive process boundary
            LOG.exception("request failed")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})

    def _read_json(self) -> dict[str, Any]:
        """Read one bounded JSON object from the request body."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise AdapterError("Content-Length is invalid") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise AdapterError("request body is empty or exceeds local limit")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise AdapterError("request body must be a JSON object")
        return {cast(str, key): item for key, item in value.items()}

    @staticmethod
    def _required_execution_id(body: Mapping[str, Any]) -> str:
        """Read a nonblank local execution handle from a workflow request."""
        value = body.get("execution_id")
        if not isinstance(value, str) or not value.strip():
            raise AdapterError("execution_id must be a non-empty string")
        return value

    def _send_json(self, status: HTTPStatus, value: Mapping[str, Any]) -> None:
        """Write one compact JSON response with an explicit content length."""
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


class AdapterHTTPServer(ThreadingHTTPServer):
    """Threaded server carrying one immutable adapter instance."""

    def __init__(self, address: tuple[str, int], adapter: LocalAdapter) -> None:
        """Bind the configured loopback listener and expose its adapter."""
        super().__init__(address, RequestHandler)
        self.adapter = adapter


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse deployment-owned adapter paths without reading remote invocation data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18101)
    parser.add_argument("--map-root", type=Path, default=DEFAULT_MAP_ROOT)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB)
    parser.add_argument("--robonix-endpoint", default=DEFAULT_ROBONIX_ENDPOINT)
    parser.add_argument("--request-timeout-s", type=float, default=30.0)
    parser.add_argument(
        "--ros-service-list-command",
        default=DEFAULT_ROS_SERVICE_LIST_COMMAND,
        help="fixed argv string used only for read-only ROS service discovery",
    )
    parser.add_argument("--ros-discovery-timeout-s", type=float, default=5.0)
    parser.add_argument("--max-archive-bytes", type=int, default=4 * 1024 * 1024 * 1024)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Start the local adapter and serve until the process receives shutdown."""
    args = parse_args(argv)
    if not 1 <= args.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    if (
        args.request_timeout_s <= 0
        or args.ros_discovery_timeout_s <= 0
        or args.max_archive_bytes <= 0
    ):
        raise SystemExit("timeouts and archive limits must be positive")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = AdapterConfig(
        map_root=args.map_root.resolve(),
        artifact_root=args.artifact_root.resolve(),
        state_db=args.state_db.resolve(),
        robonix_endpoint=args.robonix_endpoint,
        request_timeout_s=args.request_timeout_s,
        max_archive_bytes=args.max_archive_bytes,
        ros_service_list_command=tuple(shlex.split(args.ros_service_list_command)),
        ros_discovery_timeout_s=args.ros_discovery_timeout_s,
    )
    adapter = LocalAdapter(config)
    server = AdapterHTTPServer((args.host, args.port), adapter)
    LOG.info("listening on %s:%s for local Robonix workflow", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutdown requested")
    finally:
        server.shutdown()
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
