"""Actual mutual-TLS, tenant and HTTP replay tests using owned offline PKI."""

from __future__ import annotations

import hashlib
import json
import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.api import MissionRequestHttpServer, _build_mtls_context, _load_role_map
from mission.authorization import AUTHORIZATION_SCHEMA
from mission.models import JSONObject
from mission.requests import MissionRequestError
from mission.service_config import load_service_settings
from mission.submission_evidence import canonical_plan_digest
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)


def _openssl(directory: Path, *arguments: str) -> None:
    """Run a bounded offline certificate command without printing private key material."""
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=15,
    )


def _pki(directory: Path) -> dict[str, str]:
    """Generate distinct localhost leaves so identity comes from certificates alone."""
    directory.mkdir()
    _openssl(
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
        "/CN=mission-test-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (directory / "extensions").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
    )
    digests = {}
    for name in ("server", "tenant-a", "tenant-b", "viewer", "unmapped"):
        _openssl(
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
        _openssl(
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
            "extensions",
        )
        certificate = ssl.PEM_cert_to_DER_cert((directory / f"{name}.crt").read_text())
        digests[name] = f"sha256:{hashlib.sha256(certificate).hexdigest()}"
    return digests


def _client(directory: Path, name: str | None) -> ssl.SSLContext:
    """Trust only the owned test CA and optionally send one distinct identity."""
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    if name is not None:
        context.load_cert_chain(directory / f"{name}.crt", directory / f"{name}.key")
    return context


def test_mtls_identity_tenant_forgery_and_slow_handshake(tmp_path: Path) -> None:
    """TLS identity gates create/read; a stalled handshake cannot block valid traffic."""
    directory = tmp_path / "pki"
    digests = _pki(directory)
    roles = directory / "roles.json"
    roles.write_text(
        json.dumps(
            {
                digests["tenant-a"]: {"role": "operator", "tenant_id": "a"},
                digests["tenant-b"]: {"role": "operator", "tenant_id": "b"},
                digests["viewer"]: {"role": "viewer", "tenant_id": "a"},
            }
        )
    )
    settings = replace(
        load_service_settings(Path("config/mission-service.toml"), repository_root=Path.cwd()),
        mtls_server_cert=directory / "server.crt",
        mtls_server_key=directory / "server.key",
        mtls_client_ca=directory / "ca.crt",
    )
    interpreter = FakeInterpreter([_assessment()])
    engine = _engine(
        tmp_path, interpreter, FakePlanner(), FakeController(_inventory(*_fixture_contracts()))
    )
    server = MissionRequestHttpServer(
        ("127.0.0.1", 0),
        engine,
        1024 * 1024,
        role_map=_load_role_map(roles),
        tls_context=_build_mtls_context(settings),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    slow = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
    endpoint = f"https://localhost:{server.server_port}"
    payload = json.dumps({"instruction": "execute exactly once"}).encode()
    try:
        for identity, key, status in (
            ("viewer", "denied", 403),
            ("unmapped", "denied", 401),
            ("tenant-a", "", 400),
        ):
            request = urllib.request.Request(
                f"{endpoint}/v1/mission-requests",
                data=payload,
                headers={"Idempotency-Key": key, "X-RoboGuide-Role": "admin"},
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(request, context=_client(directory, identity), timeout=2)  # noqa: S310
            assert failure.value.code == status
        request = urllib.request.Request(
            f"{endpoint}/v1/mission-requests",
            data=payload,
            headers={"Idempotency-Key": "create"},
            method="POST",
        )
        with urllib.request.urlopen(
            request, context=_client(directory, "tenant-a"), timeout=2
        ) as response:  # noqa: S310
            first = cast(JSONObject, json.loads(response.read()))
        with urllib.request.urlopen(
            request, context=_client(directory, "tenant-a"), timeout=2
        ) as response:  # noqa: S310
            assert json.loads(response.read()) == first
        assert len(interpreter.calls) == 1
        assert engine.request_tenant(cast(str, first["request_id"])) == "a"
        with pytest.raises(urllib.error.HTTPError) as cross_tenant:
            urllib.request.urlopen(
                f"{endpoint}/v1/mission-requests/{first['request_id']}",  # noqa: S310
                context=_client(directory, "tenant-b"),
                timeout=2,
            )
        assert cross_tenant.value.code == 403
        with urllib.request.urlopen(
            f"{endpoint}/v1/mission-requests/{first['request_id']}",  # noqa: S310
            context=_client(directory, "viewer"),
            timeout=2,
        ) as response:
            assert json.loads(response.read()) == first
        with pytest.raises((urllib.error.URLError, ssl.SSLError)):
            urllib.request.urlopen(
                f"{endpoint}/healthz", context=_client(directory, None), timeout=2
            )  # noqa: S310
    finally:
        slow.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rotation_preserves_command_identity_and_revocation_is_durable(tmp_path: Path) -> None:
    """Rotate actual TLS leaves without repeating a command, then fence revocation and rollback."""
    directory = tmp_path / "pki"
    digests = _pki(directory)
    profile_path = tmp_path / "authorization.json"
    grants: dict[str, JSONObject] = {
        digests[name]: {"principal_id": "stable-user", "role": "operator", "tenant_id": "a"}
        for name in ("tenant-a", "viewer")
    }
    profile = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "profile_id": "deployment",
        "revision": 1,
        "grants": grants,
    }
    profile_path.write_text(json.dumps(profile))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(directory / "server.crt", directory / "server.key")
    context.load_verify_locations(directory / "ca.crt")
    context.verify_mode = ssl.CERT_REQUIRED
    interpreter = FakeInterpreter([_assessment()])
    engine = _engine(tmp_path, interpreter, FakePlanner(), FakeController(_inventory()))
    server = MissionRequestHttpServer(
        ("127.0.0.1", 0),
        engine,
        65536,
        role_map=_load_role_map(profile_path),
        tls_context=context,
        authorization_path=profile_path,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"https://127.0.0.1:{server.server_port}"
    request = urllib.request.Request(
        endpoint + "/v1/mission-requests",
        data=json.dumps({"instruction": "执行已声明的工作"}).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": "one-command"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, context=_client(directory, "tenant-a"), timeout=2
        ) as response:  # noqa: S310
            original = json.loads(response.read())
        with urllib.request.urlopen(
            request, context=_client(directory, "viewer"), timeout=2
        ) as response:  # noqa: S310
            assert json.loads(response.read()) == original
        assert len(interpreter.calls) == 1
        grants[digests["tenant-a"]]["revoked"] = True
        profile["revision"] = 2
        profile_path.write_text(json.dumps(profile))
        with pytest.raises(urllib.error.HTTPError) as revoked:
            urllib.request.urlopen(request, context=_client(directory, "tenant-a"), timeout=2)  # noqa: S310
        assert revoked.value.code == 401
        with urllib.request.urlopen(
            request, context=_client(directory, "viewer"), timeout=2
        ) as response:  # noqa: S310
            assert json.loads(response.read()) == original
        profile["revision"] = 1
        profile_path.write_text(json.dumps(profile))
        with pytest.raises(urllib.error.HTTPError) as rollback:
            urllib.request.urlopen(request, context=_client(directory, "viewer"), timeout=2)  # noqa: S310
        assert rollback.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    with pytest.raises(MissionRequestError, match="rollback"):
        MissionRequestHttpServer(
            ("127.0.0.1", 0),
            engine,
            65536,
            role_map=_load_role_map(profile_path),
            tls_context=context,
            authorization_path=profile_path,
        )


def test_legacy_tenant_migration_is_explicit_digest_bound_and_scope_checked(tmp_path: Path) -> None:
    """Migrate a stored Request over mTLS without repeating deliberation or submission."""
    directory = tmp_path / "pki"
    digests = _pki(directory)
    interpreter = FakeInterpreter([_assessment()])
    engine = _engine(tmp_path, interpreter, FakePlanner(), FakeController(_inventory()))
    record = engine.create("legacy declared work")
    assert engine.request_tenant(record.request_id) is None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(directory / "server.crt", directory / "server.key")
    context.load_verify_locations(directory / "ca.crt")
    context.verify_mode = ssl.CERT_REQUIRED
    server = MissionRequestHttpServer(
        ("127.0.0.1", 0),
        engine,
        65536,
        role_map={digests["tenant-a"]: ("admin", "a")},
        tls_context=context,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"https://127.0.0.1:{server.server_port}"
    body: JSONObject = {
        "request_id": record.request_id,
        "tenant_id": "a",
        "source_digest": canonical_plan_digest(record.to_json()),
    }

    def migrate(document: JSONObject, key: str) -> tuple[int, JSONObject]:
        """Send one real authenticated command and preserve its rejection for assertions."""
        request = urllib.request.Request(
            endpoint + "/v1/tenant-migrations",
            data=json.dumps(document).encode(),
            headers={"Idempotency-Key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, context=_client(directory, "tenant-a"), timeout=2
            ) as response:  # noqa: S310
                return response.status, cast(JSONObject, json.loads(response.read()))
        except urllib.error.HTTPError as error:
            return error.code, cast(JSONObject, json.loads(error.read()))

    try:
        assert migrate({**body, "tenant_id": "b"}, "foreign")[0] == 403
        assert migrate({**body, "source_digest": "sha256:" + "0" * 64}, "wrong-source")[0] == 409
        assert engine.request_tenant(record.request_id) is None
        status, receipt = migrate(body, "source-bound")
        assert status == 200 and receipt["execution_repeated"] is False
        assert migrate(body, "source-bound") == (status, receipt)
        assert engine.request_tenant(record.request_id) == "a"
        assert engine.get(record.request_id).to_json() == record.to_json()
        assert len(interpreter.calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
