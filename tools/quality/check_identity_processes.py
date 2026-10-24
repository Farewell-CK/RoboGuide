"""Exercise real Controller mTLS identity administration with zero registered device facts."""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import ssl
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def openssl(directory: Path, *arguments: str) -> None:
    """Run one bounded offline PKI operation without emitting private key bytes."""
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
    )


def pki(directory: Path) -> dict[str, str]:
    """Issue software test credentials, never hardware identities or capabilities."""
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
        "/CN=offline-identity-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (directory / "extensions").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
    )
    digests: dict[str, str] = {}
    for name in (
        "server",
        "admin",
        "admin-next",
        "viewer",
        "enrolment-first",
        "enrolment-next",
        "reenrolment",
    ):
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
    return digests


def port() -> int:
    """Select an isolated local listener; binding failure is retained as a test failure."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def ports(count: int) -> list[int]:
    """Select a distinct actual loopback listener set while retaining every original socket."""
    if not 1 <= count <= 64:
        raise ValueError("actual listener set must contain between 1 and 64 sockets")
    with ExitStack() as reservations:
        selected: list[int] = []
        for _ in range(count):
            listener = reservations.enter_context(closing(socket.socket()))
            listener.bind(("127.0.0.1", 0))
            selected.append(int(listener.getsockname()[1]))
        return selected


def client(directory: Path, name: str) -> ssl.SSLContext:
    """Send one independently issued leaf and verify the configured localhost server."""
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    context.load_cert_chain(directory / (name + ".crt"), directory / (name + ".key"))
    return context


def request(
    origin: str,
    context: ssl.SSLContext,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    *,
    timeout_seconds: float = 3,
) -> tuple[int, dict[str, Any]]:
    """Use the public HTTPS API and preserve bounded domain rejection responses."""
    if not 0 < timeout_seconds <= 60:
        raise ValueError("actual HTTP observation requires an explicit bounded response wait")
    payload = None if body is None else json.dumps(body).encode()
    call = urllib.request.Request(
        origin + path, data=payload, headers=headers or {}, method="GET" if body is None else "POST"
    )
    try:
        response = urllib.request.urlopen(call, context=context, timeout=timeout_seconds)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    with response:
        value = json.loads(response.read(2 * 1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError("Controller response is not an object")
        return response.status, value


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop only the owned test Controller and wait for its writer lease to close."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def metrics_text(origin: str, context: ssl.SSLContext) -> str:
    """Read bounded actual Prometheus text over the original authenticated transport."""
    with urllib.request.urlopen(origin + "/metrics", context=context, timeout=3) as response:  # noqa: S310
        content: bytes = response.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("Controller metrics exceeded the observation bound")
        return content.decode()


def operator_capacity(
    origin: str, context: ssl.SSLContext, artifact_origin: str, limit: int, directory: Path
) -> dict[str, Any]:
    """Occupy actual TLS permits, reject excess transport and probe the Artifact listener."""
    endpoint = urllib.parse.urlsplit(origin)
    assert endpoint.hostname == "127.0.0.1" and endpoint.port is not None
    held: list[ssl.SSLSocket] = []
    rejected = False
    try:
        for _ in range(limit):
            raw = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
            try:
                held.append(context.wrap_socket(raw, server_hostname=endpoint.hostname))
            except BaseException:
                raw.close()
                raise
        try:
            with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname=endpoint.hostname) as excess:
                    excess.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    rejected = not excess.recv(1)
        except OSError:
            rejected = True
        assert rejected, "over-capacity transport received an application response"
        with urllib.request.urlopen(artifact_origin + "/healthz", timeout=3) as independent:  # noqa: S310
            assert independent.status == 200
    finally:
        for connection in held:
            connection.close()
    deadline = time.monotonic() + 4
    observed = ""
    values: dict[str, int] = {}
    while time.monotonic() < deadline:
        try:
            observed = metrics_text(origin, context)
            for stage in (
                "operator_connection_limit",
                "operator_connection_rejections",
                "operator_connections_active",
            ):
                prefix = 'roboguide_controller_stage_value{kind="http",stage="' + stage + '"} '
                for line in observed.splitlines():
                    if line.startswith(prefix):
                        values[stage] = int(line.removeprefix(prefix))
            if (
                values.get("operator_connection_limit") == limit
                and values.get("operator_connection_rejections", 0) >= 1
                and values.get("operator_connections_active") == 0
            ):
                break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.05)
    else:
        raise TimeoutError("original connection permits or observations did not recover")
    (directory / "operator-capacity-metrics.prom").write_text(observed)
    return {
        "actual_held_tls_transports": limit,
        "excess_transport_rejected_before_request": rejected,
        "independent_artifact_http_status": 200,
        "released_permits_observed": True,
        "actual_observed_values": values,
        "registered_node_facts": 0,
    }


def queued_revocation(
    origin: str,
    admin: ssl.SSLContext,
    next_admin: ssl.SSLContext,
    profile: dict[str, Any],
    profile_path: Path,
    database: Path,
    directory: Path,
    revoked_digest: str,
) -> dict[str, Any]:
    """Revoke queued HTTPS work after observing its original grant-file read."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "cannot observe original authority reads")
    try:
        if libc.inotify_add_watch(descriptor, os.fsencode(profile_path), 0x30) < 0:
            raise OSError(ctypes.get_errno(), "cannot watch the actual operator profile")

        def reads(count: int) -> int:
            """Wait for actual read-close events within the bounded injection window."""
            observed = 0
            deadline = time.monotonic() + 2
            while observed < count and time.monotonic() < deadline:
                try:
                    content = os.read(descriptor, 65536)
                except BlockingIOError:
                    time.sleep(0.005)
                    continue
                offset = 0
                while offset + 16 <= len(content):
                    _, mask, _, size = struct.unpack_from("iIII", content, offset)
                    if mask & 0x4000:
                        raise ValueError("authority-read observation overflowed")
                    observed += bool(mask & 0x10)
                    offset += 16 + size
            if observed < count:
                raise TimeoutError(f"original authority read count {observed}/{count}")
            return observed

        first: dict[str, Any] = {
            "command_id": str(uuid.uuid4()),
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.memory-authorization/v0.1",
                "registry_id": str(uuid.uuid4()),
                "revision": 1,
                "policies": {},
            },
        }
        second = {
            "command_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "registry": {**first["registry"], "revision": 2},
        }
        endpoint = urllib.parse.urlsplit(origin)
        assert endpoint.hostname is not None and endpoint.port is not None
        with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3) as raw:
            with admin.wrap_socket(raw, server_hostname=endpoint.hostname) as waiting:
                transport_reads = reads(1)
                payload = json.dumps(second).encode()
                waiting.sendall(
                    b"POST /v1/memory-authorizations HTTP/1.1\r\nHost: localhost\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                    + payload[:-1]
                )

                def finish_original_request() -> tuple[int, dict[str, Any]]:
                    """Finish the already authenticated connection before deployment revocation."""
                    waiting.sendall(payload[-1:])
                    response = http.client.HTTPResponse(waiting)
                    response.begin()
                    value = json.loads(response.read(2 * 1024 * 1024 + 1))
                    if not isinstance(value, dict):
                        raise ValueError("queued Controller response is not an object")
                    return response.status, value

                with closing(sqlite3.connect(database, timeout=1)) as lock, lock:
                    lock.execute("BEGIN IMMEDIATE")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
                        try:
                            original = workers.submit(
                                request, origin, admin, "/v1/memory-authorizations", first
                            )
                            first_reads = reads(3)
                            queued = workers.submit(finish_original_request)
                            try:
                                queued_reads = reads(1)
                            except TimeoutError:
                                lock.rollback()
                                observed_failure = {
                                    "original_result": original.result(timeout=4),
                                    "queued_result": queued.result(timeout=4),
                                }
                                (directory / "queue-window-unavailable.json").write_text(
                                    json.dumps(observed_failure, indent=2) + "\n"
                                )
                                raise
                            metrics = workers.submit(metrics_text, origin, next_admin)
                            metrics_reads = reads(2)
                            health_started = time.monotonic()
                            health = request(origin, next_admin, "/healthz")
                            health_elapsed = time.monotonic() - health_started
                            assert health[0] == 200, health
                            profile["revision"] += 1
                            profile["grants"][revoked_digest]["revoked"] = True
                            temporary = profile_path.with_suffix(".pending")
                            temporary.write_text(json.dumps(profile))
                            temporary.replace(profile_path)
                        finally:
                            lock.rollback()
                        original_result = original.result(timeout=4)
                        queued_result = queued.result(timeout=4)
                        blocked_metrics = metrics.result(timeout=4)
                        assert "roboguide_storage_read_available" in blocked_metrics
        observed = {
            "original_result": original_result,
            "queued_result": queued_result,
            "original_file_reads": first_reads,
            "queued_transport_file_reads": transport_reads,
            "queued_original_file_reads": queued_reads,
            "health_during_actual_sql_writer_lock": health[0],
            "health_elapsed_seconds": health_elapsed,
            "concurrent_metrics_original_file_reads": metrics_reads,
        }
        (directory / "queued-observation.json").write_text(json.dumps(observed, indent=2) + "\n")
        assert original_result[0] == 200, original_result
        assert queued_result[0] == 409, queued_result
        assert "revision changed before transaction" in queued_result[1]["error"]
        registry = request(origin, next_admin, "/v1/memory-authorizations")
        assert registry[0] == 200 and registry[1]["registry"]["revision"] == 1, registry
        evidence = {
            "original_status": original_result[0],
            "queued_status": queued_result[0],
            "queued_rejection": queued_result[1],
            "original_file_reads": first_reads,
            "queued_original_file_reads": queued_reads,
            "remaining_registry_revision": 1,
            "second_command_not_committed": True,
        }
        (directory / "queued-revocation.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return evidence
    finally:
        os.close(descriptor)


def run(
    directory: Path,
    skip_build: bool,
    async_workers: int = 8,
    connection_limit: int = 128,
    capacity_probe: bool = False,
    controller_binary: Path | None = None,
) -> dict[str, Any]:
    """Validate public identity transactions, restart, rollback and principal rotation."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
            timeout=180,
        )
    binary = (controller_binary or ROOT / "target/debug/integration-server").resolve(strict=True)
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    certificates = directory / "pki"
    digests = pki(certificates)
    registry = {
        "schema": "roboguide.node-identity-registry/v0.1",
        "registry_id": str(uuid.uuid4()),
        "records": {},
    }
    grants: dict[str, dict[str, Any]] = {
        "sha256:" + digests[name]: {
            "principal_id": "owned-admin",
            "role": "recovery-admin",
            "tenant_ids": ["*"],
        }
        for name in ("admin", "admin-next")
    }
    grants["sha256:" + digests["viewer"]] = {
        "principal_id": "owned-viewer",
        "role": "viewer",
        "tenant_ids": ["tenant-a"],
    }
    profile = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": str(uuid.uuid4()),
        "revision": 1,
        "grants": grants,
    }
    profile_path = directory / "operator.json"
    profile_path.write_text(json.dumps(profile))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment.update(
        {
            "TOKIO_WORKER_THREADS": str(async_workers),
            "ROBOGUIDE_OPERATOR_CONNECTION_LIMIT": str(connection_limit),
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(registry),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(profile_path),
        }
    )
    schema = (
        subprocess.check_output([str(binary), "--application-schema"], env=environment, timeout=5)
        .decode()
        .strip()
    )
    http_port = port()
    artifact_port = port()
    origin = f"https://127.0.0.1:{http_port}"
    args = [
        str(binary),
        f"127.0.0.1:{port()}",
        str(directory / "events.sqlite3"),
        f"127.0.0.1:{http_port}",
        f"127.0.0.1:{artifact_port}",
        str(directory / "artifacts"),
    ]
    admin = client(certificates, "admin")
    next_admin = client(certificates, "admin-next")
    viewer = client(certificates, "viewer")
    logs: list[str] = []

    def start(identity: ssl.SSLContext) -> subprocess.Popen[bytes]:
        """Start the real composition root with its original storage and wait for public health."""
        if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_sha256:
            raise ValueError("Controller binary changed during the software check")
        path = directory / f"controller-{len(logs)}.log"
        logs.append(str(path))
        with path.open("wb") as output:
            process = subprocess.Popen(
                args, cwd=ROOT, env=environment, stdout=output, stderr=output
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Controller exited; inspect {path}")
            try:
                if request(origin, identity, "/healthz")[0] == 200:
                    return process
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        stop(process)
        raise TimeoutError(f"Controller did not become healthy; inspect {path}")

    process = start(admin)
    try:
        capacity = (
            operator_capacity(
                origin, admin, f"http://127.0.0.1:{artifact_port}", connection_limit, directory
            )
            if capacity_probe
            else None
        )
        node_id = "credential-" + str(uuid.uuid4())
        command = {
            "command_id": str(uuid.uuid4()),
            "node_id": node_id,
            "expected_revision": 0,
            "action": {
                "kind": "register",
                "tenant_id": "tenant-a",
                "domain_id": "domain-a",
                "digest": digests["enrolment-first"],
                "expires_at_ms": 0,
            },
        }
        assert (
            request(
                origin,
                viewer,
                "/v1/node-identities/commands",
                command,
                {"X-RoboGuide-Operator-Role": "recovery-admin"},
            )[0]
            == 401
        )
        first = request(origin, admin, "/v1/node-identities/commands", command)
        assert first[0] == 200 and first[1]["receipt"]["authority_subject"] == "owned-admin"
        assert request(origin, next_admin, "/v1/node-identities/commands", command) == first
        for revision, action in [
            (1, {"kind": "prepare", "digest": digests["enrolment-next"]}),
            (2, {"kind": "activate", "overlap_ms": 100}),
            (3, {"kind": "revoke"}),
            (4, {"kind": "reenroll", "digest": digests["reenrolment"], "expires_at_ms": 0}),
        ]:
            change = {
                "command_id": str(uuid.uuid4()),
                "node_id": node_id,
                "expected_revision": revision,
                "action": action,
            }
            assert request(origin, admin, "/v1/node-identities/commands", change)[0] == 200
        inventory = request(origin, admin, "/v1/inventory")
        assert inventory[0] == 200 and inventory[1]["nodes"] == []
        assert (
            request(
                origin, viewer, "/v1/node-identities", headers={"X-RoboGuide-Tenant": "tenant-b"}
            )[0]
            == 403
        )
        queued = queued_revocation(
            origin,
            admin,
            next_admin,
            profile,
            profile_path,
            directory / "events.sqlite3",
            directory,
            "sha256:" + digests["admin"],
        )
        assert request(origin, admin, "/v1/node-identities")[0] == 401
        before = request(origin, next_admin, "/v1/node-identities")[1]["registry"]
        stop(process)
        process = start(next_admin)
        assert request(origin, next_admin, "/v1/node-identities")[1]["registry"] == before
        assert request(origin, next_admin, "/v1/node-identities/commands", command) == first
        assert request(origin, next_admin, "/v1/inventory")[1]["nodes"] == []
        profile["revision"] = 1
        profile_path.write_text(json.dumps(profile))
        assert request(origin, next_admin, "/v1/node-identities")[0] == 401
        return {
            "passed": True,
            "application_schema": schema,
            "binary_sha256": binary_sha256,
            "async_workers": async_workers,
            "operator_connection_limit": connection_limit,
            "operator_capacity": capacity,
            "queued_revocation": queued,
            "registry_records": len(before["records"]),
            "registered_node_facts": 0,
            "hardware_identity": "HardwareEvidencePending",
            "logs": logs,
        }
    finally:
        stop(process)


def main() -> None:
    """Write a reproducible process result, keeping owned logs and software PKI local."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--async-workers", type=int, choices=range(1, 65), default=8)
    parser.add_argument(
        "--operator-connection-limit", type=int, choices=range(4, 1025), default=128
    )
    parser.add_argument("--capacity-probe", action="store_true")
    parser.add_argument("--controller-binary", type=Path)
    args = parser.parse_args()
    if args.controller_binary is not None and not args.skip_build:
        parser.error("--controller-binary requires --skip-build and explicit archived bytes")
    result = run(
        args.output.resolve(),
        args.skip_build,
        args.async_workers,
        args.operator_connection_limit,
        args.capacity_probe,
        args.controller_binary,
    )
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
