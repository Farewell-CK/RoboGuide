"""Check actual Artifact owner migration, mTLS CAS, old-writer fence and complete cold restore."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import call as artifact_call
from check_artifact_authority_processes import grant
from check_identity_processes import ROOT, client, pki, port, request, stop
from check_state_verifier_processes import save


def run(
    directory: Path,
    skip_build: bool,
    old_controller: Path | None,
    old_recovery_ref: str | None = None,
    reference_journal: bool = False,
) -> dict[str, Any]:
    """Exercise real storage, Controller and CLI processes with zero registered Nodes."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
            timeout=240,
        )
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "artifact-store", "--example", "layout_probe"],
            cwd=ROOT,
            check=True,
            timeout=180,
        )
    binary = ROOT / "target/debug/integration-server"
    probe = ROOT / "target/debug/examples/layout_probe"
    inputs = ROOT / "core/domain/src/memory.rs"
    payload = inputs.read_bytes()
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    source = directory / "artifacts"
    source_paths = [
        *sorted((ROOT / "core/artifact-store/src").rglob("*.rs")),
        ROOT / "apps/integration-server/src/main.rs",
        ROOT / "apps/integration-server/src/artifact_http/server.rs",
        ROOT / "core/artifact-store/examples/layout_probe.rs",
        ROOT / "core/artifact-store/Cargo.toml",
        ROOT / "Cargo.lock",
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        Path(__file__),
    ]
    frozen = {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    save(directory / "source-identity.json", frozen)
    commands: list[dict[str, Any]] = []
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }

    def execute(
        arguments: list[str],
        *,
        passed: bool = True,
        selected_environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Retain exact argv, raw output and real process exits without synthetic pass receipts."""
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env=selected_environment or environment,
            capture_output=True,
            timeout=75,
        )
        label = f"command-{len(commands)}"
        (directory / (label + ".stdout")).write_bytes(completed.stdout)
        (directory / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"arguments": arguments, "exit_code": completed.returncode})
        save(directory / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {}
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        result = json.loads(completed.stdout)
        assert isinstance(result, dict)
        return result

    original = execute([str(probe), "legacy", str(source), str(inputs)])
    assert original["owner_id"] is None
    assert original["artifact"] == {"content_digest": checksum, "byte_size": len(payload)}
    blob = source / "blobs/sha256" / checksum[7:9] / checksum[7:]
    actual_inode = blob.stat().st_ino
    migrated = execute([str(probe), "retained", str(source)])
    owner_id = migrated["owner_id"]
    retained_blob = source / "objects-v1/sha256" / checksum[7:9] / checksum[7:]
    assert retained_blob.stat().st_ino == actual_inode and retained_blob.read_bytes() == payload
    assert (source / "blobs").is_file()
    assert execute([str(probe), "retained", str(source)])["owner_id"] == owner_id
    if reference_journal:
        assert execute([str(probe), "references", str(source)])["owner_id"] == owner_id
        assert execute([str(probe), "references", str(source)])["owner_id"] == owner_id
        execute([str(probe), "retained", str(source)], passed=False)
    execute([str(probe), "legacy", str(source)], passed=False)
    certificates = directory / "pki"
    digests = pki(certificates)
    admin, viewer = client(certificates, "admin"), client(certificates, "viewer")
    trace: list[dict[str, Any]] = []

    def call(
        origin: str,
        context: ssl.SSLContext,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Retain actual TLS outcomes and independent hashes without dumping payload bytes."""
        status, response = artifact_call(origin, context, method, path, body, headers)
        record: dict[str, Any] = {
            "origin": origin,
            "method": method,
            "path": path,
            "caller": "admin" if context is admin else "viewer",
            "status": status,
            "response_size": len(response),
            "response_digest": "sha256:" + hashlib.sha256(response).hexdigest(),
        }
        try:
            record["response_json"] = json.loads(response)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        trace.append(record)
        save(directory / "http-trace.json", trace)
        return status, response

    artifact_profile = {
        "schema_version": "roboguide.artifact-authorization/v0.1",
        "registry_id": "actual-artifact-owner",
        "revision": 1,
        "grants": {
            "sha256:" + digests["admin"]: grant("artifact-owner-admin", "admin", "local"),
            "sha256:" + digests["viewer"]: grant("owner-viewer-denied", "viewer", "foreign"),
        },
    }
    operator_profile = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": "actual-owner-operator",
        "revision": 1,
        "grants": {
            "sha256:" + digests["admin"]: {
                "principal_id": "owner-recovery-admin",
                "role": "recovery-admin",
                "tenant_ids": ["*"],
            }
        },
    }
    artifact_policy, operator_policy = directory / "artifact.json", directory / "operator.json"
    save(artifact_policy, artifact_profile)
    save(operator_policy, operator_profile)
    for plane, policy in (("ARTIFACT", artifact_policy), ("OPERATOR", operator_policy)):
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(certificates / "server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(certificates / "server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
                f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP": str(policy),
            }
        )
    storage_schema = (
        "roboguide.artifact-storage/v2" if reference_journal else "roboguide.artifact-storage/v1"
    )
    environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = storage_schema
    environment["TOKIO_WORKER_THREADS"] = "1"
    control_port, artifact_port, grpc_port = port(), port(), port()
    control_origin, artifact_origin = (
        f"https://127.0.0.1:{control_port}",
        f"https://127.0.0.1:{artifact_port}",
    )
    database = directory / "events.sqlite3"
    arguments = [
        str(binary),
        f"127.0.0.1:{grpc_port}",
        str(database),
        f"127.0.0.1:{control_port}",
        f"127.0.0.1:{artifact_port}",
        str(source),
    ]
    processes: list[subprocess.Popen[bytes]] = []

    def launch(root: Path) -> subprocess.Popen[bytes]:
        """Start the shipped actual Controller against the selected original or restored CAS."""
        path = directory / f"controller-{len(processes)}.log"
        with path.open("wb") as output:
            process = subprocess.Popen(
                [*arguments[:-1], str(root)],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
            )
        processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"actual Controller exited; inspect {path}")
            try:
                if request(control_origin, admin, "/healthz")[0] == 200:
                    return process
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise TimeoutError("actual retained Controller did not become ready")

    recovery_environment = dict(environment, PYTHONPATH=str(ROOT / "apps/recovery-tool"))

    def recovery(*parameters: str, passed: bool = True) -> dict[str, Any]:
        """Use the independent shipped CLI with actual externally pinned owner/content bytes."""
        return execute(
            [sys.executable, "-m", "roboguide_recovery", *parameters],
            passed=passed,
            selected_environment=recovery_environment,
        )

    try:
        process = launch(source)
        status, observation = call(artifact_origin, admin, "GET", "/v1/artifact-store")
        observed = json.loads(observation)
        assert status == 200 and observed["owner_id"] == owner_id
        assert observed["storage_schema"] == storage_schema
        assert (
            call(
                artifact_origin,
                viewer,
                "GET",
                "/v1/artifact-store",
                headers={"X-RoboGuide-Role": "admin"},
            )[0]
            == 403
        )
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum) == (200, payload)
        upload_id = "cert-" + digests["admin"] + "-actual-owner"
        extra = payload + b"\nactual retained Controller upload\n"
        extra_digest = "sha256:" + hashlib.sha256(extra).hexdigest()
        assert (
            call(
                artifact_origin,
                admin,
                "POST",
                "/v1/artifact-uploads",
                json.dumps({"upload_id": upload_id}).encode(),
            )[0]
            == 201
        )
        assert (
            call(
                artifact_origin,
                admin,
                "POST",
                "/v1/artifact-uploads/" + upload_id + "/content",
                extra,
            )[0]
            == 202
        )
        finalized = call(
            artifact_origin,
            admin,
            "POST",
            "/v1/artifact-uploads/" + upload_id + "/finalize",
            json.dumps({"content_digest": extra_digest, "byte_size": len(extra)}).encode(),
        )
        assert finalized[0] == 201
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + extra_digest) == (200, extra)
        assert request(control_origin, admin, "/v1/inventory")[1]["nodes"] == []
        stop(process)
        profile: dict[str, Any] = {
            "schema": "roboguide.recovery-profile/v0.11"
            if reference_journal
            else "roboguide.recovery-profile/v0.9",
            "profile_id": "actual-artifact-owner",
            "revision": 1,
            "max_bytes": 16 * 1024 * 1024,
            "max_files": 128,
            "budget_seconds": 30,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "artifact",
                    "kind": "artifact",
                    "root": str(source),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                }
            ],
        }
        profile_path = directory / "recovery-profile.json"
        save(profile_path, profile)
        old_profile = directory / "legacy-recovery-profile.json"
        save(old_profile, profile | {"schema": "roboguide.recovery-profile/v0.8"})
        recovery(
            "backup",
            "--profile",
            str(old_profile),
            "--destination",
            str(directory / "legacy-refused"),
            "--command-id",
            "legacy",
            passed=False,
        )
        assert not (directory / "legacy-refused").exists()
        old_recovery = None
        if old_recovery_ref is not None:
            if re.fullmatch(r"[0-9a-f]{40}", old_recovery_ref) is None:
                raise ValueError("old recovery requires an exact source commit")
            archive = subprocess.run(
                ["git", "archive", old_recovery_ref, "apps/recovery-tool/roboguide_recovery"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                timeout=30,
            )
            old_source = directory / "old-recovery-source"
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as files:
                for member in files:
                    if not member.isfile() or not member.name.endswith(".py"):
                        continue
                    selected = Path(member.name)
                    if selected.is_absolute() or ".." in selected.parts:
                        raise ValueError("old recovery source archive is unsafe")
                    content = files.extractfile(member)
                    assert content is not None
                    target = old_source / selected
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target.write_bytes(content.read())
            old_environment = dict(environment, PYTHONPATH=str(old_source / "apps/recovery-tool"))
            old_cli_profile = old_profile
            if reference_journal:
                old_cli_profile = directory / "original-v1-profile.json"
                save(old_cli_profile, profile | {"schema": "roboguide.recovery-profile/v0.10"})
            execute(
                [
                    sys.executable,
                    "-m",
                    "roboguide_recovery",
                    "backup",
                    "--profile",
                    str(old_cli_profile),
                    "--destination",
                    str(directory / "old-cli-refused"),
                    "--command-id",
                    "original-old-cli",
                ],
                passed=False,
                selected_environment=old_environment,
            )
            error = (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
            assert (
                b"Artifact original owner metadata is invalid" in error
                if reference_journal
                else b"must be real directories" in error
            ), error
            assert not (directory / "old-cli-refused").exists()
            old_recovery = {
                "revision": old_recovery_ref,
                "exit_code": commands[-1]["exit_code"],
                "new_layout_refused_before_publication": True,
            }
        old_proof = None
        if old_controller is not None:
            old_arguments = [str(old_controller.resolve(strict=True)), *arguments[1:]]
            old_environment = dict(environment)
            if reference_journal:
                old_environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = (
                    "roboguide.artifact-storage/v1"
                )
            execute(old_arguments, passed=False, selected_environment=old_environment)
            error = (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
            assert (
                b"original owner identity is invalid" in error
                or b"artifact directory component must be a directory" in error
            ), error
            old_proof = {
                "binary_digest": "sha256:"
                + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
                "exit_code": commands[-1]["exit_code"],
                "original_owner_preserved": True,
            }
            assert (
                json.loads((source / ".retention-owner.json").read_bytes())["owner_id"] == owner_id
            )
        bundle = directory / "bundle"
        created = recovery(
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(bundle),
            "--command-id",
            "original",
        )
        recovery("verify", "--bundle", str(bundle), "--manifest-digest", created["manifest_digest"])
        restored = directory / "restored"
        receipt = recovery(
            "restore",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            created["manifest_digest"],
            "--destination",
            str(restored),
            "--command-id",
            "restore",
        )
        assert receipt["schema"] == (
            "roboguide.restore-receipt/v0.11"
            if reference_journal
            else "roboguide.restore-receipt/v0.9"
        )
        recovered = restored / "stores/artifact"
        process = launch(recovered)
        assert (
            json.loads(call(artifact_origin, admin, "GET", "/v1/artifact-store")[1])["owner_id"]
            == owner_id
        )
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum) == (200, payload)
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + extra_digest) == (200, extra)
        assert request(control_origin, admin, "/v1/inventory")[1]["nodes"] == []
        stop(process)
        # Damage only this owned restored fixture; the original and its pinned bundle remain intact.
        (recovered / ".retention-owner.json").unlink()
        execute([*arguments[:-1], str(recovered)], passed=False)
        error = (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
        assert (
            b"original maintenance journal requires explicit Artifact storage v2" in error
            if reference_journal
            else b"original owner intent is missing" in error
        )
        assert not (recovered / ".retention-owner.json").exists()
        assert not (recovered / ".retention-intent.json").exists()
        missing = json.loads(json.dumps(profile))
        missing["components"][0]["root"] = str(recovered)
        missing_path = directory / "missing-profile.json"
        save(missing_path, missing)
        recovery(
            "backup",
            "--profile",
            str(missing_path),
            "--destination",
            str(directory / "missing-refused"),
            "--command-id",
            "missing",
            passed=False,
        )
        assert not (directory / "missing-refused").exists()
        assert {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        } == frozen
        result = {
            "passed": True,
            "actual_owner_id": owner_id,
            "original_maintenance_journal": reference_journal,
            "old_controller": old_proof,
            "old_recovery": old_recovery,
            "actual_original_inode_preserved": True,
            "actual_controller_uploads": 1,
            "actual_restored_content_reads": 2,
            "original_owner_preserved_after_restore": True,
            "missing_identity_never_recreated": True,
            "viewer_spoof_rejected": True,
            "registered_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "binary_digest": "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
        save(directory / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)


def main() -> None:
    """Inspect only explicitly owned process roots; all real device evidence remains pending."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--old-controller", type=Path)
    parser.add_argument("--old-recovery-ref")
    parser.add_argument("--reference-journal", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output.resolve(),
                args.skip_build,
                args.old_controller,
                args.old_recovery_ref,
                args.reference_journal,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
