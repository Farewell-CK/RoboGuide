"""Exercise Scene source identity, cache lifetime and fixed readonly transport without a robot."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import Mock, patch

from robonix_task.scene_targets import (
    MAX_AGE_MS,
    MAX_BODY_BYTES,
    SCHEMA,
    SceneTargetObserver,
    _DeadlineSocket,
    decode_scene_definition,
    read_scene_document,
    validate_scene_targets,
)


def region(identity: str = "anno.tea", name: str = "茶水间门口") -> dict[str, Any]:
    """Return one synthetic original Scene definition with a nondegenerate polygon."""
    return {
        "annotation_id": identity,
        "kind": "region",
        "name": name,
        "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
        "theta": None,
        "created_at": 100,
        "updated_at": 100,
        "stale": False,
        "stale_reason": "",
    }


class Source:
    """Keep a deterministic loopback source substitute and process-local monotonic clock."""

    def __init__(self) -> None:
        """Initialize explicit local fixtures without opening any socket or acquiring a robot."""
        self.ns = 1_000_000_000
        self.calls: list[tuple[str, float]] = []
        self.state: dict[str, Any] = {
            "map_binding": {
                "map_id": "synthetic-office",
                "mode": "localization",
                "generation": 0,
                "source": "synthetic-test",
            },
            "objects": [{"id": "must-not-export"}],
            "atlas": {"private": "must-not-export"},
        }
        self.regions: dict[str, Any] = {"ok": True, "annotations": [region()]}
        self.confirmation: dict[str, Any] | None = None
        self.error: Exception | None = None
        self.delay_ns = 10_000_000
        self.raw: dict[str, bytes] = {}

    def clock(self) -> int:
        """Return only this fixture's monotonic time domain."""
        return self.ns

    def wall(self) -> int:
        """Supply positive source-local audit receipts without cross-host clock arithmetic."""
        return 1_000_000_000_000 + self.ns

    def read(self, path: str, timeout: float) -> bytes:
        """Return a finite original body and account for its own deterministic read delay."""
        self.calls.append((path, timeout))
        self.ns += self.delay_ns
        if self.error is not None:
            raise self.error
        value = self.regions if path == "/api/regions" else self.state
        if path == "/api/state" and len(self.calls) % 3 == 0 and self.confirmation is not None:
            value = self.confirmation
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.raw[path] = raw
        return raw

    def observer(self) -> SceneTargetObserver:
        """Construct the real observer with only source IO and clocks substituted."""
        return SceneTargetObserver(
            "entity-dog1", "dog1", reader=self.read, clock=self.clock, wall_clock=self.wall
        )


class SceneTargetsTests(unittest.TestCase):
    """Preserve original target definitions without promoting a cache receipt into localization."""

    def test_initial_unknown_and_get_never_contacts_scene(self) -> None:
        """A new process has no inherited evidence, and ordinary reads have no IO side effects."""
        source = Source()
        observer = source.observer()
        report = observer.read()
        self.assertEqual(report["schema_version"], SCHEMA)
        self.assertEqual(report["availability"], "unknown")
        self.assertEqual(report["remaining_at_publisher_ms"], 0)
        self.assertIsNone(report["definition_json"])
        self.assertEqual(source.calls, [])

    def test_current_original_regions_are_filtered_and_source_hashed(self) -> None:
        """Only original region World fields escape, with both state and region response digests."""
        source = Source()
        source.regions["annotations"][0]["atlas"] = {"private": "must-not-export"}
        source.regions["annotations"].append({"kind": "object", "name": "not-a-region"})
        observer = source.observer()
        observer.sample()
        report = observer.read()
        self.assertEqual(report["availability"], "current")
        self.assertEqual(decode_scene_definition(report)["regions"], [region()])
        self.assertNotIn("map_binding", report)
        self.assertNotIn("regions", report)
        self.assertEqual(report["sample_age_ms"], 30)
        self.assertEqual(report["remaining_at_publisher_ms"], MAX_AGE_MS - 30)
        self.assertEqual([p for p, _ in source.calls], ["/api/state", "/api/regions", "/api/state"])
        self.assertEqual(
            report["source_documents"]["regions"]["body_sha256"],
            "sha256:" + hashlib.sha256(source.raw["/api/regions"]).hexdigest(),
        )
        self.assertNotIn("must-not-export", json.dumps(report))
        self.assertFalse(report["arrival_verified"])
        self.assertFalse(report["localization_verified"])
        self.assertEqual(validate_scene_targets(report), report)

    def test_pose_receipts_and_region_order_do_not_change_definition_identity(self) -> None:
        """The stable target digest excludes live objects, HTTP clocks and source list order."""
        source = Source()
        source.regions["annotations"].append(region("anno.other", "other"))
        observer = source.observer()
        observer.sample()
        first = observer.read()
        source.state["objects"] = [{"changed_pose": [123, 456]}]
        source.regions["annotations"].reverse()
        observer.sample()
        second = observer.read()
        self.assertEqual(first["region_source_digest"], second["region_source_digest"])
        self.assertNotEqual(
            first["source_documents"]["state"]["body_sha256"],
            second["source_documents"]["state"]["body_sha256"],
        )
        self.assertNotEqual(first["sample_completed_at_ns"], second["sample_completed_at_ns"])

    def test_reads_spend_original_budget_and_expiry_does_not_renew(self) -> None:
        """Repeated outer State publication cannot extend the cached definitions' inner lifetime."""
        source = Source()
        observer = source.observer()
        started = source.ns
        observer.sample()
        first = observer.read()
        source.ns = started + 14_999_000_000
        self.assertEqual(observer.read()["remaining_at_publisher_ms"], 1)
        source.ns += 1_000_000
        expired = observer.read()
        self.assertEqual(expired["availability"], "expired")
        self.assertEqual(expired["remaining_at_publisher_ms"], 0)
        self.assertEqual(expired["definition_json"], first["definition_json"])
        self.assertEqual(expired["region_source_digest"], first["region_source_digest"])
        self.assertEqual(len(source.calls), 3)
        with self.assertRaises(ValueError):
            validate_scene_targets(expired)

    def test_failed_sample_clears_prior_current_definitions(self) -> None:
        """A failed subsequent observation publishes unavailable rather than a renewed old map."""
        source = Source()
        observer = source.observer()
        observer.sample()
        source.error = OSError("synthetic source unavailable")
        observer.sample()
        failed = observer.read()
        self.assertEqual(failed["availability"], "unavailable")
        self.assertIsNone(failed["definition_json"])
        self.assertIsNone(failed["region_source_digest"])
        self.assertEqual(failed["remaining_at_publisher_ms"], 0)
        self.assertNotIn("synthetic source unavailable", failed["reason"])

    def test_map_switch_between_original_reads_rejects_mixed_regions(self) -> None:
        """Different map generations cannot share a cross-request target snapshot."""
        source = Source()
        source.confirmation = copy.deepcopy(source.state)
        source.confirmation["map_binding"]["generation"] = 1
        observer = source.observer()
        observer.sample()
        self.assertEqual(observer.read()["availability"], "unavailable")

    def test_sampling_and_monotonic_clock_failures_do_not_create_current_reports(self) -> None:
        """Sampling delay and clock regression are separate explicit unavailable outcomes."""
        source = Source()
        observer = source.observer()
        observer.sample()
        source.ns = 0
        self.assertEqual(observer.read()["reason"], "scene_monotonic_clock_regressed")
        slow = Source()
        slow.delay_ns = 5_100_000_000
        late = slow.observer()
        late.sample()
        self.assertEqual(late.read()["availability"], "unavailable")
        self.assertEqual(len(slow.calls), 1)

    def test_malformed_or_oversized_source_never_publishes_current(self) -> None:
        """Malformed fields, unbounded input and unknown map bindings fail closed."""
        for change in ("duplicate", "too-large", "degenerate", "nan", "generation", "many"):
            with self.subTest(change=change):
                source = Source()
                if change == "degenerate":
                    source.regions["annotations"][0]["points"] = [[0, 0], [0, 0], [0, 0]]
                elif change == "nan":
                    source.regions["annotations"][0]["points"][0][0] = float("nan")
                elif change == "generation":
                    source.state["map_binding"]["generation"] = True
                elif change == "many":
                    source.regions["annotations"] = [region(str(i), "门" * 256) for i in range(64)]
                if change in {"duplicate", "too-large"}:
                    raw = (
                        b'{"map_binding":{},"map_binding":{}}'
                        if change == "duplicate"
                        else b"x" * (MAX_BODY_BYTES + 1)
                    )

                    def fixed_body(_path: str, _timeout: float, original: bytes = raw) -> bytes:
                        """Return this case's exact malformed bytes without late loop binding."""
                        return original

                    observer = SceneTargetObserver(
                        "entity-dog1",
                        "dog1",
                        reader=fixed_body,
                        clock=source.clock,
                        wall_clock=source.wall,
                    )
                else:
                    observer = source.observer()
                observer.sample()
                self.assertEqual(observer.read()["availability"], "unavailable")

    def test_report_preserves_unknown_mode_and_generation_without_localization_claims(self) -> None:
        """A real source's unknown binding fields remain original reported landmark evidence."""
        digests = []
        for mode in ("", None):
            with self.subTest(mode=mode):
                source = Source()
                source.state["map_binding"].update(mode=mode, generation=None)
                observer = source.observer()
                observer.sample()
                report = observer.read()
                self.assertEqual(report["availability"], "current")
                definition = decode_scene_definition(report)
                self.assertEqual(definition["map_binding"], source.state["map_binding"])
                self.assertEqual(definition["regions"], [region()])
                self.assertEqual(validate_scene_targets(report), report)
                self.assertFalse(report["localization_verified"])
                self.assertFalse(report["arrival_verified"])
                digests.append(report["region_source_digest"])
                source.confirmation = copy.deepcopy(source.state)
                source.confirmation["map_binding"]["generation"] = 0
                observer.sample()
                self.assertEqual(observer.read()["availability"], "unavailable")
        self.assertNotEqual(digests[0], digests[1])

    def test_unknown_binding_does_not_admit_wrong_types_or_missing_map_identity(self) -> None:
        """Only null or correctly typed original unknowns are allowed by the report contract."""
        for key, value in (
            ("mode", False),
            ("mode", []),
            ("mode", "x" * 65),
            ("generation", -1),
            ("generation", "0"),
            ("generation", 1.0),
            ("map_id", None),
            ("map_id", ""),
            ("source", None),
            ("source", ""),
        ):
            with self.subTest(key=key, value=value):
                source = Source()
                source.state["map_binding"].update(mode=None, generation=None)
                source.state["map_binding"][key] = value
                observer = source.observer()
                observer.sample()
                self.assertEqual(observer.read()["availability"], "unavailable")

    def test_current_report_tampering_is_detected_by_shared_validator(self) -> None:
        """Reject changed identities, definitions, hidden fields and inflated budgets."""
        source = Source()
        observer = source.observer()
        observer.sample()
        good = observer.read()
        shorter = copy.deepcopy(good)
        shorter["remaining_at_publisher_ms"] = 1
        self.assertEqual(validate_scene_targets(shorter), shorter)
        for field in ("identity", "geometry", "digest", "budget", "extra", "source", "wall"):
            with self.subTest(field=field):
                report = copy.deepcopy(good)
                if field == "identity":
                    report["source_node_id"] = "other-dog"
                elif field == "geometry":
                    definition = decode_scene_definition(report)
                    definition["regions"][0]["points"][0][0] = 0.1
                    report["definition_json"] = json.dumps(definition)
                elif field == "digest":
                    report["region_source_digest"] = "sha256:" + "0" * 64
                elif field == "budget":
                    report["remaining_at_publisher_ms"] += 1
                elif field == "extra":
                    report["resources"] = [{"kind": "space", "units": 1}]
                elif field == "source":
                    report["source_documents"]["state"]["path"] = "http://foreign.test"
                else:
                    report["sample_completed_at_ns"] = report["sample_started_at_ns"] - 1
                with self.assertRaises(ValueError):
                    validate_scene_targets(report, entity_id="entity-dog1", node_id="dog1")

    def test_exact_definition_string_preserves_real_problematic_float_values(self) -> None:
        """The original definition string survives outer JSON without rehashing typed f64 values."""
        source = Source()
        source.regions["annotations"][0]["points"] = [
            [11.399093130268493, -2.2241165304059414],
            [1.6169695854376407, -0.12282490304588523],
            [1.8489674036424497, 0.0],
        ]
        observer = source.observer()
        observer.sample()
        report = observer.read()
        transported = json.loads(json.dumps(report))
        self.assertEqual(validate_scene_targets(transported), report)
        definition = decode_scene_definition(transported)
        self.assertEqual(definition["regions"], source.regions["annotations"])
        self.assertIn("11.399093130268493", transported["definition_json"])
        self.assertIn("1.6169695854376407", transported["definition_json"])
        self.assertEqual(
            transported["region_source_digest"],
            "sha256:" + hashlib.sha256(transported["definition_json"].encode()).hexdigest(),
        )

    def test_definition_decoder_refuses_hashed_malformed_or_ambiguous_sources(self) -> None:
        """A valid digest cannot authorize duplicate keys, unsafe geometry or wrong identity."""
        source = Source()
        observer = source.observer()
        observer.sample()
        good = observer.read()
        for change in ("duplicate", "nan", "identity", "extra", "geometry", "legacy", "utf8"):
            with self.subTest(change=change):
                report = copy.deepcopy(good)
                definition = decode_scene_definition(report)
                if change == "duplicate":
                    raw = report["definition_json"].replace(
                        '"physical_entity_id":"entity-dog1"',
                        '"physical_entity_id":"entity-dog1","physical_entity_id":"entity-dog1"',
                    )
                elif change == "utf8":
                    raw = "\ud800"
                else:
                    if change == "nan":
                        definition["regions"][0]["points"][0][0] = float("nan")
                    elif change == "identity":
                        definition["source_node_id"] = "other-dog"
                    elif change == "extra":
                        definition["objects"] = [{"id": "injected"}]
                    elif change == "geometry":
                        definition["regions"][0]["points"] = [[0, 0], [0, 0], [0, 0]]
                    else:
                        report["map_binding"] = definition["map_binding"]
                        report["regions"] = definition["regions"]
                        report["schema_version"] = "roboguide.world.scene-targets/v0.1"
                    raw = json.dumps(
                        definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                report["definition_json"] = raw
                if change != "utf8":
                    report["region_source_digest"] = (
                        "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
                    )
                with self.assertRaises(ValueError):
                    validate_scene_targets(report)

    def test_fixed_http_transport_refuses_foreign_paths_and_oversized_bodies(self) -> None:
        """Pin loopback, disable proxies and redirects, and read a finite response body."""

        class Response:
            """Offer a context-managed response without opening a network connection."""

            status = 200

            def __enter__(self) -> Response:
                """Borrow the synthetic response exactly once for this fixed GET."""
                return self

            def __exit__(self, *args: Any) -> None:
                """Release no external resources in this transport fixture."""

            def read(self, limit: int) -> bytes:
                """Return the requested bounded prefix for overflow detection."""
                self.limit = limit
                return b"x" * limit

        connection, transport = Mock(), Mock()
        connection.getresponse.return_value = Response()
        with (
            patch("http.client.HTTPConnection", return_value=connection) as connections,
            patch("robonix_task.scene_targets._DeadlineSocket", return_value=transport) as sockets,
        ):
            with self.assertRaisesRegex(ValueError, "body_too_large"):
                read_scene_document("/api/regions", 2)
            connections.assert_called_once_with("127.0.0.1", 50107, timeout=2)
            sockets.assert_called_once_with(2)
            transport.connect.assert_called_once_with(("127.0.0.1", 50107))
            connection.request.assert_called_once_with(
                "GET", "/api/regions", headers={"Accept": "application/json"}
            )
            connection.close.assert_called_once()
            for path, timeout in (
                ("http://foreign.test/api/state", 1),
                ("/api/load", 1),
                ("/api/state", 6),
            ):
                with self.assertRaises(ValueError):
                    read_scene_document(path, timeout)
            self.assertEqual(connections.call_count, 1)
            connection.reset_mock()
            connection.getresponse.return_value.status = 302
            with self.assertRaisesRegex(ValueError, "status_unavailable"):
                read_scene_document("/api/state", 1)
            connection.request.assert_called_once_with(
                "GET", "/api/state", headers={"Accept": "application/json"}
            )

    def test_socket_deadline_is_not_extended_by_incremental_reads(self) -> None:
        """Every underlying socket read uses the same original remaining budget."""
        with patch("robonix_task.scene_targets.time.monotonic", side_effect=(10, 11, 15.001)):
            with _DeadlineSocket(5) as transport:
                transport._budget()
                self.assertEqual(transport.gettimeout(), 4)
                with self.assertRaises(TimeoutError):
                    transport._budget()

    def test_actual_incremental_http_body_obeys_one_original_deadline(self) -> None:
        """An actual slow loopback response cannot refresh the timeout with each byte."""

        class Handler(BaseHTTPRequestHandler):
            """Serve only this isolated software transport fixture, never any robot route."""

            def log_message(self, *_args: Any) -> None:
                """Keep expected transport interruptions out of unrelated test output."""

            def do_GET(self) -> None:
                """Send continuously arriving bytes that outlive a short original budget."""
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.end_headers()
                for byte in b"abcd":
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.03)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        original_connect = _DeadlineSocket.connect

        def isolated_connect(transport: _DeadlineSocket, address: Any) -> None:
            """Remap only the exact original literal source to this fixture's ephemeral listener."""
            self.assertEqual(address, ("127.0.0.1", 50107))
            original_connect(transport, ("127.0.0.1", server.server_port))

        try:
            with patch("robonix_task.scene_targets._DeadlineSocket.connect", isolated_connect):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    read_scene_document("/api/state", 0.05)
                self.assertLess(time.monotonic() - started, 0.3)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
