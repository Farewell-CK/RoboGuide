"""Verify revisioned tenant quotas through the real mTLS Controller with zero Node facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def port() -> int:
    """Select one isolated local TCP port for an owned service listener."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def openssl(directory: Path, *arguments: str) -> None:
    """Run a bounded offline test PKI operation without printing private material."""
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def make_certificates(directory: Path) -> tuple[dict[str, str], ssl.SSLContext, ssl.SSLContext]:
    """Issue software-only mTLS leaves for two stable admin rotations and one operator."""
    directory.mkdir(mode=0o700)
    openssl(
        directory,
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-days",
        "1",
        "-subj",
        "/CN=offline-quota-test-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (directory / "extensions").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
    )
    digests: dict[str, str] = {}
    for name in ("server", "admin", "admin-next", "operator"):
        openssl(
            directory,
            "req",
            "-new",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            name + ".key",
            "-out",
            name + ".csr",
            "-subj",
            f"/CN={name}",
        )
        openssl(
            directory,
            "x509",
            "-req",
            "-in",
            name + ".csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            name + ".crt",
            "-days",
            "1",
            "-extfile",
            "extensions",
        )
        der = ssl.PEM_cert_to_DER_cert((directory / (name + ".crt")).read_text())
        digests[name] = hashlib.sha256(der).hexdigest()

    def context(name: str) -> ssl.SSLContext:
        """Build one TLS client context with the selected local operator leaf."""
        result = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        result.load_cert_chain(directory / f"{name}.crt", directory / f"{name}.key")
        return result

    return digests, context("admin"), context("operator")


def request(
    origin: str,
    context: ssl.SSLContext,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Use the public TLS HTTP surface and retain bounded JSON rejection responses."""
    payload = None if body is None else json.dumps(body).encode()
    outgoing = urllib.request.Request(
        origin + path,
        data=payload,
        headers=headers or {},
        method="GET" if body is None else "POST",
    )
    try:
        response = urllib.request.urlopen(outgoing, context=context, timeout=3)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Controller response exceeded its bound")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("Controller response is not an object")
        return response.status, value


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop only the owned Controller process and wait for its SQLite writer lock."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(output: Path, skip_build: bool, binary_path: Path | None) -> dict[str, Any]:
    """Exercise authenticated policy writes, SQL receipts, restore and no-Node topology."""
    output.mkdir(parents=True, exist_ok=True)
    directory = output / "owned-controller"
    directory.mkdir(mode=0o700)
    if binary_path is None:
        if not skip_build:
            subprocess.run(
                ["cargo", "build", "--locked", "-p", "integration-server"],
                cwd=ROOT,
                check=True,
                timeout=240,
            )
        binary = (ROOT / "target/debug/integration-server").resolve(strict=True)
    else:
        if not skip_build:
            raise ValueError("--controller-binary requires --skip-build and explicit binary bytes")
        binary = binary_path.resolve(strict=True)
    binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    certificates = directory / "pki"
    digests, admin, operator = make_certificates(certificates)
    grants = {
        "sha256:" + digests["admin"]: {
            "principal_id": "quota-admin",
            "role": "recovery-admin",
            "tenant_ids": ["*"],
        },
        "sha256:" + digests["admin-next"]: {
            "principal_id": "quota-admin",
            "role": "recovery-admin",
            "tenant_ids": ["*"],
        },
        "sha256:" + digests["operator"]: {
            "principal_id": "tenant-operator",
            "role": "operator",
            "tenant_ids": ["tenant-a"],
        },
    }
    role_map = directory / "operator.json"
    role_map.write_text(
        json.dumps(
            {
                "schema_version": "roboguide.operator-authorization/v0.1",
                "registry_id": "offline-quota-operator-policy",
                "revision": 1,
                "grants": grants,
            }
        )
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment.update(
        {
            "TOKIO_WORKER_THREADS": "4",
            "ROBOGUIDE_OPERATOR_CONNECTION_LIMIT": "32",
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(role_map),
        }
    )
    schema = (
        subprocess.check_output([str(binary), "--application-schema"], env=environment, timeout=5)
        .decode()
        .strip()
    )
    if schema not in {
        "roboguide.controller-checkpoint/v47",
        "roboguide.controller-checkpoint/v48",
        "roboguide.controller-checkpoint/v49",
        "roboguide.controller-checkpoint/v50",
        "roboguide.controller-checkpoint/v51",
        "roboguide.controller-checkpoint/v52",
    }:
        raise ValueError(f"unexpected Controller application schema {schema}")
    http_port, artifact_port, grpc_port = port(), port(), port()
    origin = f"https://127.0.0.1:{http_port}"
    database = directory / "events.sqlite3"
    args = [
        str(binary),
        f"127.0.0.1:{grpc_port}",
        str(database),
        f"127.0.0.1:{http_port}",
        f"127.0.0.1:{artifact_port}",
        str(directory / "artifacts"),
    ]
    log_path = output / "controller.log"
    process: subprocess.Popen[bytes] | None = None

    def start() -> subprocess.Popen[bytes]:
        """Start the exact binary with original SQLite and wait on authenticated health."""
        if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_digest:
            raise ValueError("Controller binary changed during the process check")
        with log_path.open("ab") as log:
            child = subprocess.Popen(args, cwd=ROOT, env=environment, stdout=log, stderr=log)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Controller exited; inspect {log_path}")
            try:
                if request(origin, admin, "/healthz")[0] == 200:
                    return child
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        stop(child)
        raise TimeoutError(f"Controller did not become healthy; inspect {log_path}")

    try:
        process = start()
        policy = {
            "command_id": "quota-policy-1",
            "expected_revision": 0,
            "registry": {
                "registry_id": "offline-process-quotas",
                "revision": 1,
                "limits": [{"tenant_id": "tenant-a", "kind": "Compute", "max_units": 12}],
            },
        }
        forged = request(
            origin,
            operator,
            "/v1/resource-quotas",
            policy,
            {"X-RoboGuide-Operator-Role": "recovery-admin"},
        )
        assert forged[0] == 401, "tenant operator forged a global recovery-admin grant"
        assert request(origin, operator, "/v1/resource-quotas", policy)[0] == 403
        first = request(origin, admin, "/v1/resource-quotas", policy)
        assert first[0] == 200 and first[1]["receipt"]["authority_subject"] == "quota-admin"
        admin_next = ssl.create_default_context(cafile=str(certificates / "ca.crt"))
        admin_next.load_cert_chain(certificates / "admin-next.crt", certificates / "admin-next.key")
        assert request(origin, admin_next, "/v1/resource-quotas", policy) == first
        conflict = json.loads(json.dumps(policy))
        conflict["registry"]["limits"][0]["max_units"] = 13
        assert request(origin, admin_next, "/v1/resource-quotas", conflict)[0] == 409
        update = json.loads(json.dumps(conflict))
        update.update(command_id="quota-policy-2", expected_revision=1)
        update["registry"]["revision"] = 2
        assert request(origin, admin_next, "/v1/resource-quotas", update)[0] == 200
        current = request(origin, admin_next, "/v1/resource-quotas")
        assert current[0] == 200 and current[1]["registry"]["revision"] == 2
        assert current[1]["schema_version"] == "roboguide.resource-quota-view/v0.2"
        assert current[1]["usage"] == [
            {
                "tenant_id": "tenant-a",
                "kind": "Compute",
                "max_units": 13,
                "used_units": 0,
                "remaining_units": 13,
            }
        ]
        assert request(origin, admin_next, "/v1/resource-quotas", policy) == first
        inventory = request(origin, admin_next, "/v1/inventory")
        assert inventory[0] == 200 and inventory[1]["nodes"] == []
        stop(process)
        process = None
        with closing(sqlite3.connect(database, timeout=2)) as connection:
            schema_row = connection.execute(
                "SELECT schema, checkpoint_json FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
            journal_rows = connection.execute(
                "SELECT command_id, causation_id, authority_revision FROM control_command_journal "
                "WHERE command_id IN ('registry:quota-policy-1','registry:quota-policy-2') "
                "ORDER BY command_id"
            ).fetchall()
        assert schema_row is not None
        wrapper_schema, wrapper_json = schema_row
        wrapper = json.loads(wrapper_json)
        integration = json.loads(wrapper["integration_json"])
        control = integration["control"]
        quota_checkpoint = control["resource_quota_registry"]
        assert wrapper_schema == schema and quota_checkpoint["revision"] == 2
        assert control["node_identity_registry"] is None
        assert len(journal_rows) == 2
        assert all(row[1] == "quota-admin" and row[2] > 0 for row in journal_rows)
        process = start()
        restored = request(origin, admin_next, "/v1/resource-quotas")
        assert restored[0] == 200 and restored[1]["registry"] == quota_checkpoint
        assert request(origin, admin_next, "/v1/resource-quotas", policy) == first
        inventory_after_restart = request(origin, admin_next, "/v1/inventory")
        assert inventory_after_restart[0] == 200 and inventory_after_restart[1]["nodes"] == []
        return {
            "passed": True,
            "application_schema": schema,
            "binary_sha256": binary_digest,
            "resource_quota_policy_revision": quota_checkpoint["revision"],
            "durable_quota_commands": len(journal_rows),
            "stable_admin_principal": "quota-admin",
            "registered_nodes_before_and_after_restart": 0,
            "node_identity_registry": None,
            "controller_log": str(log_path),
        }
    finally:
        if process is not None:
            stop(process)


def main() -> None:
    """Write bounded process evidence for the actual Controller and original SQLite files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--controller-binary", type=Path)
    args = parser.parse_args()
    result = run(args.output.resolve(), args.skip_build, args.controller_binary)
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
