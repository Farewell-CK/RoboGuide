"""Check actual Controller TLS, upload bytes, revocation and restart with zero Nodes or devices."""

from __future__ import annotations

import argparse
import copy
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
from pathlib import Path
from typing import Any

from check_identity_processes import ROOT, client, pki, port, request, stop


def call(
    origin: str,
    context: ssl.SSLContext,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    *,
    timeout_seconds: float = 5,
) -> tuple[int, bytes]:
    """Read bounded actual HTTP bytes with explicit local TLS and no ambient proxy routing."""
    if not 0 < timeout_seconds <= 60:
        raise ValueError("actual HTTP observation requires an explicit bounded response wait")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    message = urllib.request.Request(origin + path, method=method, data=body, headers=headers or {})
    try:
        response = opener.open(message, timeout=timeout_seconds)  # noqa: S310 - owned loopback.
    except urllib.error.HTTPError as error:
        response = error
    with response:
        result = response.read(2 * 1024 * 1024 + 1)
        if len(result) > 2 * 1024 * 1024:
            raise ValueError("Artifact process response exceeds its bound")
        return response.status, result


def grant(principal: str, role: str, tenant: str) -> dict[str, Any]:
    """Declare service privileges without Node, capability or device evidence."""
    return {
        "principal_id": principal,
        "tenant_id": tenant,
        "role": role,
        "node_id": None,
        "provider_ids": [],
        "memory_ids": [],
        "map_ids": [],
        "artifact_digests": [],
        "not_before_ms": 0,
        "expires_at_ms": 0,
        "revoked": False,
    }


def run(directory: Path, skip_build: bool) -> dict[str, Any]:
    """Test real bytes, concurrent revocation and stable rotation on production listeners."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
            timeout=180,
        )
    binary = ROOT / "target/debug/integration-server"
    certificates = directory / "pki"
    digests = pki(certificates)
    operator_profile = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": "owned-artifact-process-operators",
        "revision": 1,
        "grants": {
            "sha256:" + digests[name]: {
                "principal_id": "controller-administrator",
                "role": "recovery-admin",
                "tenant_ids": ["*"],
            }
            for name in ("admin", "admin-next")
        },
    }
    original_leaf = "sha256:" + digests["admin"]
    next_leaf = "sha256:" + digests["admin-next"]
    artifact_profile: dict[str, Any] = {
        "schema_version": "roboguide.artifact-authorization/v0.1",
        "registry_id": "owned-artifact-process-content",
        "revision": 1,
        "grants": {
            original_leaf: grant("stable-uploader", "admin", "tenant-owned"),
            "sha256:" + digests["viewer"]: grant("separate-viewer", "viewer", "foreign"),
        },
    }
    profile_path = directory / "artifact.json"
    profile_path.write_text(json.dumps(artifact_profile))
    operator_path = directory / "operators.json"
    operator_path.write_text(json.dumps(operator_profile))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    for plane in ("ARTIFACT", "OPERATOR"):
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(certificates / "server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(certificates / "server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
                f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP": str(
                    profile_path if plane == "ARTIFACT" else operator_path
                ),
            }
        )
    control_port, artifact_port = port(), port()
    control_origin = f"https://127.0.0.1:{control_port}"
    artifact_origin = f"https://127.0.0.1:{artifact_port}"
    arguments = [
        str(binary),
        f"127.0.0.1:{port()}",
        str(directory / "events.sqlite3"),
        f"127.0.0.1:{control_port}",
        f"127.0.0.1:{artifact_port}",
        str(directory / "artifacts"),
    ]
    original, rotated, viewer = (
        client(certificates, name) for name in ("admin", "admin-next", "viewer")
    )
    logs: list[str] = []

    def start() -> subprocess.Popen[bytes]:
        """Start the owned Controller with its retained authority and CAS."""
        path = directory / f"controller-{len(logs)}.log"
        logs.append(str(path))
        with path.open("wb") as output:
            process = subprocess.Popen(
                arguments, cwd=ROOT, env=environment, stdout=output, stderr=output
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Controller exited; inspect {path}")
            try:
                if request(control_origin, original, "/healthz")[0] == 200:
                    return process
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        stop(process)
        raise TimeoutError(f"Controller startup expired; inspect {path}")

    source = ROOT / "core/domain/src/memory.rs"
    payload = source.read_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    prefix = f"cert-{digests['admin']}-"
    retained_id, revoked_id = prefix + "retained", prefix + "revoked"
    process = start()
    slow: ssl.SSLSocket | None = None
    try:
        for identity in (retained_id, revoked_id):
            status, _ = call(
                artifact_origin,
                original,
                "POST",
                "/v1/artifact-uploads",
                json.dumps({"upload_id": identity}).encode(),
            )
            assert status == 201
        midpoint = len(payload) // 2
        assert (
            call(
                artifact_origin,
                original,
                "POST",
                f"/v1/artifact-uploads/{retained_id}/content",
                payload[:midpoint],
            )[0]
            == 202
        )
        slow = original.wrap_socket(
            socket.create_connection(("127.0.0.1", artifact_port), timeout=5),
            server_hostname="localhost",
        )
        slow.sendall(
            f"POST /v1/artifact-uploads/{revoked_id}/content HTTP/1.1\r\n"
            "Host: localhost\r\nContent-Length: 2\r\nConnection: close\r\n\r\na".encode()
        )
        staged_path = directory / "artifacts/staging" / (revoked_id + ".partial")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and staged_path.stat().st_size != 1:
            time.sleep(0.02)
        assert staged_path.stat().st_size == 1
        updated = copy.deepcopy(artifact_profile)
        updated["revision"] = 2
        updated["grants"][original_leaf]["revoked"] = True
        updated["grants"][next_leaf] = grant("stable-uploader", "admin", "tenant-owned")
        status, _ = request(
            control_origin,
            original,
            "/v1/artifact-authorizations",
            {"command_id": "rotate-original-uploader", "expected_revision": 1, "registry": updated},
        )
        assert status == 200
        profile_path.write_text(json.dumps(updated))
        slow.sendall(b"b")
        response = bytearray()
        while chunk := slow.recv(65536):
            response.extend(chunk)
        assert bytes(response).startswith(b"HTTP/1.1 403"), bytes(response[:200])
        slow.close()
        slow = None
        assert not staged_path.exists()
        rejected = False
        try:
            rejected = call(artifact_origin, original, "GET", "/healthz")[0] in (401, 403)
        except (OSError, urllib.error.URLError):
            rejected = True
        assert rejected
        assert (
            call(
                artifact_origin,
                viewer,
                "GET",
                f"/v1/artifact-uploads/{retained_id}",
                headers={"X-RoboGuide-Role": "admin"},
            )[0]
            == 403
        )
        status, value = call(artifact_origin, rotated, "GET", f"/v1/artifact-uploads/{retained_id}")
        assert status == 200
        original_record = json.loads(value)["record"]
        assert original_record["principal_id"] == "stable-uploader"
        assert original_record["original_certificate"] == original_leaf
        assert (
            call(
                artifact_origin,
                rotated,
                "POST",
                f"/v1/artifact-uploads/{retained_id}/content",
                payload[midpoint:],
            )[0]
            == 202
        )
        finish = json.dumps({"content_digest": digest, "byte_size": len(payload)}).encode()
        status, body = call(
            artifact_origin,
            rotated,
            "POST",
            f"/v1/artifact-uploads/{retained_id}/finalize",
            finish,
        )
        assert status == 201
        receipt = json.loads(body)["receipt"]
        assert receipt["admitted_at_ms"] == original_record["admitted_at_ms"]
        assert receipt["deadline_ms"] == original_record["deadline_ms"]
        status, actual = call(
            artifact_origin, rotated, "GET", "/v1/artifacts/" + digest.removeprefix("sha256:")
        )
        assert status == 200 and actual == payload
        assert request(control_origin, original, "/v1/inventory")[1]["nodes"] == []
        stop(process)
        process = start()
        assert call(
            artifact_origin,
            rotated,
            "POST",
            f"/v1/artifact-uploads/{retained_id}/finalize",
            finish,
        ) == (201, body)
        with sqlite3.connect(directory / "events.sqlite3") as sql:
            rows = [json.loads(row[0]) for row in sql.execute("SELECT payload_json FROM events")]
        transitions = [
            row["ArtifactUploadTransition"]["record"]
            for row in rows
            if "ArtifactUploadTransition" in row
        ]
        assert [r["phase"] for r in transitions if r["upload_id"] == retained_id] == [
            "started",
            "prepared",
            "completed",
        ]
        assert [r["phase"] for r in transitions if r["upload_id"] == revoked_id] == ["started"]
        return {
            "passed": True,
            "devices": 0,
            "registered_nodes": 0,
            "actual_source_file": str(source.relative_to(ROOT)),
            "actual_source_digest": digest,
            "actual_source_bytes": len(payload),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "application_schema": subprocess.check_output(
                [str(binary), "--application-schema"], timeout=5
            )
            .decode()
            .strip(),
            "upload_transitions": len(transitions),
            "inflight_revocation_fenced": True,
            "stable_rotation_preserved_original_upload": True,
            "restart_replayed_original_receipt": True,
            "logs": logs,
            "physical_safety": "HardwareEvidencePending",
        }
    finally:
        if slow is not None:
            slow.close()
        stop(process)


def main() -> None:
    """Retain actual process results and failures; never mark an interrupted check as passed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    try:
        result = run(directory, args.skip_build)
    except Exception as error:
        if directory.is_dir():
            (directory / "result.json").write_text(
                json.dumps({"passed": False, "error": str(error)[:8192]}, indent=2) + "\n"
            )
        raise
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
