"""Freeze operation-owned reported origins and offer explicit generic Memory exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from robonix_task.journal import Conflict, canonical
from robonix_task.position import MAX_MAPPING_AGE_MS, _number, annotate_mapping_age

ORIGIN_SCHEMA = "roboguide.reported-origin/v0.1"
PROVIDER_ID = "reported-origins"
OWNER_ID = "robot-observer"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def identifier(value: object) -> str:
    """Require a bounded path-safe external identity before any filesystem access."""
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("bounded path-safe identity required")
    return value


class OriginStore:
    """Keep immutable local observation receipts under one process and thread owner."""

    def __init__(self, root: Path, node_id: str, *, max_records: int = 128) -> None:
        """Lock a private root and refuse another deployment or unsupported store generation."""
        self.node_id = identifier(node_id)
        if max_records <= 0:
            raise ValueError("positive origin bound required")
        self.max_records = max_records
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.owner = (self.root / "owner.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                self.owner.seek(0)
                if not self.owner.read(1):
                    self.owner.write(b"0")
                    self.owner.flush()
                self.owner.seek(0)
                try:
                    msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise Conflict("another process owns the origin store") from error
            else:
                import fcntl

                try:
                    fcntl.flock(self.owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise Conflict("another process owns the origin store") from error
            profile = {"schema": "roboguide.origin-store/v0.1", "node_id": node_id}
            profile_path = self.root / "profile.json"
            if profile_path.exists():
                if self._read(profile_path) != profile:
                    raise Conflict("origin store generation or deployment differs")
            else:
                if list(self.root.glob("*.json")):
                    raise Conflict("unversioned origin records require operator inspection")
                self._publish(profile_path, canonical(profile).encode())
            for record in self._records():
                self._verify(record)
        except BaseException:
            self.owner.close()
            raise

    def close(self) -> None:
        """Release the OS owner only after the combined server has stopped its handlers."""
        self.owner.close()

    def _read(self, path: Path) -> dict[str, Any]:
        """Read bounded finite JSON without following a substituted final file symlink."""
        if path.is_symlink() or path.stat().st_size > 131072:
            raise Conflict("origin file is substituted or exceeds its bound")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise Conflict("origin file must be an object")
        canonical(value)
        return value

    def _publish(self, path: Path, raw: bytes) -> None:
        """Fsync complete bytes and atomically publish a nonoverwriting immutable name."""
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".origin-", delete=False) as out:
                temporary = out.name
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.link(temporary, path)
            if sys.platform != "win32":
                descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _records(self) -> list[dict[str, Any]]:
        """Enumerate only this provider's bounded immutable receipts."""
        paths = sorted(self.root.glob("origin-*.json"))
        if len(paths) > self.max_records:
            raise Conflict("origin record bound exceeded")
        return [self._read(path) for path in paths]

    def _verify(self, record: dict[str, Any]) -> None:
        """Rehash original content before returning a receipt or offering publication."""
        if set(record) != {"schema", "request", "manifest", "content"}:
            raise Conflict("unsupported origin receipt fields")
        if record["schema"] != "roboguide.origin-receipt/v0.1":
            raise Conflict("unsupported origin receipt generation")
        raw = canonical(record["content"]).encode()
        manifest = record["manifest"]
        content = record["content"]
        request = record["request"]
        invocation = request["invocation"]
        intent = invocation.get("intent", invocation)
        params = intent["parameters"]
        if (
            content.get("schema_version") != ORIGIN_SCHEMA
            or content.get("node_id") != self.node_id
            or content.get("motion_authorized") is not False
            or content.get("localization_verified") is not False
            or content.get("source_execution_id") != request["attempt_id"]
            or content.get("attempt_id") != request["attempt_id"]
            or manifest["source_execution_id"] != request["attempt_id"]
            or manifest["source_mission_id"] != invocation["mission_id"]
            or content.get("source_mission_id") != invocation["mission_id"]
            or content.get("source_task_id") != invocation["task_id"]
            or manifest["source_task_ref"]
            != {"mission_id": invocation["mission_id"], "task_id": invocation["task_id"]}
            or manifest["selector"] != {"memory_id": params["memory_id"], "revision_id": "r1"}
            or content.get("memory_id") != params["memory_id"]
            or content["position"]["map_id"] != params["expected_map_id"]
            or manifest["scope"] != {"kind": params["scope"]}
            or manifest["schema"] != "roboguide.memory-manifest/v0.1"
            or manifest["payload_schema"] != ORIGIN_SCHEMA
            or manifest["provider_id"] != PROVIDER_ID
            or manifest["kind"] != "experience"
            or manifest["media_type"] != "application/json"
            or manifest["visibility"] != "exchangeable"
        ):
            raise Conflict("origin receipt source or authority was changed")
        if manifest["artifact"] != {
            "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
        }:
            raise Conflict("origin content differs from its immutable manifest")
        blob = self.root / (hashlib.sha256(raw).hexdigest() + ".blob")
        if blob.is_symlink() or blob.read_bytes() != raw:
            raise Conflict("origin artifact bytes are unavailable or changed")
        if manifest["owner"] != {
            "owner": "node",
            "node_id": self.node_id,
            "local_system_id": OWNER_ID,
        }:
            raise Conflict("origin owner differs from its deployment")

    def capture(self, body: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """Freeze one normal Node invocation without accepting caller-supplied coordinates."""
        if set(body) != {"attempt_id", "invocation"}:
            raise ValueError("only original attempt and invocation are allowed")
        canonical(body)
        attempt = body["attempt_id"]
        if not isinstance(attempt, str) or not 1 <= len(attempt) <= 1024 or not attempt.strip():
            raise ValueError("bounded original Node execution identity required")
        invocation = body["invocation"]
        if not isinstance(invocation, dict):
            raise ValueError("canonical invocation required")
        intent = invocation.get("intent", invocation)
        operation = intent.get("operation") if isinstance(intent, dict) else None
        if operation != "spatial.origin.capture@v1" and operation != {
            "namespace": "spatial.origin",
            "name": "capture",
            "version": "v1",
        }:
            raise ValueError("origin capture operation required")
        execution = attempt
        handle = hashlib.sha256(attempt.encode()).hexdigest()
        if "execution_id" in invocation and invocation["execution_id"] != attempt:
            raise ValueError("original Node attempt and execution must match")
        mission = identifier(invocation.get("mission_id"))
        task = identifier(invocation.get("task_id"))
        params = intent.get("parameters")
        if not isinstance(params, dict) or set(params) != {"memory_id", "expected_map_id", "scope"}:
            raise ValueError("explicit memory_id, expected_map_id and scope required")
        memory_id = identifier(params["memory_id"])
        map_id = identifier(params["expected_map_id"])
        if params["scope"] not in {"global", "local"}:
            raise ValueError("only explicitly selected global or local scope is supported")
        # The Node has already validated execution/attempt identity and allocation.
        # This local endpoint is reachable only by the deployment-fixed loopback workflow.
        with self.lock:
            path = self.root / ("origin-" + handle + ".json")
            if path.exists():
                old = self._read(path)
                self._verify(old)
                if old["request"] != body:
                    raise Conflict("origin execution input changed")
                return self.status(handle)
            records = self._records()
            if len(records) >= self.max_records or any(
                row["manifest"]["selector"]["memory_id"] == memory_id for row in records
            ):
                raise Conflict("origin identity is already frozen or record bound reached")
            now = time.time_ns()
            mapping = annotate_mapping_age(snapshot, now)["mapping"]
            value = mapping.get("value")
            if (
                mapping["availability"] != "current"
                or mapping["age_ms"] > MAX_MAPPING_AGE_MS
                or not isinstance(value, dict)
                or value.get("has_map") is not True
                or value.get("map_id") != map_id
                or value.get("mode") != "localization"
            ):
                raise ValueError("current matching-map Mapping response is unavailable")
            pose = value.get("pose")
            if not isinstance(pose, dict) or not all(
                _number(pose.get(k)) for k in ("x", "y", "theta")
            ):
                raise ValueError("finite reported map pose required")
            content = {
                "schema_version": ORIGIN_SCHEMA,
                "node_id": self.node_id,
                "memory_id": memory_id,
                "captured_at_ns": now,
                "attempt_id": attempt,
                "source_mission_id": mission,
                "source_task_id": task,
                "source_execution_id": execution,
                "position": {
                    "map_id": map_id,
                    "x_m": pose["x"],
                    "y_m": pose["y"],
                    "yaw_rad": pose["theta"],
                    "map_revision": None,
                    "frame_basis": "local Mapping map; shared physical frame unverified",
                },
                "mapping_observation": mapping,
                "pose_measurement_time": None,
                "localization_verified": False,
                "motion_authorized": False,
            }
            raw = canonical(content).encode()
            if len(raw) > 65536:
                raise ValueError("origin content exceeds its admitted decoder bound")
            digest = hashlib.sha256(raw).hexdigest()
            manifest = {
                "schema": "roboguide.memory-manifest/v0.1",
                "selector": {"memory_id": memory_id, "revision_id": "r1"},
                "kind": "experience",
                "provider_id": PROVIDER_ID,
                "owner": {"owner": "node", "node_id": self.node_id, "local_system_id": OWNER_ID},
                "scope": {"kind": params["scope"]},
                "visibility": "exchangeable",
                "payload_schema": ORIGIN_SCHEMA,
                "media_type": "application/json",
                "artifact": {"content_digest": "sha256:" + digest, "byte_size": len(raw)},
                "source_mission_id": mission,
                "source_execution_id": execution,
                "source_task_ref": {"mission_id": mission, "task_id": task},
                "created_at": now // 1_000_000,
            }
            blob = self.root / (digest + ".blob")
            if not blob.exists():
                self._publish(blob, raw)
            receipt = {
                "schema": "roboguide.origin-receipt/v0.1",
                "request": body,
                "manifest": manifest,
                "content": content,
            }
            self._verify(receipt)
            receipt_raw = canonical(receipt).encode()
            if len(receipt_raw) > 131072:
                raise ValueError("origin receipt exceeds its immutable reader bound")
            self._publish(path, receipt_raw)
            return self.status(handle)

    def status(self, execution: str) -> dict[str, Any]:
        """Return immutable local completion without claiming central publication success."""
        with self.lock:
            row = self._read(self.root / ("origin-" + identifier(execution) + ".json"))
            self._verify(row)
            return {
                "handle": execution,
                "state": "SUCCEEDED",
                "detail": "origin frozen; catalog publication is a separate Node operation",
                "manifest": row["manifest"],
            }

    def discover(self) -> dict[str, Any]:
        """Offer only records whose original operation explicitly selected global sharing."""
        with self.lock:
            rows = self._records()
            for row in rows:
                self._verify(row)
            return {
                "manifests": [
                    r["manifest"] for r in rows if r["manifest"]["scope"] == {"kind": "global"}
                ]
            }

    def export(self, manifest: dict[str, Any]) -> dict[str, str]:
        """Select exact original metadata and recheck content before Node Artifact upload."""
        with self.lock:
            for row in self._records():
                if row["manifest"] == manifest and manifest["scope"] == {"kind": "global"}:
                    self._verify(row)
                    return {
                        "path": manifest["artifact"]["content_digest"].removeprefix("sha256:")
                        + ".blob"
                    }
        raise ValueError("manifest is not an exact publish-eligible origin")
