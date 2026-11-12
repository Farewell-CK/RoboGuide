"""Verify actual client-certificate authentication using isolated local TLS processes."""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from roboguide_workbench.client import ReadOnlyController, WorkbenchError, request_json


def openssl(root: Path, *arguments: str) -> None:
    """Generate only test-local certificate material without exposing keys or changing trust."""
    subprocess.run(["openssl", *arguments], cwd=root, check=True, capture_output=True, timeout=15)


def test_actual_mtls_preserves_tenant_and_requires_client_certificate(tmp_path: Path) -> None:
    """An authenticated TLS read succeeds while the same server refuses an anonymous client."""
    if shutil.which("openssl") is None:
        pytest.skip("actual TLS certificate fixture requires openssl")
    openssl(
        tmp_path,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=Workbench Test CA",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    for name, usage in (("server", "serverAuth"), ("client", "clientAuth")):
        openssl(
            tmp_path,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
        )
        extensions = tmp_path / f"{name}.ext"
        extensions.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            f"extendedKeyUsage={usage}\nsubjectAltName=IP:127.0.0.1\n",
            encoding="utf-8",
        )
        openssl(
            tmp_path,
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            extensions.name,
            "-out",
            f"{name}.crt",
        )
    calls: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        """Serve a zero-Node fixture only after the TLS stack verifies the peer certificate."""

        def do_GET(self) -> None:
            """Record authenticated scope and return complete inventory JSON."""
            calls.append(self.headers.get("X-RoboGuide-Tenant"))
            body = json.dumps(
                {"schema_version": "roboguide.inventory/v0.1", "observed_at_ms": 1, "nodes": []}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Keep test server output independent of client diagnostics."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_tls.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
    server_tls.load_verify_locations(tmp_path / "ca.crt")
    server_tls.verify_mode = ssl.CERT_REQUIRED
    server.socket = server_tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    endpoint = f"https://127.0.0.1:{server.server_port}"
    try:
        client = ReadOnlyController(
            endpoint,
            tenant="team-a",
            ca=tmp_path / "ca.crt",
            certificate=tmp_path / "client.crt",
            key=tmp_path / "client.key",
        )
        assert client.inventory()["nodes"] == []
        anonymous_tls = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
        with pytest.raises(WorkbenchError):
            request_json(endpoint + "/v1/inventory", "GET", {}, None, 1, anonymous_tls)
        assert calls == ["team-a"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
