"""Resolve one same-Mission photo handoff through fixed authenticated read authorities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol, cast

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


class _ConsumerPending(ValueError):
    """Represent only an exact current consumer awaiting its first Running observation."""


def _origin(value: str) -> str:
    """Admit a fixed HTTPS authority without caller paths, credentials or redirect targets."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("fixed_https_origin_required")
    return value.rstrip("/")


def _object(value: object, reason: str) -> dict[str, Any]:
    """Require an original JSON object without coercing absent or scalar evidence."""
    if not isinstance(value, dict):
        raise ValueError(reason)
    return value


def _text(value: object, reason: str) -> str:
    """Keep identifiers bounded before constructing any fixed-authority read path."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(reason)
    return value


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate metadata keys rather than reinterpret ambiguous authority fields."""
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _constant(value: str) -> Any:
    """Refuse non-finite JSON constants at the authenticated response boundary."""
    raise ValueError("invalid_json_constant:" + value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a configured read identity to a server-selected target."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Make every redirect fail instead of silently changing authorities or paths."""
        raise ValueError("handoff_redirect_forbidden")


class HandoffTransport(Protocol):
    """Read only fixed Controller JSON and Artifact bytes under deployment identities."""

    def get_json(self, origin: str, path: str) -> dict[str, Any]:
        """Return one bounded authenticated metadata document without mutation."""
        ...

    def get_bytes(self, origin: str, path: str, max_bytes: int) -> bytes:
        """Read a bounded original blob without redirect, decompression or remote execution."""
        ...


class HttpsHandoffTransport:
    """Bind hostname-verifying mTLS contexts to exactly the configured read authorities."""

    def __init__(
        self,
        contexts: dict[str, ssl.SSLContext],
        tenant_id: str,
        timeout_seconds: float = 10,
    ) -> None:
        """Disable environment proxies and require the original certificate verification rules."""
        if not 0 < timeout_seconds <= 30 or not tenant_id or tenant_id == "*":
            raise ValueError("bounded_timeout_and_exact_tenant_required")
        self.tenant_id = tenant_id
        self.timeout_seconds = timeout_seconds
        self._openers: dict[str, urllib.request.OpenerDirector] = {}
        for origin, context in contexts.items():
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError("verified_server_identity_required")
            self._openers[_origin(origin)] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context),
                _NoRedirect(),
            )

    def _get_bytes(self, origin: str, path: str, max_bytes: int, timeout_seconds: float) -> bytes:
        """GET one bounded fixed-origin object; any non-200 or oversized response fails."""
        if (
            origin not in self._openers
            or not path.startswith("/v1/")
            or path.startswith("//")
            or not 0 < max_bytes <= MAX_IMAGE_BYTES
        ):
            raise ValueError("fixed_handoff_read_required")
        request = urllib.request.Request(
            origin + path,
            headers={"X-RoboGuide-Tenant": self.tenant_id, "Accept-Encoding": "identity"},
            method="GET",
        )
        with self._openers[origin].open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise ValueError("handoff_read_not_successful")
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("handoff_response_exceeds_bound")
        return cast(bytes, raw)

    def get_bytes(self, origin: str, path: str, max_bytes: int) -> bytes:
        """GET one original object under the configured per-request timeout and size bound."""
        return self._get_bytes(origin, path, max_bytes, self.timeout_seconds)

    def get_json(self, origin: str, path: str) -> dict[str, Any]:
        """Decode one unambiguous bounded metadata object without relaxing original fields."""
        return self.get_json_with_timeout(origin, path, self.timeout_seconds)

    def get_json_with_timeout(
        self, origin: str, path: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Honor the smaller remaining publication budget for an exact Memory metadata read."""
        if not 0 < timeout_seconds <= self.timeout_seconds:
            raise ValueError("bounded_handoff_timeout_required")
        raw = self._get_bytes(origin, path, MAX_METADATA_BYTES, timeout_seconds)
        return _object(
            json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant),
            "handoff_metadata_object_required",
        )


def _no_links(path: Path) -> None:
    """Reject symlink or Windows reparse ancestors before creating or reading owned inputs."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError("handoff_input_symlink_forbidden")


def _read_owned(path: Path) -> bytes:
    """Verify existing cache bytes through a no-follow regular-file descriptor."""
    _no_links(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
            raise ValueError("handoff_regular_bounded_input_required")
        return stream.read(MAX_IMAGE_BYTES + 1)


def _publish(root: Path, digest: str, raw: bytes) -> Path:
    """Publish immutable digest-addressed input without overwriting any existing content."""
    _no_links(root)
    path = root / (digest[7:] + ".png")
    if path.exists() or path.is_symlink():
        if _read_owned(path) != raw:
            raise ValueError("handoff_existing_input_corrupt")
        return path
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".photo-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _no_links(root)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_owned(path) != raw:
                raise ValueError("handoff_existing_input_corrupt") from None
        if os.name != "nt":
            descriptor = os.open(
                root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


class PhotoHandoffReader:
    """Bind a downstream local computation to one original completed producer and its bytes."""

    def __init__(
        self,
        controller_origin: str,
        artifact_origin: str,
        input_root: Path,
        *,
        expected_memory_id: str,
        controller_context: ssl.SSLContext | None = None,
        artifact_context: ssl.SSLContext | None = None,
        tenant_id: str = "local",
        producer_node_id: str = "lite3-dog2",
        consumer_node_id: str = "lite3-dog1",
        transport: HandoffTransport | None = None,
    ) -> None:
        """Fix authorities and cache; requests cannot choose credentials or URLs."""
        self.controller_origin = _origin(controller_origin)
        self.artifact_origin = _origin(artifact_origin)
        self.tenant_id = _text(tenant_id, "exact_handoff_tenant_required")
        if tenant_id == "*" or producer_node_id == consumer_node_id:
            raise ValueError("distinct_nodes_and_exact_tenant_required")
        self.producer_node_id = _text(producer_node_id, "producer_node_required")
        self.consumer_node_id = _text(consumer_node_id, "consumer_node_required")
        self.expected_memory_id = _text(expected_memory_id, "fixed_photo_memory_id_required")
        self.input_root = input_root.absolute()
        _no_links(self.input_root)
        self.input_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _no_links(self.input_root)
        if transport is None:
            if controller_context is None or artifact_context is None:
                raise ValueError("origin_specific_handoff_identities_required")
            transport = HttpsHandoffTransport(
                {
                    self.controller_origin: controller_context,
                    self.artifact_origin: artifact_context,
                },
                tenant_id,
            )
        self.transport = transport

    def _memory(self, producer: dict[str, Any], digest: str, size: int) -> dict[str, Any]:
        """Wait at most 15s for the exact bounded Global publication, never retrying ACL denial."""
        path = "/v1/memories/" + urllib.parse.quote(self.expected_memory_id, safe="")
        path += "/revisions/r1"
        deadline = time.monotonic() + 15
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("original_photo_memory_not_published")
            try:
                if isinstance(self.transport, HttpsHandoffTransport):
                    response = self.transport.get_json_with_timeout(
                        self.artifact_origin,
                        path,
                        min(remaining, self.transport.timeout_seconds),
                    )
                else:
                    response = self.transport.get_json(self.artifact_origin, path)
                break
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        manifest = _object(response.get("manifest"), "photo_memory_manifest_required")
        if (
            manifest.get("schema") != "roboguide.memory-manifest/v0.1"
            or manifest.get("selector")
            != {"memory_id": self.expected_memory_id, "revision_id": "r1"}
            or manifest.get("kind") != "artifact"
            or manifest.get("provider_id") != "photo-captures"
            or manifest.get("payload_schema") != "roboguide.rgb-image/v0.1"
            or manifest.get("media_type") != "image/png"
            or manifest.get("scope") != {"kind": "global"}
            or manifest.get("visibility") != "exchangeable"
            or manifest.get("owner")
            != {
                "owner": "node",
                "node_id": self.producer_node_id,
                "local_system_id": "dog2-camera",
            }
            or manifest.get("artifact") != {"content_digest": digest, "byte_size": size}
            or manifest.get("source_mission_id") != producer["mission_id"]
            or manifest.get("source_execution_id") != producer["execution_id"]
            or manifest.get("source_task_ref")
            != {"mission_id": producer["mission_id"], "task_id": producer["task_id"]}
        ):
            raise ValueError("photo_memory_not_original_global_publication")
        return manifest

    def _producer(self, invocation: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        """Require exactly one completed producer attempt in this original Mission and Group."""
        mission_id = _text(invocation.get("mission_id"), "mission_id_required")
        group_id = _text(invocation.get("group_id"), "group_id_required")
        path = "/v1/missions/" + urllib.parse.quote(mission_id, safe="")
        mission = self.transport.get_json(self.controller_origin, path)
        if mission.get("mission_id") != mission_id or mission.get("group_id") != group_id:
            raise ValueError("producer_mission_or_group_mismatch")
        tasks = mission.get("tasks")
        if not isinstance(tasks, list):
            raise ValueError("producer_task_evidence_missing")
        selected = [
            t
            for t in tasks
            if isinstance(t, dict) and t.get("task_id") == parameters["producer_task_id"]
        ]
        if len(selected) != 1 or selected[0].get("status") != "Completed":
            raise ValueError("producer_task_not_completed")
        attempts = self.transport.get_json(self.controller_origin, path + "/execution-attempts")
        rows = attempts.get("attempts")
        if not isinstance(rows, list):
            raise ValueError("producer_attempt_evidence_missing")
        selected = [
            a
            for a in rows
            if isinstance(a, dict)
            and a.get("task_id") == parameters["producer_task_id"]
            and a.get("role_id") == parameters["producer_role_id"]
        ]
        if len(selected) != 1:
            raise ValueError("producer_attempt_missing_or_ambiguous")
        attempt = selected[0]
        if (
            attempt.get("mission_id") != mission_id
            or attempt.get("group_id") != group_id
            or attempt.get("node_id") != self.producer_node_id
            or attempt.get("status") != "Completed"
        ):
            raise ValueError("producer_attempt_not_completed_or_foreign")
        _text(attempt.get("execution_id"), "producer_attempt_id_required")
        return attempt

    def _authority_json(self, path: str, deadline: float | None) -> dict[str, Any]:
        """Read consumer authority within its original initial-Running deadline when present."""
        if deadline is None:
            return self.transport.get_json(self.controller_origin, path)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("consumer_running_observation_budget_expired")
        if isinstance(self.transport, HttpsHandoffTransport):
            response = self.transport.get_json_with_timeout(
                self.controller_origin,
                path,
                min(remaining, self.transport.timeout_seconds),
            )
        else:
            response = self.transport.get_json(self.controller_origin, path)
        if time.monotonic() >= deadline:
            raise ValueError("consumer_running_observation_budget_expired")
        return response

    def _consumer(
        self,
        invocation: dict[str, Any],
        consumer_attempt_id: str,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Require this exact running consumer under the original Mission, Group and Node."""
        mission_id = _text(invocation.get("mission_id"), "mission_id_required")
        group_id = _text(invocation.get("group_id"), "group_id_required")
        task_id = _text(invocation.get("task_id"), "consumer_task_id_required")
        role_id = _text(invocation.get("role_id"), "consumer_role_id_required")
        _text(consumer_attempt_id, "original_consumer_attempt_id_required")
        path = "/v1/missions/" + urllib.parse.quote(mission_id, safe="")
        mission = self._authority_json(path, deadline)
        if mission.get("mission_id") != mission_id or mission.get("group_id") != group_id:
            raise ValueError("consumer_mission_or_group_mismatch")
        tasks = mission.get("tasks")
        if not isinstance(tasks, list):
            raise ValueError("consumer_task_evidence_missing")
        selected = [t for t in tasks if isinstance(t, dict) and t.get("task_id") == task_id]
        if len(selected) != 1 or selected[0].get("status") not in {"Ready", "Active"}:
            raise ValueError("consumer_task_not_active")
        task_ready = selected[0]["status"] == "Ready"
        response = self._authority_json(path + "/execution-attempts", deadline)
        rows = response.get("attempts")
        if not isinstance(rows, list):
            raise ValueError("consumer_attempt_evidence_missing")
        selected = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("task_id") == task_id
            and row.get("role_id") == role_id
        ]
        if len(selected) != 1:
            raise ValueError("consumer_attempt_missing_or_ambiguous")
        consumer = selected[0]
        if (
            consumer.get("execution_id") != consumer_attempt_id
            or consumer.get("mission_id") != mission_id
            or consumer.get("group_id") != group_id
            or consumer.get("node_id") != self.consumer_node_id
        ):
            raise ValueError("consumer_attempt_not_current_running_or_foreign")
        if consumer.get("status") in {"Dispatched", "Accepted"}:
            raise _ConsumerPending("consumer_running_observation_not_yet_received")
        if consumer.get("status") != "Running":
            raise ValueError("consumer_attempt_not_current_running_or_foreign")
        if task_ready:
            raise _ConsumerPending("consumer_task_activation_not_yet_received")
        return consumer

    def _running_consumer(
        self, invocation: dict[str, Any], consumer_attempt_id: str
    ) -> dict[str, Any]:
        """Wait at most 3s for exact Ready/Accepted progression to Active plus Running."""
        deadline = time.monotonic() + 3
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("consumer_running_observation_budget_expired")
            try:
                return self._consumer(invocation, consumer_attempt_id, deadline=deadline)
            except _ConsumerPending:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def resolve(self, invocation: dict[str, Any], *, consumer_attempt_id: str) -> dict[str, Any]:
        """Resolve exact attributed evidence and bytes; fail without fabricating a new producer."""
        if invocation.get("operation") != "image.inspect@v1":
            raise ValueError("image_inspection_operation_required")
        parameters = _object(invocation.get("parameters"), "producer_parameters_required")
        if set(parameters) != {"producer_task_id", "producer_role_id", "producer_node_id"}:
            raise ValueError("exact_producer_parameters_required")
        for key, value in parameters.items():
            _text(value, key + "_required")
        if (
            parameters["producer_node_id"] != self.producer_node_id
            or parameters["producer_task_id"] == invocation.get("task_id")
            or type(invocation.get("execution_generation")) is not int
            or invocation["execution_generation"] <= 0
        ):
            raise ValueError("distinct_original_producer_required")
        consumer = self._running_consumer(invocation, consumer_attempt_id)
        producer = self._producer(invocation, parameters)
        state = self.transport.get_json(
            self.controller_origin,
            "/v1/state/records?object_class=node&object_type=camera-capture",
        )
        now, records = state.get("observed_at_ms"), state.get("records")
        if (
            state.get("schema") != "roboguide.state-query/v0.1"
            or type(now) is not int
            or not isinstance(records, list)
        ):
            raise ValueError("camera_state_evidence_missing")
        source = f"node:{self.producer_node_id}/dog2-camera"
        selected = [
            r
            for r in records
            if isinstance(r, dict)
            and r.get("object")
            == {
                "class": "node",
                "object_type": "camera-capture",
                "object_id": self.producer_node_id,
            }
            and r.get("source") == source
            and r.get("channel_id") == "capture-result"
            and r.get("semantic") == "reported"
        ]
        if len(selected) != 1:
            raise ValueError("camera_source_missing_or_ambiguous")
        record = selected[0]
        envelope = _object(record.get("value"), "camera_envelope_required")
        received, ttl = envelope.get("received_at_ms"), envelope.get("valid_for_ms")
        if (
            record.get("stale") is not False
            or envelope.get("payload_schema") != "lite3.camera-capture/v0.1"
            or type(received) is not int
            or type(ttl) is not int
            or not 0 < ttl <= 15000
            or not 0 <= now - received < ttl
            or type(envelope.get("sequence")) is not int
            or envelope["sequence"] <= 0
            or not isinstance(envelope.get("source_epoch"), str)
            or not envelope["source_epoch"]
        ):
            raise ValueError("camera_envelope_stale_or_unqualified")
        snapshot = _object(envelope.get("value"), "camera_snapshot_required")
        capture = _object(snapshot.get("capture"), "original_capture_required")
        if (
            capture.get("attempt_id") != producer["execution_id"]
            or capture.get("state") != "SUCCEEDED"
        ):
            raise ValueError("capture_not_original_completed_producer")
        capture_id = _text(capture.get("execution_id"), "capture_id_required")
        metadata = _object(capture.get("metadata"), "capture_metadata_required")
        artifact = _object(metadata.get("artifact"), "capture_artifact_required")
        digest, size = artifact.get("artifact_content_digest"), artifact.get("artifact_byte_size")
        receipt = _object(artifact.get("artifact_receipt"), "artifact_completion_receipt_required")
        if (
            not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or type(size) is not int
            or not 0 < size <= MAX_IMAGE_BYTES
            or artifact.get("artifact_finalized") is not True
            or metadata.get("artifact_publication") != "finalized"
            or metadata.get("media_type") != "image/png"
            or metadata.get("sha256") != digest[7:]
            or metadata.get("byte_size") != size
            or receipt.get("content_digest") != digest
            or receipt.get("byte_size") != size
            or receipt.get("phase") != "completed"
            or receipt.get("tenant_id") != self.tenant_id
            or not isinstance(artifact.get("artifact_upload_id"), str)
            or not artifact["artifact_upload_id"]
            or receipt.get("upload_id") != artifact["artifact_upload_id"]
        ):
            raise ValueError("original_artifact_completion_unqualified")
        manifest = self._memory(producer, digest, size)
        raw = self.transport.get_bytes(
            self.artifact_origin, "/v1/artifacts/" + digest[7:], MAX_IMAGE_BYTES
        )
        if len(raw) != size or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("handoff_artifact_digest_or_size_mismatch")
        if self._producer(invocation, parameters) != producer:
            raise ValueError("producer_changed_during_handoff")
        if self._consumer(invocation, consumer_attempt_id) != consumer:
            raise ValueError("consumer_changed_during_handoff")
        path = _publish(self.input_root, digest, raw)
        proof = {
            "schema": "roboguide.photo-handoff/v0.1",
            "producer_attempt": producer,
            "consumer_node_id": self.consumer_node_id,
            "consumer_attempt_id": consumer_attempt_id,
            "consumer_attempt": consumer,
            "consumer_task_ref": {
                "mission_id": invocation["mission_id"],
                "task_id": invocation["task_id"],
            },
            "consumer_role_id": invocation["role_id"],
            "capture_id": capture_id,
            "content_digest": digest,
            "byte_size": size,
            "state_source": source,
            "state_channel_id": "capture-result",
            "state_source_epoch": envelope["source_epoch"],
            "state_sequence": envelope["sequence"],
            "controller_received_at_ms": received,
            "controller_observed_at_ms": now,
            "valid_for_ms": ttl,
            "source_stamp_ns": metadata.get("source_stamp_ns"),
            "request_boundary_ns": metadata.get("request_boundary_ns"),
            "reported_width": metadata.get("width"),
            "reported_height": metadata.get("height"),
            "artifact_completion_receipt": receipt,
            "memory_manifest": manifest,
            "motion_authorized": False,
            "memory_use_authority": False,
            "physical_effect_verified": False,
        }
        return {"artifact_path": str(path), "content_digest": digest, "producer_receipt": proof}
