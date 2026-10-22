"""Verify the local Memory reference protocol across actual provider restarts, without Nodes."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "integrations/memory-provider"
sys.path.insert(0, str(PROVIDER))

from roboguide_memory_provider.store import canonical, original_content_digest  # noqa: E402


class UnixConnection(http.client.HTTPConnection):
    """Use only the configured provider's actual same-user Unix socket."""

    def __init__(self, path: Path) -> None:
        """Retain the exact Unix socket path and a bounded request timeout."""
        super().__init__("localhost", timeout=10)
        self.path = path

    def connect(self) -> None:
        """Connect directly through AF_UNIX without a TCP fallback."""
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(self.timeout)
        try:
            channel.connect(str(self.path))
        except BaseException:
            channel.close()
            raise
        self.sock = channel


def save(path: Path, value: object) -> None:
    """Persist one deterministic evidence document under a private output directory."""
    path.write_bytes(canonical(value) + b"\n")


def request(
    path: Path, route: str, body: dict[str, Any], method: str
) -> tuple[int, dict[str, Any]]:
    """Call one fixed local route and preserve its actual HTTP status and JSON response."""
    connection = UnixConnection(path)
    try:
        connection.request(
            method,
            route,
            body=canonical(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("provider response exceeds the inspection bound")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError("provider response is not a JSON object")
        return response.status, result
    finally:
        connection.close()


def start(
    root: Path, inputs: Path, log: BinaryIO, *, snapshot_references: bool = False
) -> subprocess.Popen[bytes]:
    """Start the shipped provider binary and verify its actual private socket announcement."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(PROVIDER)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roboguide_memory_provider",
            "--root",
            str(root),
            "--provider-id",
            "local-files",
            "--input-root",
            str(inputs),
            "--listen-unix",
            "--enable-use-leases",
            "--enable-file-jobs",
            "--enable-content-reclamation",
            *(["--enable-snapshot-references"] if snapshot_references else []),
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=log,
    )
    try:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 15)
        if not readable:
            raise TimeoutError("actual Memory provider did not announce readiness")
        announcement = json.loads(process.stdout.readline())
        expected = "unix://" + str(root / "local.sock")
        if announcement != {"endpoint": expected}:
            raise ValueError("actual Memory provider announced a different endpoint")
        deadline = time.monotonic() + 10
        while not (root / "local.sock").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("actual Memory provider exited before opening its socket")
            time.sleep(0.02)
        if not (root / "local.sock").exists():
            raise TimeoutError("actual Memory provider socket did not become available")
        return process
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        raise


def stop(process: subprocess.Popen[bytes], socket_path: Path) -> int:
    """Stop the owned provider and require its real socket cleanup before restart."""
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        code = process.wait(timeout=15)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait(timeout=10)
        raise TimeoutError("actual Memory provider did not finish shutdown") from error
    if socket_path.exists():
        raise ValueError("actual Memory provider left its local socket after shutdown")
    return code


def _import_fixture(source: Path, provider_id: str, instance_id: str) -> dict[str, Any]:
    """Build one file-only original import record without any Node or device identity."""
    payload = source.read_bytes()
    artifact = {
        "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }
    manifest = {
        "schema": "roboguide.memory-manifest/v0.1",
        "kind": "artifact",
        "selector": {"memory_id": uuid.uuid4().hex, "revision_id": "original"},
        "scope": {"kind": "local"},
        "visibility": "local",
        "artifact": artifact,
    }
    import_id = uuid.uuid4().hex
    invocation = {
        "import_id": import_id,
        "command_id": "memory-import-" + import_id,
        "consumer_provider_id": provider_id,
        "manifest_json": canonical(manifest).decode(),
        "mission_id": "memory-reference-process-check-mission",
        "group_id": "memory-reference-process-check-group",
        "task_id": "memory-reference-process-check-task",
        "role_id": "memory-reference-process-check-role",
        "execution_id": "memory-reference-process-check-operation",
    }
    return {
        "manifest": manifest,
        "original": {
            "import_id": import_id,
            "command_id": invocation["command_id"],
            "provider_id": provider_id,
            "provider_instance_id": instance_id,
            "identity_digest": hashlib.sha256(canonical(invocation)).hexdigest(),
            "provider_contract_digest": "sha256:" + hashlib.sha256(b"fixture contract").hexdigest(),
            "original_invocation": invocation,
            "admitted_at_ms": 1,
            "original_content_digest": original_content_digest(invocation),
        },
        "artifact": artifact,
    }


def check_recovery(
    output: Path,
    root: Path,
    inputs: Path,
    query: dict[str, Any],
    acquired: dict[str, Any],
) -> dict[str, Any]:
    """Restore real active reference bytes via the independent CLI and reject lost-CAS bundles."""
    selected: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.4",
        "profile_id": "local-content-reference-recovery",
        "revision": 1,
        "max_bytes": 16 * 1024 * 1024,
        "max_files": 128,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "provider",
                "kind": "memory",
                "root": str(root),
                "database": None,
                "trees": [],
                "depends_on": [],
            }
        ],
    }
    profile = output / "reference-recovery-profile.json"
    save(profile, selected)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def invoke(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Run the shipped recovery CLI in a new process and retain each exact exit and output."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=40,
        )
        label = f"reference-recovery-{len(commands) + 1}"
        (output / (label + ".stdout")).write_bytes(completed.stdout)
        (output / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"arguments": list(arguments), "exit_code": completed.returncode})
        if not passed:
            assert completed.returncode != 0
            assert b"lacks its bundled CAS content" in completed.stderr, completed.stderr
            return {}
        assert completed.returncode == 0, completed.stderr
        value = json.loads(completed.stdout)
        assert isinstance(value, dict)
        return value

    bundle, restored = output / "reference-bundle", output / "reference-restored"
    created = invoke(
        "backup",
        "--profile",
        str(profile),
        "--destination",
        str(bundle),
        "--command-id",
        "original-reference-backup",
    )
    invoke("verify", "--bundle", str(bundle), "--manifest-digest", created["manifest_digest"])
    receipt = invoke(
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        created["manifest_digest"],
        "--destination",
        str(restored),
        "--command-id",
        "original-reference-restore",
    )
    recovered = restored / "stores/provider"
    log = (output / "provider-restored.stderr").open("wb")
    try:
        process = start(recovered, inputs, log)
        try:
            assert request(recovered / "local.sock", "/v1/content/references", query, "GET") == (
                200,
                acquired,
            )
        finally:
            assert stop(process, recovered / "local.sock") == 0
    finally:
        log.close()
    checksum = acquired["content_digest"]
    content = recovered / "content" / checksum[7:]
    assert "sha256:" + hashlib.sha256(content.read_bytes()).hexdigest() == checksum
    # Corrupt only the owned restored copy: the original provider keeps all its bytes and history.
    content.unlink()
    selected["components"][0]["root"] = str(recovered)
    missing_profile = output / "missing-reference-profile.json"
    save(missing_profile, selected)
    invoke(
        "backup",
        "--profile",
        str(missing_profile),
        "--destination",
        str(output / "missing-backup"),
        "--command-id",
        "missing-content",
        passed=False,
    )
    assert not (output / "missing-backup").exists()
    # A manifest cannot bless a missing referenced object merely by omitting its file row.
    tampered = output / "missing-reference-bundle"
    shutil.copytree(bundle, tampered)
    manifest = json.loads((tampered / "manifest.json").read_bytes())
    relative = "stores/provider/content/" + checksum[7:]
    (tampered / relative).unlink()
    manifest["files"] = [row for row in manifest["files"] if row["path"] != relative]
    raw = canonical(manifest)
    (tampered / "manifest.json").write_bytes(raw)
    repinned = "sha256:" + hashlib.sha256(raw).hexdigest()
    invoke("verify", "--bundle", str(tampered), "--manifest-digest", repinned, passed=False)
    invoke(
        "restore",
        "--bundle",
        str(tampered),
        "--manifest-digest",
        repinned,
        "--destination",
        str(output / "missing-restore"),
        "--command-id",
        "missing-restore",
        passed=False,
    )
    assert not (output / "missing-restore").exists()
    result = {
        "active_reference_restored_via_actual_provider": True,
        "actual_restored_content_digest": checksum,
        "missing_active_content_backup_rejected": True,
        "repinned_missing_content_verify_and_restore_rejected": True,
        "restore_receipt": receipt,
        "commands": commands,
    }
    save(output / "reference-recovery-result.json", result)
    return result


def run(output: Path) -> dict[str, Any]:
    """Exercise acquire, observe, release and replay across two actual provider restarts."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    inputs = output / "input"
    inputs.mkdir(mode=0o700)
    source = inputs / "actual-file.bin"
    source.write_bytes(b"actual local Memory reference lifecycle bytes\n")
    root = output / "provider"
    logs: list[BinaryIO] = []
    processes: list[subprocess.Popen[bytes]] = []
    socket_path = root / "local.sock"
    paths = [
        Path(__file__),
        *sorted((PROVIDER / "roboguide_memory_provider").glob("*.py")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
    ]
    save(
        output / "source-identity.json",
        {str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    )

    def launch(label: str) -> subprocess.Popen[bytes]:
        """Start and retain one owned actual provider process and its stderr evidence."""
        log = (output / f"{label}.stderr").open("wb")
        logs.append(log)
        process = start(root, inputs, log)
        processes.append(process)
        return process

    try:
        first = launch("provider-first")
        status, identity = request(socket_path, "/v1/instance", {}, "GET")
        assert status == 200 and identity["provider_id"] == "local-files"
        imported = _import_fixture(source, identity["provider_id"], identity["instance_id"])
        status, imported_receipt = request(
            socket_path,
            "/v1/import",
            {
                "memory_import": imported["original"],
                "memory_manifest": imported["manifest"],
                "staged_artifact": str(source),
            },
            "POST",
        )
        assert status == 200 and imported_receipt["result"]["outcome"] == "completed"
        reference: dict[str, Any] = {
            "schema": "roboguide.content-reference-command/v0.1",
            "operation": "acquire",
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            "reference_id": "execution-" + uuid.uuid4().hex,
            "content_digest": imported["artifact"]["content_digest"],
            "kind": "lineage",
            "owner": imported["original"]["import_id"],
            "generation": 7,
            "byte_size": imported["artifact"]["byte_size"],
        }
        status, acquired = request(socket_path, "/v1/content/references", reference, "POST")
        assert status == 200 and acquired["state"] == "active"
        assert request(socket_path, "/v1/content/references", reference, "POST") == (
            200,
            acquired,
        )
        query = {
            "schema": "roboguide.content-reference-query/v0.1",
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            "reference_id": reference["reference_id"],
        }
        assert request(socket_path, "/v1/content/references", query, "GET") == (
            200,
            acquired,
        )
        reclamation = {
            "schema": "roboguide.content-reclamation-command/v0.1",
            "command_id": "memory-reference-reclaim-" + uuid.uuid4().hex,
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            "content_digest": imported["artifact"]["content_digest"],
            "byte_size": imported["artifact"]["byte_size"],
        }
        status, blocked = request(socket_path, "/v1/content/tombstone", reclamation, "POST")
        assert status == 200 and blocked["state"] == "blocked"
        assert (
            "lineage:" + reference["owner"] + ":" + reference["reference_id"]
            in (blocked["reasons"])
        )
        first_exit = stop(first, socket_path)
        assert first_exit == 0
        recovery = check_recovery(output, root, inputs, query, acquired)

        second = launch("provider-second")
        status, identity_after_restart = request(socket_path, "/v1/instance", {}, "GET")
        assert status == 200 and identity_after_restart == identity
        assert request(socket_path, "/v1/content/references", query, "GET") == (
            200,
            acquired,
        )
        release = {**reference, "operation": "release"}
        release.pop("byte_size")
        status, released = request(socket_path, "/v1/content/references", release, "POST")
        assert status == 200 and released["state"] == "released"
        assert request(socket_path, "/v1/content/references", release, "POST") == (
            200,
            released,
        )
        status, stale_acquire = request(socket_path, "/v1/content/references", reference, "POST")
        assert status == 200 and stale_acquire == released
        second_exit = stop(second, socket_path)
        assert second_exit == 0

        with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            row = connection.execute(
                "SELECT content_digest,kind,owner,generation,active "
                "FROM content_references WHERE reference_id=?",
                (reference["reference_id"],),
            ).fetchone()
        assert version == 3 and row == (
            reference["content_digest"],
            reference["kind"],
            reference["owner"],
            reference["generation"],
            0,
        )
        result = {
            "passed": True,
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            "provider_schema_version": version,
            "acquired_generation": acquired["generation"],
            "restart_preserved_active_reference": True,
            "release_replay_state": released["state"],
            "stale_acquire_did_not_reactivate": stale_acquire["state"] == "released",
            "content_reclamation_blocked_by_original_reference": True,
            "sqlite_projection_active": row[4],
            "reference_recovery": recovery,
            "first_process_exit": first_exit,
            "second_process_exit": second_exit,
            "registered_nodes": 0,
            "device_facts": 0,
            "models": 0,
            "simulators": 0,
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    stop(process, socket_path)
                except (OSError, TimeoutError, ValueError):
                    process.kill()
                    process.wait(timeout=10)
        for log in logs:
            log.close()


def main() -> int:
    """Run one isolated actual-process conformance case and print its evidence path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = run(arguments.output)
    print(json.dumps(result, sort_keys=True))
    print("evidence=" + str(arguments.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
