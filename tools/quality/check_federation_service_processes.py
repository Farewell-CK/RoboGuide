"""Exercise two authenticated Controller Federation services with zero Node facts."""

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
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "roboguide.federation-service/v0.1"


def openssl(directory: Path, *arguments: str) -> None:
    """Run one bounded local PKI operation without printing private key material."""
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def port() -> int:
    """Reserve an isolated loopback port for one owned process listener."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def issue_pki(directory: Path) -> tuple[ssl.SSLContext, str]:
    """Issue one real CA, server leaf and mTLS client leaf for the software gate."""
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
        "/CN=roboguide-federation-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (directory / "leaf.ext").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
    )
    digests: dict[str, str] = {}
    for name in ("server", "admin"):
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
            f"{name}.key",
            "-out",
            f"{name}.csr",
            "-subj",
            f"/CN={name}",
        )
        openssl(
            directory,
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            f"{name}.crt",
            "-days",
            "1",
            "-extfile",
            "leaf.ext",
        )
        der = ssl.PEM_cert_to_DER_cert((directory / f"{name}.crt").read_text())
        digests[name] = hashlib.sha256(der).hexdigest()
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    context.load_cert_chain(directory / "admin.crt", directory / "admin.key")
    return context, digests["admin"]


def request(
    origin: str,
    context: ssl.SSLContext,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    tenant: str | None = "tenant-a",
) -> tuple[int, dict[str, Any]]:
    """Call the actual bounded HTTPS route and retain rejection bodies for audit."""
    payload = None if body is None else json.dumps(body).encode()
    headers = {"X-RoboGuide-Tenant": tenant} if tenant is not None else {}
    call = urllib.request.Request(
        origin + path,
        data=payload,
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        response = urllib.request.urlopen(call, context=context, timeout=4)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    with response:
        value = json.loads(response.read(2 * 1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError("Federation response is not an object")
        return response.status, value


def stop(process: subprocess.Popen[bytes] | None) -> None:
    """Stop one owned Controller and wait for its SQLite writer lease to close."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start(
    binary: Path,
    directory: Path,
    domain: str,
    http_port: int,
    admin_environment: dict[str, str],
    logs: list[str],
) -> subprocess.Popen[bytes]:
    """Start one real integration-server domain with independent event and Artifact roots."""
    domain_root = directory / domain
    domain_root.mkdir(parents=True, exist_ok=True)
    grpc_port = port()
    artifact_port = port()
    environment = dict(admin_environment)
    environment["ROBOGUIDE_CONTROL_DOMAIN"] = domain
    arguments = [
        str(binary),
        f"127.0.0.1:{grpc_port}",
        str(domain_root / "events.sqlite3"),
        f"127.0.0.1:{http_port}",
        f"127.0.0.1:{artifact_port}",
        str(domain_root / "artifacts"),
    ]
    log_path = directory / f"{domain}-{len(logs)}.log"
    logs.append(str(log_path))
    with log_path.open("wb") as output:
        process = subprocess.Popen(
            arguments, cwd=ROOT, env=environment, stdout=output, stderr=output
        )
    origin = f"https://127.0.0.1:{http_port}"
    context = ssl.create_default_context(cafile=environment["ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA"])
    context.load_cert_chain(
        environment["ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT"].replace("server.crt", "admin.crt"),
        environment["ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY"].replace("server.key", "admin.key"),
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{domain} Controller exited; inspect {log_path}")
        try:
            if request(origin, context, "/healthz", tenant=None)[0] == 200:
                return process
        except (OSError, urllib.error.URLError, ssl.SSLError, json.JSONDecodeError):
            time.sleep(0.05)
    stop(process)
    raise TimeoutError(f"{domain} Controller did not become healthy; inspect {log_path}")


def run(output: Path, skip_build: bool, controller_binary: Path | None) -> dict[str, Any]:
    """Run prepare/commit/restart/fence cases across two independent software domains."""
    output.mkdir(parents=True, exist_ok=True)
    binary = controller_binary or ROOT / "target" / "debug" / "integration-server"
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "-p", "integration-server"], cwd=ROOT, check=True, timeout=180
        )
    if not binary.is_file():
        raise FileNotFoundError(binary)
    certificates = output / "pki"
    _, admin_digest = issue_pki(certificates)
    role_map = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": "federation-gate-" + uuid.uuid4().hex,
        "revision": 1,
        "grants": {
            "sha256:" + admin_digest: {
                "principal_id": "federation-admin",
                "role": "recovery-admin",
                "tenant_ids": ["tenant-a"],
                "not_before_ms": 0,
                "expires_at_ms": 0,
                "revoked": False,
            }
        },
    }
    role_path = output / "operator.json"
    role_path.write_text(json.dumps(role_map))
    domain_grants = output / "domain-grants.json"
    domain_grants.write_text(json.dumps({"sha256:" + admin_digest: ["domain-a", "domain-b"]}))
    base_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    base_environment.update(
        {
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(role_path),
            "ROBOGUIDE_FEDERATION_DOMAIN_GRANTS": str(domain_grants),
            "TOKIO_WORKER_THREADS": "2",
        }
    )
    client_context = ssl.create_default_context(cafile=str(certificates / "ca.crt"))
    client_context.load_cert_chain(certificates / "admin.crt", certificates / "admin.key")
    ports = {"domain-a": port(), "domain-b": port()}
    origins = {domain: f"https://127.0.0.1:{value}" for domain, value in ports.items()}
    processes: dict[str, subprocess.Popen[bytes] | None] = {"domain-a": None, "domain-b": None}
    logs: list[str] = []
    transaction_id = "federation-process-" + uuid.uuid4().hex
    deadline = int(time.time() * 1000) + 120_000
    session = {
        "transaction_id": transaction_id,
        "source_domain": "domain-a",
        "target_domain": "domain-b",
        "tenant_id": "tenant-a",
        "source_epoch": 7,
        "plan_digest": "sha256:" + "a" * 64,
        "ownership_digest": "sha256:" + "b" * 64,
        "deadline": deadline,
        "state": "prepared",
    }
    prepare = {"schema_version": SCHEMA, "session": session}
    commit = {
        "schema_version": SCHEMA,
        "source_epoch": 7,
        "plan_digest": session["plan_digest"],
        "ownership_digest": session["ownership_digest"],
    }
    try:
        processes["domain-a"] = start(
            binary, output, "domain-a", ports["domain-a"], base_environment, logs
        )
        processes["domain-b"] = start(
            binary, output, "domain-b", ports["domain-b"], base_environment, logs
        )
        prepared_a = request(
            origins["domain-a"], client_context, "/v1/federation/sessions/prepare", prepare
        )
        prepared_b = request(
            origins["domain-b"], client_context, "/v1/federation/sessions/prepare", prepare
        )
        if prepared_a[0] != 202 or prepared_b[0] != 202:
            raise AssertionError((prepared_a, prepared_b))
        stop(processes["domain-b"])
        processes["domain-b"] = None
        processes["domain-b"] = start(
            binary, output, "domain-b", ports["domain-b"], base_environment, logs
        )
        recovered = request(
            origins["domain-b"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}",
        )
        if recovered[0] != 200 or recovered[1]["session"]["state"] != "prepared":
            raise AssertionError(recovered)
        committed_a = request(
            origins["domain-a"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}/commit",
            commit,
        )
        committed_b = request(
            origins["domain-b"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}/commit",
            commit,
        )
        if committed_a[0] != 202 or committed_b[0] != 202:
            raise AssertionError((committed_a, committed_b))
        stale = dict(commit, source_epoch=8)
        if (
            request(
                origins["domain-b"],
                client_context,
                f"/v1/federation/sessions/{transaction_id}/commit",
                stale,
            )[0]
            != 409
        ):
            raise AssertionError("stale epoch was accepted")
        wrong_tenant = request(
            origins["domain-a"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}",
            tenant="tenant-b",
        )
        if wrong_tenant[0] != 403:
            raise AssertionError("tenant delegation crossed the Federation boundary")
        final_a = request(
            origins["domain-a"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}",
        )
        final_b = request(
            origins["domain-b"],
            client_context,
            f"/v1/federation/sessions/{transaction_id}",
        )
        if any(
            result[0] != 200 or result[1]["session"]["state"] != "committed"
            for result in (final_a, final_b)
        ):
            raise AssertionError((final_a, final_b))
        journal_rows: dict[str, int] = {}
        for domain in ("domain-a", "domain-b"):
            database = output / domain / "events.sqlite3"
            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    "SELECT command_id, causation_id FROM control_command_journal "
                    "WHERE command_id LIKE 'federation:%'"
                ).fetchall()
            if not rows or any(causation != "federation-admin" for _, causation in rows):
                raise AssertionError(f"{domain} Federation journal lacks the mTLS subject")
            journal_rows[domain] = len(rows)
        return {
            "passed": True,
            "schema": SCHEMA,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "domains": ["domain-a", "domain-b"],
            "tenant": "tenant-a",
            "transaction_id": transaction_id,
            "restart_reconciled": True,
            "stale_epoch_rejected": True,
            "wrong_tenant_rejected": True,
            "journal_rows": journal_rows,
            "registered_nodes": 0,
            "hardware_evidence": "HardwareEvidencePending",
            "logs": logs,
        }
    finally:
        for process in processes.values():
            with suppress(Exception):
                stop(process)


def main() -> None:
    """Write bounded two-domain Federation evidence without creating devices or model facts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--controller-binary", type=Path)
    args = parser.parse_args()
    if args.controller_binary is not None and not args.skip_build:
        parser.error("--controller-binary requires --skip-build")
    result = run(args.output.resolve(), args.skip_build, args.controller_binary)
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
