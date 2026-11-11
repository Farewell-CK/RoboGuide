"""Verify manual arrival publication boundaries using synthetic projections and no robots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import ssl
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from robonix_task.arrival_verifier import (
    CONFIG_SCHEMA,
    CONFIRMATION_SCHEMA,
    ArrivalVerificationError,
    ArrivalVerifier,
    HttpArrivalController,
    _json,
    main,
    validate_config,
)
from robonix_task.arrival_verifier_store import ArrivalVerifierStore, digest, encode
from robonix_task.patrol import ARRIVAL_CONTRACT


def fixture() -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Build one synthetic accepted Task with two roles on the same fixed physical Actor."""
    target = {
        "entity_id": "entity-test-dog",
        "node_id": "test-dog",
        "target_name": "合成门口",
        "map_id": "synthetic-map",
        "region_id": "synthetic-region",
        "region_source_digest": "sha256:" + "1" * 64,
    }
    predicate = (
        "onsite-arrival/v0.1 entity-test-dog arrived-and-stopped at 合成门口 target-reference-"
        + digest(target)
    )
    config = {
        "schema_version": CONFIG_SCHEMA,
        "source_id": "onsite-test",
        "revision": 1,
        "tenant_id": "local",
        "producer_principal": "observer:test",
        "verifier": dict(ARRIVAL_CONTRACT),
        "max_age_ms": 5000,
        "targets": [{**target, "predicate": predicate}],
    }
    plan = {
        "schema_version": "roboguide.mission-plan/v0.10",
        "mission": {
            "id": "mission-test",
            "actors": [{"id": "dog", "physical_entity": "entity-test-dog"}],
        },
        "contexts": [{"id": "inspection", "roles": [{"id": "navigator", "actor": "dog"}]}],
        "tasks": [
            {
                "id": "navigate",
                "context_id": "inspection",
                "roles": [
                    {"id": role, "context_role": "navigator"} for role in ("route", "report")
                ],
                "satisfaction": {
                    "basis": "verifier-evidence",
                    "expected_effect": predicate,
                    "verifier": {
                        "contract": dict(ARRIVAL_CONTRACT),
                        "predicate": predicate,
                        "max_evidence_age_ms": 5000,
                    },
                },
            }
        ],
    }
    raw = encode(plan).encode()
    view = {
        "tenant": {
            "mission_id": "mission-test",
            "tenant_id": "local",
            "source_plan_digest": "sha256:" + "2" * 64,
        },
        "admission": {
            "mission_id": "mission-test",
            "group_id": "group-test",
            "accepted_request_body_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        },
        "mission": {
            "mission_id": "mission-test",
            "group_id": "group-test",
            "status": "Running",
            "plan_revision": 0,
            "tasks": [
                {
                    "task_id": "navigate",
                    "context_id": "inspection",
                    "status": "AwaitingSatisfaction",
                }
            ],
        },
        "attempts": [
            {
                "task_id": "navigate",
                "mission_id": "mission-test",
                "group_id": "group-test",
                "role_id": role,
                "execution_id": "attempt-" + role,
                "node_id": "test-dog",
                "status": "Completed",
            }
            for role in ("route", "report")
        ],
    }
    return config, raw, view


class Clock:
    """Measure deterministic same-process elapsed time without claiming a robot source clock."""

    def __init__(self) -> None:
        """Start a synthetic monotonic clock at a nonzero origin."""
        self.value = 1_000_000_000

    def now(self) -> int:
        """Return the injected monotonic nanosecond counter."""
        return self.value

    def advance(self, milliseconds: int) -> None:
        """Charge one known validation, storage or transport interval."""
        self.value += milliseconds * 1_000_000


class FakeController:
    """Retain calls and return qualified synthetic generic-verifier receipts."""

    def __init__(self, view: dict[str, Any], clock: Clock) -> None:
        """Keep isolated mutable test projections and controllable transport failure modes."""
        self.view, self.clock = deepcopy(view), clock
        self.read_delay = 0
        self.calls: list[tuple[dict[str, Any], int]] = []
        self.reads = 0
        self.fail = False
        self.authority = "observer:test"
        self.store: ArrivalVerifierStore | None = None

    def arrival_view(self, mission_id: str, *, timeout_ms: int) -> dict[str, Any]:
        """Return the fixed original projections and charge injected read latency."""
        self.reads += 1
        self.clock.advance(self.read_delay)
        return deepcopy(self.view)

    def publish_evidence(self, evidence: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        """Assert durable before-send state and optionally simulate a lost response."""
        assert self.store is not None
        saved = self.store.get(evidence["evidence_id"])
        assert saved is not None and saved["status"] == "publishing"
        assert saved["evidence"] == evidence
        self.calls.append((deepcopy(evidence), timeout_ms))
        if self.fail:
            raise OSError("simulated_response_lost")
        return {
            "schema_version": "roboguide.semantic-verifier-receipt/v0.1",
            "receipt": {
                "evidence": deepcopy(evidence),
                "authority_subject": self.authority,
                "input_digest": digest(evidence),
                "registry_revision": 1,
                "received_at_ms": 1000,
                "valid_until_ms": 1000 + evidence["valid_for_ms"],
                "applied": True,
                "incident": None,
            },
        }


class ArrivalVerifierTests(unittest.TestCase):
    """Exercise exact binding, durable idempotence, uncertain delivery and time budgeting."""

    def setUp(self) -> None:
        """Create an isolated journal and independent synthetic Controller projections."""
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "arrival.sqlite"
        self.config, self.raw, view = fixture()
        self.clock = Clock()
        self.controller = FakeController(view, self.clock)
        self.store = ArrivalVerifierStore(self.path)
        self.controller.store = self.store
        self.publisher = ArrivalVerifier(
            self.config,
            self.controller,
            self.store,
            wall_clock_ms=self.wall,
            monotonic_ns=self.clock.now,
        )
        self.confirmation = {
            "schema_version": CONFIRMATION_SCHEMA,
            "observation_id": "observation-test",
            "observer_id": "human:test",
            "verdict": "arrived-and-stopped",
            **self.publisher.inspect(self.raw, "mission-test", "navigate"),
        }

    def tearDown(self) -> None:
        """Remove only the temporary software test directory."""
        self.temporary.cleanup()

    def wall(self) -> int:
        """Represent local report reception independently of Controller clock values."""
        return 1700000000000

    def test_success_freezes_sequence_and_returns_original_receipt_after_task_completion(
        self,
    ) -> None:
        """Exact repeats return saved receipts without reading changed Tasks or refreshing time."""
        first = self.publisher.publish(self.raw, self.confirmation)
        self.assertEqual(first["status"], "receipted")
        self.assertEqual(first["evidence"]["sequence"], 1)
        self.assertEqual(first["evidence"]["source_observed_at_ms"], self.wall())
        reads = self.controller.reads
        self.controller.view["mission"]["tasks"][0]["status"] = "Satisfied"
        self.clock.advance(9000)
        self.assertEqual(self.publisher.publish(self.raw, self.confirmation), first)
        self.assertEqual(self.controller.reads, reads)
        self.assertEqual(len(self.controller.calls), 1)

    def test_unknown_is_local_only_and_does_not_consume_source_sequence(self) -> None:
        """An uncertain human observation cannot produce satisfied evidence or release resources."""
        unknown = {**self.confirmation, "verdict": "unknown"}
        result = self.publisher.publish(self.raw, unknown)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["evidence"])
        self.assertEqual(result["report_received_at_ms"], self.wall())
        self.assertEqual(self.controller.calls, [])
        positive = {**self.confirmation, "observation_id": "fresh-observation"}
        self.assertEqual(self.publisher.publish(self.raw, positive)["evidence"]["sequence"], 1)

    def test_lost_reply_and_restart_never_resend(self) -> None:
        """A process restart cannot reinterpret a pending durable publication as safe to retry."""
        self.controller.fail = True
        with self.assertRaises(OSError):
            self.publisher.publish(self.raw, self.confirmation)
        restarted = ArrivalVerifier(
            self.config,
            self.controller,
            ArrivalVerifierStore(self.path),
            wall_clock_ms=self.wall,
            monotonic_ns=self.clock.now,
        )
        with self.assertRaisesRegex(ArrivalVerificationError, "delivery_uncertain"):
            restarted.publish(self.raw, self.confirmation)
        self.assertEqual(len(self.controller.calls), 1)

    def test_wrong_authenticated_receipt_identity_is_uncertain(self) -> None:
        """A returned producer principal must match the separately provisioned source exactly."""
        self.controller.authority = "observer:other"
        with self.assertRaisesRegex(ArrivalVerificationError, "unqualified_verifier_receipt"):
            self.publisher.publish(self.raw, self.confirmation)
        self.assertEqual(self.store.get("observation-test")["status"], "publishing")  # type: ignore[index]

    def test_changed_input_under_same_observation_is_rejected(self) -> None:
        """An observation identity cannot be reused for another observer or different fact."""
        self.publisher.publish(self.raw, self.confirmation)
        with self.assertRaisesRegex(ArrivalVerificationError, "conflicts"):
            self.publisher.publish(self.raw, {**self.confirmation, "observer_id": "human:other"})

    def test_original_wire_bytes_are_not_reencoded_for_admission_digest(self) -> None:
        """Even semantically equal whitespace changes fail the original admission binding."""
        with self.assertRaisesRegex(ArrivalVerificationError, "admission"):
            self.publisher.publish(self.raw + b"\n", self.confirmation)
        self.assertEqual(self.controller.calls, [])

    def test_wrong_confirmation_binding_is_rejected(self) -> None:
        """No tenant, plan, entity, Node, exact predicate or role attempt may drift silently."""
        changes = {
            "tenant_id": "foreign",
            "plan_digest": "sha256:" + "9" * 64,
            "entity_id": "entity-other",
            "node_id": "other-dog",
            "predicate": "anywhere",
            "wire_plan_digest": "sha256:" + "8" * 64,
            "attempts": [{"role_id": "route", "attempt_id": "attempt-route"}],
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ArrivalVerificationError):
                self.publisher.publish(self.raw, {**self.confirmation, key: value})
        self.assertEqual(self.controller.calls, [])

    def test_attempt_projection_must_be_complete_current_and_locally_completed(self) -> None:
        """Missing, extra, unfinished, foreign or changed-group attempts never pass."""
        original = deepcopy(self.controller.view["attempts"])
        cases = [original[:-1], [*original, deepcopy(original[0])]]
        for field, value in (
            ("status", "Executing"),
            ("node_id", "other-dog"),
            ("group_id", "other-group"),
            ("mission_id", "other-mission"),
        ):
            changed = deepcopy(original)
            changed[0][field] = value
            cases.append(changed)
        for attempts in cases:
            self.controller.view["attempts"] = attempts
            with self.subTest(attempts=attempts), self.assertRaises(ArrivalVerificationError):
                self.publisher.publish(self.raw, self.confirmation)
        self.assertEqual(self.controller.calls, [])

    def test_plan_revision_and_pending_satisfaction_are_required(self) -> None:
        """A replan or finalized Task cannot receive newly manufactured arrival evidence."""
        self.controller.view["mission"]["plan_revision"] = 1
        with self.assertRaises(ArrivalVerificationError):
            self.publisher.publish(self.raw, self.confirmation)
        self.controller.view["mission"]["plan_revision"] = 0
        self.controller.view["mission"]["tasks"][0]["status"] = "Completed"
        with self.assertRaises(ArrivalVerificationError):
            self.publisher.publish(self.raw, self.confirmation)

    def test_shorter_task_age_is_rejected_before_publication(self) -> None:
        """The fixed first-version publisher does not accept a different Task freshness contract."""
        plan = json.loads(self.raw)
        plan["tasks"][0]["satisfaction"]["verifier"]["max_evidence_age_ms"] = 100
        raw = encode(plan).encode()
        self.controller.view["admission"]["accepted_request_body_sha256"] = (
            "sha256:" + hashlib.sha256(raw).hexdigest()
        )
        with self.assertRaisesRegex(ArrivalVerificationError, "pending_exact_arrival"):
            self.publisher.publish(raw, self.confirmation)
        self.assertEqual(self.controller.calls, [])

    def test_fixed_actor_identity_cannot_drift_even_with_matching_wire_hash(self) -> None:
        """A different physical body is rejected before publishing for the configured target."""
        plan = json.loads(self.raw)
        plan["mission"]["actors"][0]["physical_entity"] = "entity-other"
        raw = encode(plan).encode()
        self.controller.view["admission"]["accepted_request_body_sha256"] = (
            "sha256:" + hashlib.sha256(raw).hexdigest()
        )
        with self.assertRaisesRegex(ArrivalVerificationError, "fixed_physical_actor"):
            self.publisher.publish(raw, self.confirmation)

    def test_expired_report_is_not_published(self) -> None:
        """Five seconds spent reading original views cannot be hidden by a new source timestamp."""
        self.controller.read_delay = 5000
        with self.assertRaisesRegex(ArrivalVerificationError, "budget_expired"):
            self.publisher.publish(self.raw, self.confirmation)
        self.assertEqual(self.controller.calls, [])

    def test_validation_and_journal_delay_are_deducted_before_send(self) -> None:
        """Frozen validity plus the allowed send deadline never exceed the original five seconds."""
        self.controller.read_delay = 1200
        original_prepare = self.store.prepare

        def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """Inject durable journal overhead after freezing the before-send evidence."""
            result = original_prepare(*args, **kwargs)
            self.clock.advance(250)
            return result

        with patch.object(self.store, "prepare", side_effect=delayed):
            self.publisher.publish(self.raw, self.confirmation)
        evidence, timeout = self.controller.calls[0]
        self.assertEqual(1200 + 250 + evidence["valid_for_ms"] + timeout, 5000)
        self.assertEqual(evidence["source_observed_at_ms"], self.wall())

    def test_journal_delay_can_expire_frozen_evidence_without_sending(self) -> None:
        """A slow durable write cannot trigger an over-budget send or a later same-ID retry."""
        original_prepare = self.store.prepare

        def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """Make the already durable claim consume all its reserved transport budget."""
            result = original_prepare(*args, **kwargs)
            self.clock.advance(3000)
            return result

        with patch.object(self.store, "prepare", side_effect=delayed):
            with self.assertRaisesRegex(ArrivalVerificationError, "budget_expired"):
                self.publisher.publish(self.raw, self.confirmation)
        self.assertEqual(self.store.get("observation-test")["status"], "expired")  # type: ignore[index]
        self.assertEqual(self.controller.calls, [])

    def test_clock_rollback_refuses_publication(self) -> None:
        """A backwards monotonic clock cannot be treated as additional freshness."""
        self.controller.read_delay = -1
        with self.assertRaisesRegex(ArrivalVerificationError, "budget_expired"):
            self.publisher.publish(self.raw, self.confirmation)

    def test_config_rejects_wildcard_wrong_region_digest_and_duplicate_predicates(self) -> None:
        """Source scope must describe one exact tenant and original target documents."""
        wrong_tenant, wrong_target, duplicate = (deepcopy(self.config) for _ in range(3))
        wrong_tenant["tenant_id"] = "*"
        wrong_target["targets"][0]["region_source_digest"] = "sha256:" + "3" * 64
        duplicate["targets"].append(deepcopy(duplicate["targets"][0]))
        for config in (wrong_tenant, wrong_target, duplicate):
            with self.subTest(config=config), self.assertRaises(ArrivalVerificationError):
                validate_config(config)

    def test_store_corruption_and_unknown_generation_fail_closed(self) -> None:
        """Local durability checks detect altered source inputs and unrecognized journal formats."""
        self.publisher.publish(self.raw, self.confirmation)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE arrival_reports SET original_json='{}'")
        with self.assertRaisesRegex(ArrivalVerificationError, "integrity"):
            self.store.get("observation-test")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE arrival_format SET schema='future'")
        with self.assertRaisesRegex(ArrivalVerificationError, "generation"):
            ArrivalVerifierStore(self.path)

    def test_inspect_has_no_storage_or_write_side_effect(self) -> None:
        """Readonly context inspection needs no journal and never publishes or grants actions."""
        inspector = ArrivalVerifier(self.config, self.controller, None)
        self.assertEqual(
            inspector.inspect(self.raw, "mission-test", "navigate")["attempts"],
            self.confirmation["attempts"],
        )
        self.assertEqual(self.controller.calls, [])

    def test_strict_json_and_cli_do_not_accept_alternative_authority_routes(self) -> None:
        """Reject duplicate fields and redirects; expose only inspect and publish operations."""
        with self.assertRaises(ArrivalVerificationError):
            _json(b'{"a":1,"a":2}')
        with self.assertRaises(ArrivalVerificationError):
            HttpArrivalController(
                "http://localhost", ca=Path("x"), certificate=Path("y"), private_key=Path("z")
            )
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            main(["register"])

    def test_transport_has_length_and_retains_socket_after_close_header(
        self,
    ) -> None:
        """The actual Controller requires Content-Length and closes each response connection."""
        controller = object.__new__(HttpArrivalController)
        controller.hostname, controller.port = "synthetic", 443
        controller.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        connection, response, transport_socket = Mock(), Mock(), Mock()
        connection.sock = transport_socket
        response.status = 200
        response.isclosed.return_value = False
        response.read1.side_effect = [b'{"value":"synthetic"}', b""]

        def getresponse() -> Any:
            """Simulate http.client detaching a socket on the Controller's Connection: close."""
            connection.sock = None
            return response

        connection.getresponse.side_effect = getresponse
        with patch("http.client.HTTPSConnection", return_value=connection):
            self.assertEqual(
                controller._request("/v1/missions/mission-test", timeout_ms=5000),
                {"value": "synthetic"},
            )
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Content-Length"], "0")
        connection.close.assert_called_once()
        self.assertGreater(transport_socket.settimeout.call_count, 2)

    def test_transport_refuses_redirect_and_shared_deadline_expiry(self) -> None:
        """Response status and total read time never create implicit follow-up requests."""
        controller = object.__new__(HttpArrivalController)
        controller.hostname, controller.port = "synthetic", 443
        controller.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        connection, response = Mock(), Mock()
        connection.getresponse.return_value = response
        response.status = 302
        with patch("http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(ArrivalVerificationError, "response_status"):
                controller._request("/v1/missions/mission-test", timeout_ms=5000)
        self.assertEqual(connection.request.call_count, 1)

        def delayed(_limit: int) -> bytes:
            """Simulate a response chunk arriving after the original total deadline."""
            self.clock.advance(5001)
            return b"{}"

        response.status, response.read1.side_effect = 200, delayed
        response.isclosed.return_value = False
        with (
            patch("http.client.HTTPSConnection", return_value=connection),
            patch("time.monotonic_ns", side_effect=self.clock.now),
        ):
            with self.assertRaisesRegex(ArrivalVerificationError, "transport_budget_expired"):
                controller._request("/v1/missions/mission-test", timeout_ms=5000)


if __name__ == "__main__":
    unittest.main()
