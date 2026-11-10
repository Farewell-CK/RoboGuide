"""Exercise same-Mission photo provenance, bounded fixed reads and immutable local staging."""

from __future__ import annotations

import copy
import hashlib
import json
import ssl
import unittest
import urllib.error
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

from robonix_task.photo_handoff import (
    MAX_IMAGE_BYTES,
    MAX_METADATA_BYTES,
    HttpsHandoffTransport,
    PhotoHandoffReader,
    _NoRedirect,
)


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Return one explicitly synthetic original capture and its attributed Controller views."""
    raw = b"synthetic opaque PNG bytes; parsing belongs to ImageInspector"
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    invocation = {
        "mission_id": "photo-mission",
        "group_id": "photo-group",
        "task_id": "inspect",
        "role_id": "reader",
        "execution_generation": 1,
        "operation": "image.inspect@v1",
        "parameters": {
            "producer_node_id": "lite3-dog2",
            "producer_task_id": "capture",
            "producer_role_id": "camera",
        },
    }
    mission = {
        "mission_id": "photo-mission",
        "group_id": "photo-group",
        "tasks": [
            {"task_id": "capture", "status": "Completed"},
            {"task_id": "inspect", "status": "Active"},
        ],
    }
    attempts = {
        "attempts": [
            {
                "execution_id": "original-camera-attempt",
                "mission_id": "photo-mission",
                "group_id": "photo-group",
                "task_id": "capture",
                "role_id": "camera",
                "node_id": "lite3-dog2",
                "status": "Completed",
            },
            {
                "execution_id": "original-consumer-attempt",
                "mission_id": "photo-mission",
                "group_id": "photo-group",
                "task_id": "inspect",
                "role_id": "reader",
                "node_id": "lite3-dog1",
                "status": "Running",
            },
        ]
    }
    state = {
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": 10000,
        "records": [
            {
                "object": {
                    "class": "node",
                    "object_type": "camera-capture",
                    "object_id": "lite3-dog2",
                },
                "source": "node:lite3-dog2/dog2-camera",
                "channel_id": "capture-result",
                "semantic": "reported",
                "stale": False,
                "value": {
                    "payload_schema": "lite3.camera-capture/v0.1",
                    "received_at_ms": 9000,
                    "valid_for_ms": 15000,
                    "source_epoch": "actual-node-session",
                    "sequence": 4,
                    "value": {
                        "capture": {
                            "attempt_id": "original-camera-attempt",
                            "execution_id": "original-capture-uuid",
                            "state": "SUCCEEDED",
                            "metadata": {
                                "sha256": digest[7:],
                                "byte_size": len(raw),
                                "media_type": "image/png",
                                "artifact_publication": "finalized",
                                "source_stamp_ns": 1234567,
                                "request_boundary_ns": 1234500,
                                "width": 640,
                                "height": 480,
                                "file_uri": "file:///untrusted/ignored.png",
                                "artifact": {
                                    "artifact_uri": "https://untrusted.invalid/do-not-follow",
                                    "artifact_content_digest": digest,
                                    "artifact_byte_size": len(raw),
                                    "artifact_finalized": True,
                                    "artifact_upload_id": "original-upload",
                                    "artifact_receipt": {
                                        "phase": "completed",
                                        "content_digest": digest,
                                        "byte_size": len(raw),
                                        "upload_id": "original-upload",
                                        "tenant_id": "local",
                                    },
                                },
                            },
                        }
                    },
                },
            }
        ],
    }
    return invocation, mission, attempts, state, raw


class FakeTransport:
    """Record only requested original authorities while serving bounded fixture responses."""

    def __init__(
        self, mission: dict[str, Any], attempts: dict[str, Any], state: dict[str, Any], raw: bytes
    ):
        """Keep independent mutable fixtures so each rejection can alter one original source."""
        self.mission, self.attempts, self.state, self.raw = mission, attempts, state, raw
        self.calls: list[tuple[str, str]] = []
        self.after_blob: dict[str, Any] | None = None
        self.memory_errors: list[int] = []
        self.consumer_statuses: list[str] = []
        self.consumer_task_statuses: list[str] = []
        self.memory = {
            "schema": "roboguide.memory-manifest/v0.1",
            "selector": {"memory_id": "photo-memory", "revision_id": "r1"},
            "kind": "artifact",
            "provider_id": "photo-captures",
            "payload_schema": "roboguide.rgb-image/v0.1",
            "media_type": "image/png",
            "owner": {"owner": "node", "node_id": "lite3-dog2", "local_system_id": "dog2-camera"},
            "scope": {"kind": "global"},
            "visibility": "exchangeable",
            "artifact": {
                "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
            },
            "source_mission_id": "photo-mission",
            "source_execution_id": "original-camera-attempt",
            "source_task_ref": {"mission_id": "photo-mission", "task_id": "capture"},
        }

    def get_json(self, origin: str, path: str) -> dict[str, Any]:
        """Read the selected fixture; no path can trigger a mutation or independent execution."""
        self.calls.append((origin, path))
        if path.startswith("/v1/memories/"):
            if self.memory_errors:
                raise urllib.error.HTTPError(
                    origin + path, self.memory_errors.pop(0), "fixture", Message(), None
                )
            return {"manifest": copy.deepcopy(self.memory)}
        if path.startswith("/v1/state/records?"):
            return copy.deepcopy(self.state)
        if path.endswith("/execution-attempts"):
            if self.consumer_statuses:
                self.attempts["attempts"][1]["status"] = self.consumer_statuses.pop(0)
            return copy.deepcopy(self.attempts)
        if self.consumer_task_statuses:
            self.mission["tasks"][1]["status"] = self.consumer_task_statuses.pop(0)
        return copy.deepcopy(self.mission)

    def get_bytes(self, origin: str, path: str, max_bytes: int) -> bytes:
        """Return original fixture bytes and optionally simulate concurrent lineage change."""
        self.calls.append((origin, path))
        if self.after_blob is not None:
            self.attempts = self.after_blob
        return self.raw


class PhotoHandoffTests(unittest.TestCase):
    """Keep output consumption bound to exact original completed Task and Artifact evidence."""

    def test_success_stages_original_bytes_and_ignores_untrusted_locations(self) -> None:
        """A same-Mission consumer fetches the fixed digest and preserves source attribution."""
        invocation, mission, attempts, state, raw = fixture()
        transport = FakeTransport(mission, attempts, state, raw)
        with TemporaryDirectory() as directory:
            reader = PhotoHandoffReader(
                "https://controller.invalid:8080",
                "https://artifact.invalid:8090",
                Path(directory) / "inputs",
                expected_memory_id="photo-memory",
                transport=transport,
            )
            result = reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
            self.assertEqual(Path(result["artifact_path"]).read_bytes(), raw)
            proof = result["producer_receipt"]
            self.assertEqual(proof["producer_attempt"], attempts["attempts"][0])
            self.assertEqual(proof["state_source_epoch"], "actual-node-session")
            self.assertEqual(proof["state_sequence"], 4)
            self.assertEqual(proof["source_stamp_ns"], 1234567)
            self.assertEqual(proof["memory_manifest"], transport.memory)
            self.assertFalse(proof["motion_authorized"])
            self.assertFalse(proof["memory_use_authority"])
            self.assertFalse(proof["physical_effect_verified"])
            self.assertEqual(proof["consumer_attempt_id"], "original-consumer-attempt")
            self.assertEqual(proof["consumer_attempt"], attempts["attempts"][1])
            self.assertEqual(proof["consumer_role_id"], "reader")
            stamp = Path(result["artifact_path"]).stat().st_mtime_ns
            self.assertEqual(
                reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt"), result
            )
            self.assertEqual(Path(result["artifact_path"]).stat().st_mtime_ns, stamp)
        self.assertTrue(all(origin.startswith("https://") for origin, _ in transport.calls))
        self.assertFalse(any("untrusted" in origin for origin, _ in transport.calls))
        self.assertEqual(sum(path.startswith("/v1/artifacts/") for _, path in transport.calls), 2)

    def test_foreign_inactive_or_ambiguous_consumer_never_reads_artifact(self) -> None:
        """The same producer output grants no right to an alternate, stopped or unknown consumer."""
        changes: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]] = [
            ("foreign_node", lambda m, a: a["attempts"][1].update(node_id="lite3-dog2")),
            ("foreign_group", lambda m, a: a["attempts"][1].update(group_id="other")),
            ("foreign_attempt", lambda m, a: a["attempts"][1].update(execution_id="other")),
            ("cancelled", lambda m, a: a["attempts"][1].update(status="Cancelled")),
            ("unknown", lambda m, a: a["attempts"][1].update(status="Unknown")),
            ("completed", lambda m, a: a["attempts"][1].update(status="Completed")),
            ("failed", lambda m, a: a["attempts"][1].update(status="Failed")),
            ("task_cancelled", lambda m, a: m["tasks"][1].update(status="Cancelled")),
            ("task_pending", lambda m, a: m["tasks"][1].update(status="Pending")),
            ("task_blocked", lambda m, a: m["tasks"][1].update(status="Blocked")),
            ("task_awaiting", lambda m, a: m["tasks"][1].update(status="AwaitingSatisfaction")),
            ("task_completed", lambda m, a: m["tasks"][1].update(status="Completed")),
            ("ambiguous", lambda m, a: a["attempts"].append(copy.deepcopy(a["attempts"][1]))),
        ]
        with TemporaryDirectory() as directory:
            for name, mutate in changes:
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    mutate(mission, attempts)
                    transport = FakeTransport(mission, attempts, state, raw)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with self.assertRaisesRegex(ValueError, "consumer"):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertFalse(
                        any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                    )

    def test_cancelled_or_replaced_consumer_during_download_publishes_no_file(self) -> None:
        """Recheck current consumer authority after bytes arrive and before local publication."""
        with TemporaryDirectory() as directory:
            for name in ("cancelled", "replaced"):
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    transport = FakeTransport(mission, attempts, state, raw)
                    transport.after_blob = copy.deepcopy(attempts)
                    if name == "cancelled":
                        transport.after_blob["attempts"][1]["status"] = "Cancelled"
                    else:
                        transport.after_blob["attempts"][1]["execution_id"] = "replacement"
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with self.assertRaisesRegex(ValueError, "consumer"):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertEqual(list(reader.input_root.iterdir()), [])

    def test_initial_running_wait_is_bounded_and_never_authorizes_pending_or_cancelled(
        self,
    ) -> None:
        """Only an exact Dispatched/Accepted consumer may await its original Running report."""
        with TemporaryDirectory() as directory:
            for name in ("running", "cancelled", "timeout"):
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    attempts["attempts"][1]["status"] = "Accepted"
                    transport = FakeTransport(mission, attempts, state, raw)
                    transport.consumer_statuses = (
                        ["Dispatched", "Accepted", "Running"]
                        if name == "running"
                        else ["Accepted", "Cancelled"]
                        if name == "cancelled"
                        else []
                    )
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with patch("robonix_task.photo_handoff.time.sleep"):
                        if name == "running":
                            result = reader.resolve(
                                invocation, consumer_attempt_id="original-consumer-attempt"
                            )
                            self.assertEqual(
                                result["producer_receipt"]["consumer_attempt"]["status"], "Running"
                            )
                        elif name == "cancelled":
                            with self.assertRaisesRegex(ValueError, "consumer"):
                                reader.resolve(
                                    invocation, consumer_attempt_id="original-consumer-attempt"
                                )
                        else:
                            with (
                                patch(
                                    "robonix_task.photo_handoff.time.monotonic", side_effect=[0, 4]
                                ),
                                self.assertRaisesRegex(ValueError, "budget_expired"),
                            ):
                                reader.resolve(
                                    invocation, consumer_attempt_id="original-consumer-attempt"
                                )
                    if name != "running":
                        self.assertFalse(
                            any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                        )

    def test_failed_foreign_or_ambiguous_producer_never_reads_artifact(self) -> None:
        """DAG completion and exact same-Mission lineage cannot be replaced by a fresh report."""
        mutations: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]] = [
            ("task_failed", lambda m, a: m["tasks"][0].update(status="Failed")),
            ("wrong_mission", lambda m, a: m.update(mission_id="other")),
            ("wrong_group", lambda m, a: a["attempts"][0].update(group_id="other")),
            ("wrong_node", lambda m, a: a["attempts"][0].update(node_id="lite3-dog1")),
            ("unknown", lambda m, a: a["attempts"][0].update(status="Unknown")),
            ("missing", lambda m, a: a.update(attempts=[])),
            ("ambiguous", lambda m, a: a["attempts"].append(copy.deepcopy(a["attempts"][0]))),
        ]
        with TemporaryDirectory() as directory:
            for name, mutate in mutations:
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    mutate(mission, attempts)
                    transport = FakeTransport(mission, attempts, state, raw)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with self.assertRaises(ValueError):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertFalse(
                        any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                    )

    def test_ready_task_waits_for_active_without_waiving_original_consumer_identity(self) -> None:
        """A real Ready/Dispatched progression reaches Active/Running within the original budget."""
        with TemporaryDirectory() as directory:
            for status in ("Dispatched", "Accepted", "Running"):
                with self.subTest(initial_attempt=status):
                    invocation, mission, attempts, state, raw = fixture()
                    mission["tasks"][1]["status"] = "Ready"
                    attempts["attempts"][1]["status"] = status
                    transport = FakeTransport(mission, attempts, state, raw)
                    transport.consumer_task_statuses = ["Ready", "Ready", "Active"]
                    transport.consumer_statuses = [status, "Running", "Running"]
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / status,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with patch("robonix_task.photo_handoff.time.sleep") as waiting:
                        result = reader.resolve(
                            invocation, consumer_attempt_id="original-consumer-attempt"
                        )
                    self.assertGreaterEqual(waiting.call_count, 2)
                    self.assertEqual(
                        result["producer_receipt"]["consumer_attempt"]["status"], "Running"
                    )
                    self.assertEqual(transport.mission["tasks"][1]["status"], "Active")

    def test_ready_consumer_terminal_foreign_or_expired_never_downloads(self) -> None:
        """Ready alone cannot authorize cancelled, unknown, foreign or stalled local work."""
        with TemporaryDirectory() as directory:
            for case in ("Cancelled", "Failed", "Unknown", "Completed", "foreign", "timeout"):
                with self.subTest(case=case):
                    invocation, mission, attempts, state, raw = fixture()
                    mission["tasks"][1]["status"] = "Ready"
                    attempts["attempts"][1]["status"] = (
                        "Running" if case in {"foreign", "timeout"} else case
                    )
                    if case == "foreign":
                        attempts["attempts"][1]["execution_id"] = "other-attempt"
                    transport = FakeTransport(mission, attempts, state, raw)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / case,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    if case == "timeout":
                        with (
                            patch(
                                "robonix_task.photo_handoff.time.monotonic",
                                side_effect=[0, 0, 0, 0, 0, 0, 0, 4],
                            ),
                            patch("robonix_task.photo_handoff.time.sleep"),
                            self.assertRaisesRegex(ValueError, "budget_expired"),
                        ):
                            reader.resolve(
                                invocation, consumer_attempt_id="original-consumer-attempt"
                            )
                    else:
                        with self.assertRaisesRegex(ValueError, "consumer"):
                            reader.resolve(
                                invocation, consumer_attempt_id="original-consumer-attempt"
                            )
                    self.assertFalse(
                        any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                    )

    def test_stale_foreign_and_replayed_capture_evidence_is_refused(self) -> None:
        """New outer sequence cannot turn an old or foreign capture into this Task's output."""
        mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("stale", lambda r: r.update(stale=True)),
            ("ttl", lambda r: r["value"].update(received_at_ms=-10000)),
            ("future", lambda r: r["value"].update(received_at_ms=10001)),
            ("sequence", lambda r: r["value"].update(sequence=0)),
            ("epoch", lambda r: r["value"].update(source_epoch="")),
            ("source", lambda r: r.update(source="node:lite3-dog1/dog2-camera")),
            ("schema", lambda r: r["value"].update(payload_schema="unknown")),
            (
                "old_attempt",
                lambda r: r["value"]["value"]["capture"].update(attempt_id="old-attempt"),
            ),
            (
                "unfinished",
                lambda r: r["value"]["value"]["capture"].update(state="RUNNING"),
            ),
            (
                "foreign_tenant",
                lambda r: r["value"]["value"]["capture"]["metadata"]["artifact"][
                    "artifact_receipt"
                ].update(tenant_id="other"),
            ),
        ]
        with TemporaryDirectory() as directory:
            for name, mutate in mutations:
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    mutate(state["records"][0])
                    transport = FakeTransport(mission, attempts, state, raw)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with self.assertRaises(ValueError):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertFalse(
                        any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                    )

    def test_corrupt_size_changed_producer_and_existing_cache_fail_closed(self) -> None:
        """Reject changed lineage, bad bytes and oversized input before immutable publication."""
        with TemporaryDirectory() as directory:
            for name in ("corrupt", "size", "changed", "cache", "oversized"):
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    transport = FakeTransport(mission, attempts, state, raw)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    target = reader.input_root / (hashlib.sha256(raw).hexdigest() + ".png")
                    if name == "corrupt":
                        transport.raw = bytes([raw[0] ^ 1]) + raw[1:]
                    elif name == "size":
                        transport.raw += b"x"
                    elif name == "changed":
                        transport.after_blob = copy.deepcopy(attempts)
                        transport.after_blob["attempts"][0]["execution_id"] = "replacement"
                    elif name == "cache":
                        target.write_bytes(b"corrupt old cache")
                    else:
                        state["records"][0]["value"]["value"]["capture"]["metadata"]["artifact"][
                            "artifact_byte_size"
                        ] = MAX_IMAGE_BYTES + 1
                    with self.assertRaises(ValueError):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertEqual(
                        list(reader.input_root.iterdir()), [target] if name == "cache" else []
                    )

    def test_parameters_and_cache_symlinks_cannot_select_new_input_sources(self) -> None:
        """A caller cannot supply URLs, foreign producer nodes or symlink staging targets."""
        with TemporaryDirectory() as directory:
            invocation, mission, attempts, state, raw = fixture()
            transport = FakeTransport(mission, attempts, state, raw)
            reader = PhotoHandoffReader(
                "https://controller.invalid",
                "https://artifact.invalid",
                Path(directory) / "inputs",
                expected_memory_id="photo-memory",
                transport=transport,
            )
            for patch_value in (
                {"url": "https://untrusted.invalid"},
                {"producer_node_id": "other"},
            ):
                body = copy.deepcopy(invocation)
                body["parameters"].update(patch_value)
                with self.assertRaises(ValueError):
                    reader.resolve(body, consumer_attempt_id="original-consumer-attempt")
            outside = Path(directory) / "outside.png"
            outside.write_bytes(raw)
            target = reader.input_root / (hashlib.sha256(raw).hexdigest() + ".png")
            try:
                target.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable on this host")
            with self.assertRaisesRegex(ValueError, "symlink"):
                reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
            self.assertEqual(outside.read_bytes(), raw)

    def test_memory_requires_exact_original_global_identity_before_blob_read(self) -> None:
        """An ACL-discoverable Memory cannot substitute another Task, producer, digest or scope."""
        cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("scope", lambda m: m.update(scope={"kind": "local"})),
            ("visibility", lambda m: m.update(visibility="discoverable")),
            ("provider", lambda m: m.update(provider_id="other")),
            ("schema", lambda m: m.update(payload_schema="other")),
            ("attempt", lambda m: m.update(source_execution_id="old")),
            ("mission", lambda m: m.update(source_mission_id="old")),
            ("selector", lambda m: m["selector"].update(revision_id="old")),
            ("digest", lambda m: m["artifact"].update(content_digest="sha256:" + "0" * 64)),
        ]
        with TemporaryDirectory() as directory:
            for name, mutate in cases:
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    transport = FakeTransport(mission, attempts, state, raw)
                    mutate(transport.memory)
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    with self.assertRaisesRegex(ValueError, "memory"):
                        reader.resolve(invocation, consumer_attempt_id="original-consumer-attempt")
                    self.assertFalse(
                        any(p.startswith("/v1/artifacts/") for _, p in transport.calls)
                    )

    def test_only_missing_memory_is_retried_and_publication_budget_is_bounded(self) -> None:
        """Retry only 404 within the original budget; deny immediately on 403."""
        with TemporaryDirectory() as directory:
            for name in ("published", "forbidden", "timeout"):
                with self.subTest(case=name):
                    invocation, mission, attempts, state, raw = fixture()
                    transport = FakeTransport(mission, attempts, state, raw)
                    transport.memory_errors = [403] if name == "forbidden" else [404]
                    reader = PhotoHandoffReader(
                        "https://controller.invalid",
                        "https://artifact.invalid",
                        Path(directory) / name,
                        expected_memory_id="photo-memory",
                        transport=transport,
                    )
                    if name == "published":
                        with patch("robonix_task.photo_handoff.time.sleep") as sleep:
                            self.assertEqual(
                                reader.resolve(
                                    invocation, consumer_attempt_id="original-consumer-attempt"
                                )["content_digest"],
                                "sha256:" + hashlib.sha256(raw).hexdigest(),
                            )
                        self.assertEqual(sleep.call_count, 1)
                    elif name == "forbidden":
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            reader.resolve(
                                invocation, consumer_attempt_id="original-consumer-attempt"
                            )
                        self.assertEqual(error.exception.code, 403)
                    else:
                        with (
                            patch(
                                "robonix_task.photo_handoff.time.monotonic",
                                side_effect=[0] * 8 + [16, 16],
                            ),
                            patch("robonix_task.photo_handoff.time.sleep"),
                            self.assertRaisesRegex(ValueError, "not_published"),
                        ):
                            reader.resolve(
                                invocation, consumer_attempt_id="original-consumer-attempt"
                            )


class TransportTests(unittest.TestCase):
    """Keep deployment read identities under original hostname, tenant and size boundaries."""

    def test_transport_requires_verified_https_and_rejects_redirect(self) -> None:
        """No insecure context or response-selected destination may receive the client identity."""
        context = ssl.create_default_context()
        context.check_hostname = False
        with self.assertRaises(ValueError):
            HttpsHandoffTransport({"https://controller.invalid": context}, "local")
        with self.assertRaises(ValueError):
            HttpsHandoffTransport(
                {"http://controller.invalid": ssl.create_default_context()}, "local"
            )
        with self.assertRaisesRegex(ValueError, "redirect"):
            _NoRedirect().redirect_request(None, None, 302, "found", None, "https://other.invalid")

    def test_transport_bounds_original_bytes_and_sends_exact_tenant(self) -> None:
        """Fixed GET reads disable proxy inheritance, retain tenant and reject oversized JSON."""
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = json.dumps({"ok": True}).encode()
        with patch("urllib.request.build_opener", return_value=opener) as build:
            transport = HttpsHandoffTransport(
                {"https://controller.invalid": ssl.create_default_context()}, "local"
            )
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertEqual(
            transport.get_json("https://controller.invalid", "/v1/mission"), {"ok": True}
        )
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("X-roboguide-tenant"), "local")
        self.assertEqual(request.full_url, "https://controller.invalid/v1/mission")
        response.read.assert_called_with(MAX_METADATA_BYTES + 1)
        response.read.return_value = b"x" * (MAX_METADATA_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "bound"):
            transport.get_json("https://controller.invalid", "/v1/mission")
        response.read.return_value = b'{"ok":1,"ok":2}'
        with self.assertRaisesRegex(ValueError, "duplicate"):
            transport.get_json("https://controller.invalid", "/v1/mission")
        with self.assertRaisesRegex(ValueError, "fixed"):
            transport.get_bytes("https://other.invalid", "/v1/mission", 10)


if __name__ == "__main__":
    unittest.main()
