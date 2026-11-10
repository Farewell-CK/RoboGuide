"""Publish only one explicitly selected capture as immutable Global photo Memory."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Protocol

from robonix_task.image_inspection import inspect_image_bytes
from robonix_task.journal import Conflict, canonical
from robonix_task.origin import identifier
from robonix_task.photo_handoff import _DIGEST, _no_links, _publish, _read_owned


class CaptureSource(Protocol):
    """Read the existing capture journal under its original owner's lock."""

    root: Path
    db: sqlite3.Connection
    lock: Any


def _json(path: Path) -> dict[str, Any]:
    """Read bounded owned JSON and reject malformed or substituted record files."""
    raw = _read_owned(path)
    if len(raw) > 131072:
        raise Conflict("photo_publication_metadata_exceeds_bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Conflict("photo_publication_object_required")
    canonical(value)
    return value


def _immutable(path: Path, raw: bytes) -> None:
    """Fsync complete bytes then publish a nonoverwriting fixed owner-selected record."""
    _no_links(path.parent)
    if path.exists() or path.is_symlink():
        if _read_owned(path) != raw:
            raise Conflict("photo_publication_immutable_record_conflict")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".photo-record-", delete=False
        ) as out:
            temporary = Path(out.name)
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path)
        if sys.platform != "win32":
            descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PhotoCaptureMemory:
    """Retain one exact capture's original identity and content without rescanning other photos."""

    def __init__(
        self,
        root: str | Path,
        node_id: str,
        memory_id: str,
        task_id: str,
        role_id: str,
    ) -> None:
        """Freeze publication profile and acquire one OS owner before offering any manifest."""
        self.root = Path(root).absolute()
        _no_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _no_links(self.root)
        self.profile = {
            "schema": "roboguide.photo-capture-memory-store/v0.1",
            "node_id": identifier(node_id),
            "memory_id": identifier(memory_id),
            "task_id": identifier(task_id),
            "role_id": identifier(role_id),
        }
        self.lock = threading.RLock()
        owner_path = self.root / "owner.lock"
        _no_links(owner_path)
        self.owner = owner_path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                self.owner.seek(0)
                if not self.owner.read(1):
                    self.owner.write(b"0")
                    self.owner.flush()
                self.owner.seek(0)
                msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            profile_path = self.root / "profile.json"
            if profile_path.exists() or profile_path.is_symlink():
                if _json(profile_path) != self.profile:
                    raise Conflict("photo_publication_profile_changed")
            else:
                if list(self.root.glob("*.json")) or list(self.root.glob("*.png")):
                    raise Conflict("unversioned_photo_publication_requires_inspection")
                _immutable(profile_path, canonical(self.profile).encode())
            self.record_path = self.root / "publication.json"
            self.record: dict[str, Any] | None = None
            if self.record_path.exists() or self.record_path.is_symlink():
                self.record = _json(self.record_path)
                self._verify(self.record)
        except BaseException:
            self.owner.close()
            raise

    def close(self) -> None:
        """Release the publication owner after the combined Connector has drained handlers."""
        self.owner.close()

    def _manifest(self, record: dict[str, Any], raw: bytes) -> dict[str, Any]:
        """Validate original capture identity and completed central publication before discovery."""
        capture_id = str(uuid.UUID(record["capture_id"]))
        if capture_id != record["capture_id"]:
            raise ValueError("canonical_capture_identity_required")
        request, detail = record["request"], record["detail"]
        if not isinstance(request, dict) or not isinstance(detail, dict):
            raise ValueError("original_capture_evidence_required")
        invocation = request.get("invocation")
        if not isinstance(invocation, dict) or (
            request.get("attempt_id") != record["attempt_id"]
            or not isinstance(record["attempt_id"], str)
            or not record["attempt_id"]
            or len(record["attempt_id"]) > 4096
            or invocation.get("operation") != "lite3.capture-rgb@v1"
            or invocation.get("task_id") != self.profile["task_id"]
            or invocation.get("role_id") != self.profile["role_id"]
            or invocation.get("parameters") != {"photo_memory_id": self.profile["memory_id"]}
            or not isinstance(invocation.get("mission_id"), str)
            or not invocation["mission_id"]
        ):
            raise ValueError("capture_not_exact_photo_publication_task")
        artifact = detail.get("artifact")
        if not isinstance(artifact, dict):
            raise ValueError("completed_capture_artifact_required")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        proof = inspect_image_bytes(raw, digest)
        receipt = artifact.get("artifact_receipt")
        received = detail.get("received_wall_ns")
        if (
            proof["format"] != "PNG"
            or detail.get("sha256") != digest[7:]
            or detail.get("byte_size") != len(raw)
            or detail.get("media_type") != "image/png"
            or detail.get("artifact_publication") != "finalized"
            or artifact.get("artifact_finalized") is not True
            or artifact.get("artifact_content_digest") != digest
            or artifact.get("artifact_byte_size") != len(raw)
            or not isinstance(receipt, dict)
            or receipt.get("phase") != "completed"
            or receipt.get("content_digest") != digest
            or receipt.get("byte_size") != len(raw)
            or not artifact.get("artifact_upload_id")
            or receipt.get("upload_id") != artifact["artifact_upload_id"]
            or type(received) is not int
            or received <= 0
        ):
            raise ValueError("photo_original_artifact_completion_mismatch")
        return {
            "schema": "roboguide.memory-manifest/v0.1",
            "selector": {"memory_id": self.profile["memory_id"], "revision_id": "r1"},
            "kind": "artifact",
            "provider_id": "photo-captures",
            "owner": {
                "owner": "node",
                "node_id": self.profile["node_id"],
                "local_system_id": "dog2-camera",
            },
            "scope": {"kind": "global"},
            "visibility": "exchangeable",
            "payload_schema": "roboguide.rgb-image/v0.1",
            "media_type": "image/png",
            "artifact": {"content_digest": digest, "byte_size": len(raw)},
            "source_mission_id": invocation["mission_id"],
            "source_execution_id": record["attempt_id"],
            "source_task_ref": {
                "mission_id": invocation["mission_id"],
                "task_id": invocation["task_id"],
            },
            "created_at": received // 1000000,
        }

    def _verify(self, record: dict[str, Any]) -> None:
        """Rehash original PNG and reconstruct the same manifest before any discovery or export."""
        if (
            set(record)
            != {
                "schema",
                "profile",
                "capture_id",
                "attempt_id",
                "request",
                "detail",
                "manifest",
                "path",
            }
            or record["schema"] != "roboguide.photo-capture-publication/v0.1"
            or record["profile"] != self.profile
        ):
            raise Conflict("unsupported_or_changed_photo_publication")
        manifest = record["manifest"]
        if not isinstance(manifest, dict) or not isinstance(manifest.get("artifact"), dict):
            raise Conflict("photo_publication_manifest_required")
        digest = manifest["artifact"].get("content_digest")
        if (
            not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or record["path"] != digest[7:] + ".png"
        ):
            raise Conflict("photo_publication_path_mismatch")
        raw = _read_owned(self.root / record["path"])
        if self._manifest(record, raw) != manifest:
            raise Conflict("photo_publication_content_or_identity_changed")

    def refresh(self, captures: CaptureSource) -> None:
        """Offer only the explicit successful capture; never publish historical or failed photos."""
        with self.lock:
            if self.record is not None:
                return
            with captures.lock:
                rows = captures.db.execute(
                    "SELECT id,attempt,request,state,detail FROM captures ORDER BY created_ns"
                ).fetchall()
            selected: list[tuple[Any, ...]] = []
            for row in rows:
                request = json.loads(row[2])
                if not isinstance(request, dict):
                    continue
                invocation = request.get("invocation")
                parameters = invocation.get("parameters") if isinstance(invocation, dict) else None
                if (
                    isinstance(parameters, dict)
                    and parameters.get("photo_memory_id") == self.profile["memory_id"]
                ):
                    selected.append(row)
            if len(selected) > 1:
                raise Conflict("photo_publication_selector_has_multiple_captures")
            if not selected or selected[0][3] != "SUCCEEDED":
                return
            capture_id, attempt_id, request_json, _, detail_json = selected[0]
            capture_id = str(uuid.UUID(capture_id))
            raw = _read_owned(Path(captures.root).absolute() / "images" / (capture_id + ".png"))
            record = {
                "schema": "roboguide.photo-capture-publication/v0.1",
                "profile": self.profile,
                "capture_id": capture_id,
                "attempt_id": attempt_id,
                "request": json.loads(request_json),
                "detail": json.loads(detail_json),
            }
            manifest = self._manifest(record, raw)
            path = _publish(self.root, manifest["artifact"]["content_digest"], raw)
            record.update(manifest=manifest, path=path.name)
            payload = canonical(record).encode()
            if len(payload) > 131072:
                raise ValueError("photo_publication_metadata_exceeds_bound")
            _immutable(self.record_path, payload)
            self.record = record

    def discover(self) -> dict[str, Any]:
        """Return only the exact retained manifest after rechecking immutable local bytes."""
        with self.lock:
            if self.record is None:
                return {"manifests": []}
            self._verify(self.record)
            return {"manifests": [json.loads(canonical(self.record["manifest"]))]}

    def export(self, body: dict[str, Any]) -> dict[str, Any]:
        """Resolve only this exact manifest to its provider-relative immutable PNG path."""
        with self.lock:
            if (
                self.record is None
                or set(body) != {"manifest"}
                or body["manifest"] != self.record["manifest"]
            ):
                raise ValueError("photo_export_requires_exact_manifest")
            self._verify(self.record)
            return {"path": self.record["path"]}
