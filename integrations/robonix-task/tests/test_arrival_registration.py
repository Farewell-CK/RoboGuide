"""Exercise durable one-shot enrollment recovery without network or robot access."""

from __future__ import annotations

import importlib.util
import ssl
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import jsonschema

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/register_arrival_verifier.py"
SPEC = importlib.util.spec_from_file_location("register_arrival_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)
ADMIN = "sha256:" + "a" * 64
LEAF = "sha256:" + "b" * 64


def profile() -> dict[str, Any]:
    """Return synthetic versioned historical grants whose exact fields must survive."""
    return {
        "schema_version": "roboguide.operator-authorization/v0.2",
        "registry_id": "original-operators",
        "revision": 7,
        "grants": {
            ADMIN: {
                "principal_id": "administrator",
                "role": "recovery-admin",
                "tenant_ids": ["*"],
                "expires_at_ms": 0,
            }
        },
    }


def config() -> dict[str, Any]:
    """Describe one synthetic source; no target or observation is field evidence."""
    return {
        "source_id": "synthetic-onsite",
        "revision": 1,
        "tenant_id": "local",
        "producer_principal": "onsite-verifier",
        "verifier": {"namespace": "observation.patrol", "name": "verify-arrival", "version": "v1"},
        "targets": [{"predicate": "synthetic exact target and physical entity"}],
    }


class FakeController:
    """Retain original command receipts while injecting an explicit lost first reply."""

    def __init__(self) -> None:
        """Create isolated software state with no robot, source or task history."""
        self.registry: Any = None
        self.receipts: dict[str, dict[str, Any]] = {}
        self.posts: list[bytes] = []
        self.calls: list[tuple[bool, str, str]] = []
        self.unknown = False
        self.lose_reply = False
        self.bad_permission = False

    def request(
        self,
        administrator: bool,
        method: str,
        path: str,
        body: bytes | None,
    ) -> tuple[int, dict[str, Any]]:
        """Model only the documented generic registry and restricted read probes."""
        self.calls.append((administrator, method, path))
        if not administrator and path in {"/v1/missions", "/v1/verifier-sources"}:
            return (200, {}) if self.bad_permission else (403, {"error": "forbidden"})
        if method == "POST":
            assert path == "/v1/verifier-sources" and body is not None
            self.posts.append(body)
            command = registration.decode(body)
            command_id = command["command_id"]
            if command_id not in self.receipts:
                self.registry = deepcopy(command["registry"])
                self.receipts[command_id] = {
                    "schema_version": "roboguide.registry-command-receipt/v0.1",
                    "receipt": {
                        "command_id": command_id,
                        "kind": "semantic_verifier",
                        "input_digest": registration.digest(registration.encode(command)),
                        "registry_digest": registration.digest(registration.encode(self.registry)),
                        "revision": self.registry["revision"],
                        "authority_subject": "administrator",
                        "accepted_at_ms": 1234,
                    },
                }
            if self.lose_reply:
                self.lose_reply = False
                raise OSError("injected lost reply after commit")
            return 200, deepcopy(self.receipts[command_id])
        if path == "/readyz":
            return 200, {
                "ready": True,
                "degraded": self.unknown,
                "unknown_attempts": 1 if self.unknown else 0,
            }
        if path == "/v1/resource-allocations":
            return 200, {"allocations": []}
        if path == "/v1/inventory":
            return 200, {"schema_version": "roboguide.inventory/v0.1", "nodes": []}
        if path == "/v1/verifier-sources":
            return 200, {
                "schema_version": "roboguide.semantic-verifier-view/v0.1",
                "registry": deepcopy(self.registry),
            }
        raise AssertionError("unexpected enrollment route")


class FakeClient:
    """Substitute deterministic software exchange for the production HTTPS transport."""

    def __init__(self, controller: FakeController, *, administrator: bool) -> None:
        """Avoid all credential loading and retain only the fake authority selector."""
        self.controller = controller
        self.administrator = administrator

    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Deliver one software request without retry, proxy or external connection."""
        return self.controller.request(self.administrator, method, path, body)


class ArrivalRegistrationTests(unittest.TestCase):
    """Protect narrow authority, retained originals and interrupted enrollment recovery."""

    def test_leaf_digest_hashes_der_instead_of_pem_text(self) -> None:
        """PEM wrapping cannot change the enrolled X.509 leaf digest."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pem"
            raw = b"synthetic DER bytes only, not a signed certificate"
            path.write_text(ssl.DER_cert_to_PEM_cert(raw), encoding="ascii")
            self.assertEqual(registration.certificate_digest(path), registration.digest(raw))

    def test_operator_upgrade_retains_all_original_grants_and_reuses_leaf(self) -> None:
        """Only the new verifier grant changes while repeat enrollment retains revision eight."""
        original = profile()
        upgraded = registration.prepare_operator_profile(original, LEAF, config())
        self.assertEqual(upgraded["registry_id"], original["registry_id"])
        self.assertEqual(upgraded["revision"], 8)
        self.assertEqual(upgraded["grants"][ADMIN], original["grants"][ADMIN])
        self.assertEqual(upgraded["schema_version"], "roboguide.operator-authorization/v0.3")
        self.assertEqual(registration.prepare_operator_profile(upgraded, LEAF, config()), upgraded)
        self.assertEqual(original, profile())

    def test_rejects_wildcards_and_existing_nonverifier_identity(self) -> None:
        """An old administrator/operator leaf or principal cannot be relabeled as independent."""
        for field, value in (("tenant_id", "*"), ("producer_principal", "administrator")):
            changed = config()
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(registration.EnrollmentError):
                registration.prepare_operator_profile(profile(), LEAF, changed)
        with self.assertRaises(registration.EnrollmentError):
            registration.prepare_operator_profile(profile(), ADMIN, config())

    def test_command_preserves_other_sources_and_matches_canonical_schema(self) -> None:
        """One new source advances revision without rewriting a disabled historical producer."""
        source = registration.expected_source(config())
        other = {**source, "source_id": "old", "enabled": False}
        registry = {
            "schema_version": "roboguide.semantic-verifier-registry/v0.1",
            "registry_id": "original-sources",
            "revision": 4,
            "sources": {"old": other},
        }
        command = registration.prepare_command(registry, config())
        self.assertEqual(command["expected_revision"], 4)
        self.assertEqual(command["registry"]["revision"], 5)
        self.assertEqual(command["registry"]["sources"]["old"], other)
        schema = registration.decode(
            registration.read_bytes(
                SCRIPT.parents[3]
                / "contracts/state/authority-v0.1/verifier-source-command.schema.json"
            )
        )
        jsonschema.validate(command, schema)

    def test_existing_source_mismatch_or_ambiguous_predicate_is_rejected(self) -> None:
        """Registration does not alter a source contract or introduce competing predicate owners."""
        command = registration.prepare_command(None, config())
        for mutation in ("same-source", "ambiguous"):
            registry = deepcopy(command["registry"])
            if mutation == "same-source":
                registry["sources"]["synthetic-onsite"]["max_age_ms"] = 10000
            else:
                registry["sources"]["other"] = {
                    **registration.expected_source(config()),
                    "source_id": "other",
                }
            with self.subTest(mutation=mutation), self.assertRaises(registration.EnrollmentError):
                registration.prepare_command(registry, config())

    def test_transport_has_no_mission_cancel_stop_or_evidence_write(self) -> None:
        """The production transport rejects actuator-related paths before opening a connection."""
        for administrator in (False, True):
            client = object.__new__(registration.HttpsClient)
            client.administrator = administrator
            for path in (
                "/v1/missions",
                "/v1/missions/example/cancel",
                "/v1/executions/example/safety-stop",
                "/v1/verifier-evidence",
            ):
                with self.subTest(administrator=administrator, path=path):
                    with self.assertRaises(registration.EnrollmentError):
                        client.request("POST", path, b"{}")

    def test_transport_preserves_global_admin_and_scoped_verifier_headers(self) -> None:
        """Global registry administration omits tenant selection; verifier reads select local."""
        for administrator in (False, True):
            with self.subTest(administrator=administrator):
                client = object.__new__(registration.HttpsClient)
                client.host = "controller.example"
                client.port = 8443
                client.tenant = "local"
                client.administrator = administrator
                client.context = ssl.create_default_context()
                response = Mock(status=200)
                response.read.return_value = b"{}"
                connection = Mock()
                connection.getresponse.return_value = response
                with patch.object(
                    registration.http.client, "HTTPSConnection", return_value=connection
                ):
                    self.assertEqual(client.request("GET", "/v1/inventory"), (200, {}))
                headers = connection.request.call_args.kwargs["headers"]
                expected = {"Content-Type": "application/json"}
                if not administrator:
                    expected["X-RoboGuide-Tenant"] = "local"
                self.assertEqual(headers, expected)
                connection.request.assert_called_once()
                connection.close.assert_called_once()

    def test_lost_reply_recovery_reuses_original_command_and_receipt(self) -> None:
        """A committed-but-unacknowledged registry write resumes without a new generation."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operator = root / "operator.json"
            before = registration.encode(profile())
            operator.write_bytes(before)
            output = root / "enrollment"
            controller = FakeController()
            controller.lose_reply = True
            arguments = {
                "config": config(),
                "leaf": LEAF,
                "admin_leaf": ADMIN,
                "operator_path": operator,
                "output": output,
                "origin": "https://controller.example:8443",
                "admin": FakeClient(controller, administrator=True),
                "verifier": FakeClient(controller, administrator=False),
            }
            with self.assertRaisesRegex(OSError, "lost reply"):
                registration.enroll(**arguments)
            command_raw = (output / "verifier-source-command.json").read_bytes()
            self.assertEqual((output / "operator-before.json").read_bytes(), before)
            result = registration.enroll(**arguments)
            self.assertEqual(result["status"], "registered_permission_checked")
            self.assertEqual(controller.posts, [command_raw, command_raw])
            self.assertEqual(registration.decode(operator.read_bytes())["revision"], 8)
            original_receipt = (output / "verifier-source-receipt.json").read_bytes()
            self.assertEqual(registration.enroll(**arguments), result)
            self.assertEqual(
                (output / "verifier-source-receipt.json").read_bytes(), original_receipt
            )
            self.assertFalse(result["motion_sent"])
            self.assertFalse(result["verdict_published"])

    def test_unknown_precondition_preserves_operator_and_sends_no_command(self) -> None:
        """Unknown activity is a blocker rather than an empty-state assumption."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operator = root / "operator.json"
            before = registration.encode(profile())
            operator.write_bytes(before)
            controller = FakeController()
            controller.unknown = True
            with self.assertRaises(registration.EnrollmentError):
                registration.enroll(
                    config=config(),
                    leaf=LEAF,
                    admin_leaf=ADMIN,
                    operator_path=operator,
                    output=root / "enrollment",
                    origin="https://controller.example:8443",
                    admin=FakeClient(controller, administrator=True),
                    verifier=FakeClient(controller, administrator=False),
                )
            self.assertEqual(operator.read_bytes(), before)
            self.assertEqual(controller.posts, [])

    def test_overprivileged_probe_blocks_registry_publication(self) -> None:
        """Retain the backup and withhold source authority when restricted probes fail."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operator = root / "operator.json"
            operator.write_bytes(registration.encode(profile()))
            controller = FakeController()
            controller.bad_permission = True
            with self.assertRaises(registration.EnrollmentError):
                registration.enroll(
                    config=config(),
                    leaf=LEAF,
                    admin_leaf=ADMIN,
                    operator_path=operator,
                    output=root / "enrollment",
                    origin="https://controller.example:8443",
                    admin=FakeClient(controller, administrator=True),
                    verifier=FakeClient(controller, administrator=False),
                )
            self.assertTrue((root / "enrollment/operator-before.json").exists())
            self.assertEqual(controller.posts, [])


if __name__ == "__main__":
    unittest.main()
