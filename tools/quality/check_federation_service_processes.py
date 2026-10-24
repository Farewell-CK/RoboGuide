"""Verify two real Federation participant services without creating Node facts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from check_identity_processes import client, pki, ports, request, stop

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "roboguide.federation-service/v0.2"


class Participant:
    """Own one actual Controller process and independent declared storage/authorization files."""

    def __init__(
        self,
        directory: Path,
        binary: Path,
        domain: str,
        credentials: Path,
        digests: dict[str, str],
        listener_ports: list[int],
    ) -> None:
        """Create only software deployment files, never Node registrations or device inventories."""
        self.root = directory / domain
        self.root.mkdir()
        self.binary = binary
        self.domain = domain
        self.database = self.root / "events.sqlite3"
        grpc_port, self.http_port, artifact_port = listener_ports
        self.origin = f"https://127.0.0.1:{self.http_port}"
        self.contexts = {
            name: client(credentials, name)
            for name in ("admin", "admin-next", "viewer", "enrolment-first")
        }
        grants = {
            "sha256:" + digests[name]: {
                "principal_id": principal,
                "role": role,
                "tenant_ids": tenants,
            }
            for name, principal, role, tenants in (
                ("admin", "federation-admin", "recovery-admin", ["*"]),
                ("admin-next", "federation-admin", "recovery-admin", ["*"]),
                ("viewer", "federation-viewer", "viewer", ["tenant-a"]),
                ("enrolment-first", "another-admin", "recovery-admin", ["*"]),
            )
        }
        self.operator = self.root / "operator.json"
        save(
            self.operator,
            {
                "schema_version": "roboguide.operator-authorization/v0.1",
                "registry_id": "operator-" + domain,
                "revision": 1,
                "grants": grants,
            },
        )
        self.policy: dict[str, Any] = {
            "schema_version": "roboguide.federation-authorization/v0.1",
            "registry_id": "federation-" + domain,
            "revision": 1,
            "local_domain": domain,
            "domain_epochs": {"domain-a": 7, "domain-b": 11},
            "grants": {
                "sha256:" + digests[name]: {
                    "principal_id": principal,
                    "tenant_ids": ["tenant-a"],
                    "domain_ids": ["domain-a", "domain-b"],
                    "revoked": False,
                }
                for name, principal in (
                    ("admin", "federation-admin"),
                    ("admin-next", "federation-admin"),
                    ("enrolment-first", "another-admin"),
                )
            },
        }
        self.policy_path = self.root / "federation.json"
        save(self.policy_path, self.policy)
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        }
        self.environment.update(
            {
                "TOKIO_WORKER_THREADS": "1",
                "ROBOGUIDE_CONTROL_DOMAIN": domain,
                "ROBOGUIDE_FEDERATION_DOMAIN_GRANTS": str(self.policy_path),
                "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(self.operator),
                **{
                    f"ROBOGUIDE_OPERATOR_MTLS_{key}": str(credentials / value)
                    for key, value in (
                        ("SERVER_CERT", "server.crt"),
                        ("SERVER_KEY", "server.key"),
                        ("CLIENT_CA", "ca.crt"),
                    )
                },
            }
        )
        self.arguments = [
            str(binary),
            f"127.0.0.1:{grpc_port}",
            str(self.database),
            f"127.0.0.1:{self.http_port}",
            f"127.0.0.1:{artifact_port}",
            str(self.root / "artifacts"),
        ]
        self.process: subprocess.Popen[bytes] | None = None
        self.logs: list[str] = []

    def start(self) -> None:
        """Start the original deployment and require its actual authenticated health response."""
        if self.process is not None:
            raise ValueError("participant is already owned")
        path = self.root / f"controller-{len(self.logs)}.log"
        self.logs.append(str(path))
        with path.open("wb") as log:
            self.process = subprocess.Popen(
                self.arguments, cwd=ROOT, env=self.environment, stdout=log, stderr=log
            )
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError(f"actual participant exited; inspect {path}")
            try:
                if self.call("/healthz", tenant=None)[0] == 200:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise TimeoutError(f"actual participant health unavailable; inspect {path}")

    def stop(self) -> None:
        """Wait for only this original owned process; SQL inspection never invents process exit."""
        if self.process is not None:
            stop(self.process)
            self.process = None

    def call(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        identity: str = "admin",
        tenant: str | None = "tenant-a",
    ) -> tuple[int, dict[str, Any]]:
        """Use actual mTLS and the exact tenant selector without asserted role headers."""
        result = request(
            self.origin,
            self.contexts[identity],
            path,
            body,
            {"X-RoboGuide-Tenant": tenant} if tenant is not None else {},
        )
        trace = {
            "path": path,
            "identity": identity,
            "tenant": tenant,
            "request": body,
            "status": result[0],
            "response": result[1],
        }
        with (self.root / "http-trace.jsonl").open("a") as output:
            output.write(json.dumps(trace) + "\n")
        return result

    def observe(self) -> dict[str, Any]:
        """Read committed SQL independently of HTTP responses and return flags."""
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as sql:
            row = sql.execute(
                "SELECT schema,checkpoint_json FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
            commands = sql.execute(
                "SELECT command_id,causation_id FROM control_command_journal "
                "WHERE command_id LIKE 'federation:%' ORDER BY authority_revision"
            ).fetchall()
            policies = sql.execute(
                "SELECT COUNT(*) FROM control_command_journal "
                "WHERE command_id LIKE 'federation-policy:%'"
            ).fetchone()
        if row is None or policies is None:
            raise ValueError("actual participant checkpoint is absent")
        wrapper = json.loads(row[1])
        integration = json.loads(wrapper["integration_json"])
        if integration["nodes"] or integration["control"]["reservations"]:
            raise AssertionError("the software gate created Node or resource authority")
        return {
            "schema": row[0],
            "state": wrapper["federation"],
            "sessions": integration["control"]["federation_sessions"],
            "commands": commands,
            "policy_commands": policies[0],
        }


def save(path: Path, value: Any) -> None:
    """Retain bounded evidence without publishing private key material."""
    path.write_text(json.dumps(value, indent=2) + "\n")


def prepare(identity: str, duration_ms: int = 120_000) -> dict[str, Any]:
    """Create explicit protocol input; digests are declared software values, never Mission facts."""
    return {
        "schema_version": SCHEMA,
        "session": {
            "transaction_id": identity,
            "source_domain": "domain-a",
            "target_domain": "domain-b",
            "tenant_id": "tenant-a",
            "source_epoch": 7,
            "plan_digest": "sha256:" + "a" * 64,
            "ownership_digest": "sha256:" + "b" * 64,
            "deadline": int(time.time() * 1000) + duration_ms,
            "state": "prepared",
        },
    }


def commit() -> dict[str, Any]:
    """Repeat the original declared semantic identity without extending any receive deadline."""
    return {
        "schema_version": SCHEMA,
        "source_epoch": 7,
        "plan_digest": "sha256:" + "a" * 64,
        "ownership_digest": "sha256:" + "b" * 64,
    }


def drop_response(participant: Participant, body: dict[str, Any]) -> None:
    """Close actual TLS after sending the full request, before reading its response."""
    payload = json.dumps(body).encode()
    endpoint = urlsplit(participant.origin)
    with socket.create_connection((endpoint.hostname or "", endpoint.port or 0), timeout=4) as raw:
        with participant.contexts["admin"].wrap_socket(raw, server_hostname="localhost") as stream:
            header = (
                "POST /v1/federation/sessions/prepare HTTP/1.1\r\nHost: localhost\r\n"
                "X-RoboGuide-Tenant: tenant-a\r\nContent-Length: "
                + str(len(payload))
                + "\r\nConnection: close\r\n\r\n"
            ).encode()
            stream.sendall(header + payload)
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if body["session"]["transaction_id"] in participant.observe()["state"]["records"]:
            return
        time.sleep(0.02)
    raise TimeoutError(
        "dropped response did not produce an independently observed original receipt"
    )


def old_writer_refusal(participant: Participant, old_binary: Path, output: Path) -> dict[str, Any]:
    """Try a real previous binary against a cold copied zero-event enrolled policy checkpoint."""
    participant.stop()
    copied = output / "old-writer-copy"
    shutil.copytree(participant.root, copied)
    database = copied / "events.sqlite3"
    before = sql_rows(database)
    before_bytes = hashlib.sha256(database.read_bytes()).hexdigest()
    grpc_port, http_port, artifact_port = ports(3)
    arguments = [
        str(old_binary),
        f"127.0.0.1:{grpc_port}",
        str(database),
        f"127.0.0.1:{http_port}",
        f"127.0.0.1:{artifact_port}",
        str(copied / "artifacts"),
    ]
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=participant.environment,
        capture_output=True,
        timeout=10,
        check=False,
    )
    text = completed.stderr.decode(errors="replace")
    if (
        completed.returncode == 0
        or "unsupported checkpoint schema" not in text
        or "/v54" not in text
    ):
        raise AssertionError(
            "old participant binary did not refuse the actual v54 checkpoint: " + text
        )
    if sql_rows(database) != before:
        raise AssertionError("old writer changed the original copied authority")
    participant.start()
    return {
        "binary_sha256": hashlib.sha256(old_binary.read_bytes()).hexdigest(),
        "exit_code": completed.returncode,
        "authority_rows_unchanged": True,
        "main_file_bytes_unchanged": hashlib.sha256(database.read_bytes()).hexdigest()
        == before_bytes,
        "enrolled_without_sessions": True,
    }


def sql_rows(database: Path) -> list[str]:
    """Inspect the complete WAL-aware SQL image, independent of SQLite page checkpointing."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
        return list(sql.iterdump())


def source_identity() -> dict[str, str]:
    """Freeze the actual maintained Rust inputs and this checker without including credentials."""
    paths = {
        ROOT / "Cargo.toml",
        ROOT / "Cargo.lock",
        Path(__file__).resolve(),
        Path(__file__).with_name("check_identity_processes.py").resolve(),
    }
    for directory in (ROOT / "core", ROOT / "apps/integration-server"):
        paths.update(directory.rglob("*.rs"))
        paths.update(directory.rglob("Cargo.toml"))
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def faults(a: Participant, b: Participant, output: Path, digest: str) -> dict[str, Any]:
    """Exercise real retry, rollback, disconnect, deadline, policy and ownership fences."""
    base = "/v1/federation/sessions/"
    # Lost HTTP response is reconciled from independently inspected SQL, not a second effect.
    lost = prepare("response-lost")
    drop_response(a, lost)
    first = a.call(base + "prepare", lost)
    assert first[0] == 202, first
    count = len(a.observe()["commands"])
    assert a.call(base + "prepare", lost) == first
    assert len(a.observe()["commands"]) == count
    peer_input = prepare("two-participants")
    for participant in (a, b):
        result = participant.call(base + "prepare", peer_input)
        assert result[0] == 202, result
    b.stop()
    local = a.call(base + "two-participants/commit", commit())
    assert local[0] == 202 and local[1]["global_dispatch_ready"] is False, local
    observed = a.call(base + "two-participants")[1]
    assert observed["local_commit_current"] is True and observed["global_dispatch_ready"] is False
    try:
        b.call(base + "two-participants/commit", commit())
    except (OSError, urllib.error.URLError):
        partition_observed = True
    else:
        raise AssertionError("stopped actual participant unexpectedly accepted a request")
    b.start()
    assert b.call(base + "two-participants")[1]["session"]["state"] == "prepared"
    final = b.call(base + "two-participants/commit", commit())
    assert final[0] == 202, final
    for participant in (a, b):
        assert participant.call(base + "two-participants/commit", commit())[0] == 202
        assert participant.call(base + "prepare", peer_input)[0] == 202
        assert (
            participant.call(base + "two-participants/commit", dict(commit(), source_epoch=8))[0]
            == 409
        )
        assert (
            participant.call(
                base + "two-participants/commit", dict(commit(), plan_digest="sha256:" + "c" * 64)
            )[0]
            == 409
        )
        assert (
            participant.call(
                base + "two-participants/commit", commit(), identity="enrolment-first"
            )[0]
            == 409
        )
        assert participant.call(base + "two-participants", identity="viewer")[0] == 403
        assert participant.call(base + "two-participants", tenant="tenant-b")[0] == 409
        assert (
            participant.call(base + "two-participants/commit", commit(), tenant="tenant-a")[1][
                "global_dispatch_ready"
            ]
            is False
        )
    assert a.call(base + "prepare", lost, identity="admin-next") == first
    invalid = prepare("invalid-phase")
    invalid["session"]["state"] = "committed"
    assert a.call(base + "prepare", invalid)[0] == 409
    wrong_pair = prepare("wrong-pair")
    wrong_pair["session"]["target_domain"] = "unconfigured-domain"
    assert a.call(base + "prepare", wrong_pair)[0] == 409
    assert a.call(base + "prepare", prepare("overlong-budget", 600_000))[0] == 409
    aborted = prepare("aborted")
    assert a.call(base + "prepare", aborted)[0] == 202
    abort = {"schema_version": SCHEMA}
    abort_receipt = a.call(base + "aborted/abort", abort)
    assert abort_receipt[0] == 202
    assert a.call(base + "aborted/abort", abort) == abort_receipt
    assert a.call(base + "aborted/commit", commit())[0] == 409
    expired = prepare("expired", 400)
    expiry_receipt = a.call(base + "prepare", expired)
    assert expiry_receipt[0] == 202
    end = time.monotonic() + 2
    while int(time.time() * 1000) <= expired["session"]["deadline"] and time.monotonic() < end:
        time.sleep(0.02)
    assert a.call(base + "expired/commit", commit())[0] == 409
    assert a.call(base + "prepare", expired) == expiry_receipt
    before = a.observe()
    with closing(sqlite3.connect(a.database)) as sql:
        sql.execute(
            "CREATE TRIGGER refuse_federation BEFORE INSERT ON control_command_journal "
            "WHEN NEW.command_id LIKE 'federation:%' "
            "BEGIN SELECT RAISE(ABORT,'original Federation SQL fault'); END"
        )
        sql.commit()
    failed = prepare("actual-sql-rollback")
    result = a.call(base + "prepare", failed)
    assert result[0] == 503, result
    after = a.observe()
    assert after == before, (before, after)
    with closing(sqlite3.connect(a.database)) as sql:
        sql.execute("DROP TRIGGER refuse_federation")
        sql.commit()
    status, authority = a.call("/v1/authority-status", tenant=None)
    assert status == 200 and authority["write_enabled"] is False
    recovery = {
        "schema_version": "roboguide.authority-write-recovery/v0.1",
        "command_id": "federation-sql-recovery",
        **{
            key: authority[key]
            for key in ("process_id", "generation", "checkpoint_identity", "authority_revision")
        },
    }
    assert a.call("/v1/authority/recover", recovery, tenant=None)[0] == 200
    assert a.call(base + "prepare", failed)[0] == 202
    a.stop()
    retained = a.observe()
    a.start()
    assert a.observe() == retained
    assert a.call(base + "prepare", lost) == first
    assert a.call(base + "expired/commit", commit())[0] == 409
    policy_original = copy.deepcopy(a.policy)
    a.policy["revision"] = 2
    a.policy["grants"]["sha256:" + digest]["revoked"] = True
    save(a.policy_path, a.policy)
    assert a.call(base + "response-lost")[0] == 409
    assert len(a.observe()["state"]["policies"]) == 2
    save(a.policy_path, policy_original)
    assert a.call(base + "response-lost", identity="admin-next")[0] == 409
    save(a.policy_path, a.policy)
    a.stop()
    a.start()
    assert a.call(base + "response-lost")[0] == 409
    # The rotated leaf may observe original receipts.
    # A changed policy cannot create a new Commit.
    assert a.call(base + "prepare", lost, identity="admin-next") == first
    assert a.call(base + "response-lost/commit", commit(), identity="admin-next")[0] == 409
    save(output / "participant-a-sql.json", a.observe())
    save(output / "participant-b-sql.json", b.observe())
    return {
        "response_loss_reconciled": True,
        "original_receipts_reused": True,
        "actual_sql_rollback_and_recovery": True,
        "partition_observed": partition_observed,
        "expired_budget_not_renewed": True,
        "policy_rollback_refused": True,
        "revocation_survives_restart": True,
        "local_commit_is_not_global": True,
    }


def run(
    output: Path, skip_build: bool, binary: Path | None, old_binary: Path | None
) -> dict[str, Any]:
    """Run the bounded actual participant matrix with preserved failure evidence."""
    output.mkdir(parents=True, exist_ok=False)
    original_source = source_identity()
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
            timeout=240,
        )
    binary = (binary or ROOT / "target/debug/integration-server").resolve()
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    certificates = output / "pki"
    digests = pki(certificates)
    listeners = ports(6)
    a = Participant(output, binary, "domain-a", certificates, digests, listeners[:3])
    b = Participant(output, binary, "domain-b", certificates, digests, listeners[3:])
    try:
        a.start()
        b.start()
        old = old_writer_refusal(a, old_binary.resolve(), output) if old_binary else None
        proof = faults(a, b, output, digests["admin"])
        for participant in (a, b):
            assert participant.call("/v1/inventory")[1]["nodes"] == []
            assert participant.observe()["schema"] == "roboguide.controller-checkpoint/v54"
        if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_hash:
            raise ValueError("actual Controller binary changed during acceptance")
        if source_identity() != original_source:
            raise ValueError("maintained source changed during Federation acceptance")
        save(output / "source-identity.json", original_source)
        return {
            "passed": True,
            "schema": SCHEMA,
            "binary_sha256": binary_hash,
            "application_schema": "roboguide.controller-checkpoint/v54",
            "registered_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "proof": proof,
            "old_writer": old,
            "global_dispatch_ready": False,
            "coordinator": "not_implemented",
            "cross_host_evidence": "unavailable",
            "hardware_evidence": "HardwareEvidencePending",
            "logs": a.logs + b.logs,
        }
    finally:
        a.stop()
        b.stop()


def main() -> None:
    """Save the actual software receipt with independent SQL and evidence limitations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--controller-binary", type=Path)
    parser.add_argument("--old-controller-binary", type=Path)
    args = parser.parse_args()
    if args.controller_binary is not None and not args.skip_build:
        parser.error("--controller-binary requires --skip-build")
    result = run(
        args.output.resolve(), args.skip_build, args.controller_binary, args.old_controller_binary
    )
    save(args.output / "result.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
