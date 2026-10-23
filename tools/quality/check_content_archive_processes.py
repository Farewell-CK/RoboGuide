"""Check actual zero-Node mTLS Archive publication, SQL interruption and original cold recovery."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
from contextlib import closing
from pathlib import Path
from typing import Any

from archive_abort_processes import run as run_archive_abort
from check_artifact_authority_processes import call as http_call
from check_artifact_authority_processes import grant
from check_identity_processes import ROOT, client, pki, port, stop
from check_state_verifier_processes import save
from replica_producer_processes import run as run_original_producers
from replica_snapshot_processes import run as run_snapshot_producers
from replica_state_materialization_processes import run as run_state_materialization


def run(
    directory: Path,
    skip_build: bool,
    old_controller: Path | None,
    old_recovery_ref: str | None,
    abort_unpublished: bool = False,
    upgrade_original: bool = False,
    replica_state_probe: Path | None = None,
    replica_snapshot_probe: Path | None = None,
    original_producer_journal: bool = False,
    old_replica_snapshot_probe: Path | None = None,
    controller_storage_v1: bool = False,
    upgrade_controller_storage: bool = False,
) -> dict[str, Any]:
    """Retain actual software process, file, SQL and TLS evidence with zero Nodes."""
    if original_producer_journal and replica_snapshot_probe is None:
        raise ValueError(
            "original producer journal acceptance requires the explicit maintained Snapshot probe"
        )
    if old_replica_snapshot_probe is not None and not original_producer_journal:
        raise ValueError(
            "original historical Snapshot probe requires the explicit producer journal gate"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
            timeout=240,
        )
    binary = ROOT / "target/debug/integration-server"
    source_paths = [
        *sorted((ROOT / "apps/integration-server/src").rglob("*.rs")),
        *sorted((ROOT / "core/state/src").rglob("*.rs")),
        *sorted((ROOT / "core/artifact-store/src").rglob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        ROOT / "Cargo.lock",
        Path(__file__),
        Path(__file__).with_name("archive_abort_processes.py"),
        Path(__file__).with_name("check_artifact_authority_processes.py"),
        Path(__file__).with_name("replica_state_materialization_processes.py"),
        Path(__file__).with_name("replica_snapshot_processes.py"),
        Path(__file__).with_name("replica_producer_processes.py"),
        Path(__file__).with_name("check_replica_content_processes.py"),
        Path(__file__).with_name("replica_content_upgrade_processes.py"),
        *sorted((ROOT / "core/controller-replication/src").rglob("*.rs")),
        *sorted((ROOT / "core/controller-replication/examples").glob("*.rs")),
        ROOT / "core/state/examples/replica_state_probe.rs",
    ]
    frozen = {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }
    save(directory / "source-identity.json", frozen)
    binary_digest = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    schema = subprocess.check_output(
        [str(binary), "--application-schema"], cwd=ROOT, text=True
    ).strip()
    if schema not in {
        "roboguide.controller-checkpoint/v49",
        "roboguide.controller-checkpoint/v50",
        "roboguide.controller-checkpoint/v51",
        "roboguide.controller-checkpoint/v52",
    }:
        raise ValueError("unsupported explicit original Archive process profile")
    cleanup_authority = schema in {
        "roboguide.controller-checkpoint/v50",
        "roboguide.controller-checkpoint/v51",
        "roboguide.controller-checkpoint/v52",
    }
    if abort_unpublished and not cleanup_authority:
        raise ValueError("explicit original cleanup requires wrapper v50")
    if upgrade_original and (not cleanup_authority or old_controller is None):
        raise ValueError("actual original upgrade requires the explicit previous Controller binary")
    if upgrade_controller_storage and (not controller_storage_v1 or old_controller is None):
        raise ValueError("original storage upgrade requires explicit v1 and the actual old binary")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    if controller_storage_v1:
        environment["ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA"] = "1"
    certificates = directory / "pki"
    leaves = pki(certificates)
    admin = client(certificates, "admin")
    viewer = client(certificates, "viewer")
    artifact_policy = directory / "artifact-policy.json"
    operator_policy = directory / "operator-policy.json"
    save(
        artifact_policy,
        {
            "schema_version": "roboguide.artifact-authorization/v0.1",
            "registry_id": "archive-file-authority",
            "revision": 1,
            "grants": {"sha256:" + leaves["admin"]: grant("archive-file-admin", "admin", "local")},
        },
    )
    save(
        operator_policy,
        {
            "schema_version": "roboguide.operator-authorization/v0.1",
            "registry_id": "archive-operator-authority",
            "revision": 1,
            "grants": {
                "sha256:" + leaves["admin"]: {
                    "principal_id": "archive-recovery-admin",
                    "role": "recovery-admin",
                    "tenant_ids": ["*"],
                },
                "sha256:" + leaves["viewer"]: {
                    "principal_id": "archive-viewer",
                    "role": "operator",
                    "tenant_ids": ["local"],
                },
            },
        },
    )
    for plane, policy in (("ARTIFACT", artifact_policy), ("OPERATOR", operator_policy)):
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(certificates / "server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(certificates / "server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
                f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP": str(policy),
            }
        )
    controller_root = directory / "controller"
    controller_root.mkdir()
    source = directory / "artifact"
    archive = directory / "archive"
    archive.mkdir()
    database = controller_root / "events.sqlite3"
    environment.update(
        {
            "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
            "ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY": str(archive),
            "TOKIO_WORKER_THREADS": "1",
        }
    )
    grpc_port, operator_port, artifact_port = port(), port(), port()
    operator_origin = f"https://127.0.0.1:{operator_port}"
    artifact_origin = f"https://127.0.0.1:{artifact_port}"
    arguments = [
        str(binary),
        f"127.0.0.1:{grpc_port}",
        str(database),
        f"127.0.0.1:{operator_port}",
        f"127.0.0.1:{artifact_port}",
        str(source),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    active_binary = old_controller if upgrade_original or upgrade_controller_storage else binary
    assert active_binary is not None
    original_producer_digest = "sha256:" + hashlib.sha256(active_binary.read_bytes()).hexdigest()
    original_producer_schema = subprocess.check_output(
        [str(active_binary), "--application-schema"], cwd=ROOT, text=True
    ).strip()
    trace: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []

    def call(
        origin: str,
        context: ssl.SSLContext,
        method: str,
        path: str,
        body: dict[str, Any] | bytes | None = None,
    ) -> tuple[int, bytes]:
        """Keep actual authenticated statuses and response hashes."""
        encoded = json.dumps(body).encode() if isinstance(body, dict) else body
        status, raw = http_call(
            origin,
            context,
            method,
            path,
            encoded,
            timeout_seconds=45
            if method == "POST" and path.startswith("/v1/maintenance/archive")
            else 5,
        )
        item: dict[str, Any] = {
            "origin": origin,
            "caller": "admin" if context is admin else "viewer",
            "method": method,
            "path": path,
            "status": status,
            "response_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
        try:
            item["response_json"] = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        trace.append(item)
        save(directory / "http-trace.json", trace)
        return status, raw

    def launch(
        selected_database: Path = database,
        selected_source: Path = source,
        selected_archive: Path = archive,
    ) -> subprocess.Popen[bytes]:
        """Start only the actual Controller, retaining its own logs and real readiness outcome."""
        path = directory / f"controller-{len(processes)}.log"
        with path.open("wb") as output:
            process = subprocess.Popen(
                [
                    str(active_binary),
                    arguments[1],
                    str(selected_database),
                    arguments[3],
                    arguments[4],
                    str(selected_source),
                ],
                cwd=ROOT,
                env=environment | {"ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY": str(selected_archive)},
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
            )
        processes.append(process)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"actual Controller exited; inspect {path}")
            try:
                if call(operator_origin, admin, "GET", "/readyz")[0] == 200:
                    return process
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise TimeoutError("actual Archive Controller readiness unavailable")

    def execute(
        parameters: list[str], passed: bool = True, extra_environment: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Keep actual standalone CLI/binary wait results and unmodified raw command output."""
        completed = subprocess.run(
            parameters,
            cwd=ROOT,
            env=environment | (extra_environment or {}),
            capture_output=True,
            timeout=90,
        )
        label = f"command-{len(commands)}"
        (directory / (label + ".stdout")).write_bytes(completed.stdout)
        (directory / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": parameters, "exit_code": completed.returncode})
        save(directory / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {}
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        result = json.loads(completed.stdout)
        assert isinstance(result, dict)
        return result

    def recovery(*parameters: str, passed: bool = True) -> dict[str, Any]:
        """Use the shipped independent CLI against complete externally pinned real bundle bytes."""
        return execute(
            [sys.executable, "-m", "roboguide_recovery", *parameters],
            passed,
            {"PYTHONPATH": str(ROOT / "apps/recovery-tool")},
        )

    def rows(selected_database: Path = database) -> list[tuple[Any, ...]]:
        """Read original SQL intent/state/receipt independently after production requests."""
        with closing(
            sqlite3.connect(selected_database.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            return connection.execute(
                "SELECT command_id,intent_json,state,receipt_json FROM event_archive_operations "
                "ORDER BY command_id"
            ).fetchall()

    try:
        process = launch()
        assert json.loads(call(operator_origin, admin, "GET", "/v1/inventory")[1])["nodes"] == []
        payload = (ROOT / "core/domain/src/memory.rs").read_bytes()
        checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        upload = "original-archive-file"
        assert (
            call(artifact_origin, admin, "POST", "/v1/artifact-uploads", {"upload_id": upload})[0]
            == 201
        )
        assert (
            call(artifact_origin, admin, "PUT", f"/v1/artifact-uploads/{upload}/content", payload)[
                0
            ]
            == 202
        )
        assert (
            call(
                artifact_origin,
                admin,
                "POST",
                f"/v1/artifact-uploads/{upload}/finalize",
                {"content_digest": checksum, "byte_size": len(payload)},
            )[0]
            == 201
        )
        snapshot_producers = None
        producer_journal = None
        if replica_snapshot_probe is not None:
            stop(process)
            snapshot_producers = run_snapshot_producers(
                directory / "actual-snapshot-producers",
                replica_snapshot_probe,
                ROOT / "target/debug/examples/content_store_probe",
                database,
                source,
                hashlib.sha256(b"explicit local Snapshot storage producer gate v0.1").hexdigest(),
                23,
                31,
            )
            if original_producer_journal:
                producer_journal = run_original_producers(
                    directory / "actual-original-producer-journal",
                    replica_snapshot_probe,
                    database,
                    source,
                    hashlib.sha256(b"explicit original content producer journal v0.1").hexdigest(),
                    old_replica_snapshot_probe,
                )
            process = launch()
        manifest = {
            "schema": "roboguide.memory-manifest/v0.1",
            "selector": {"memory_id": "original-source-file", "revision_id": "one"},
            "kind": "artifact",
            "provider_id": "archive-file-owner",
            "owner": {"owner": "robo_guide", "component": "archive-file-owner"},
            "scope": {"kind": "global"},
            "visibility": "exchangeable",
            "payload_schema": "opaque-file/v1",
            "media_type": "application/octet-stream",
            "artifact": {"content_digest": checksum, "byte_size": len(payload)},
            "source_mission_id": None,
            "source_execution_id": None,
            "source_task_ref": None,
            "created_at": 1,
        }
        memory_path = "/v1/memories/original-source-file/revisions/one"
        status, raw = call(artifact_origin, admin, "POST", memory_path, manifest)
        assert status == 403, raw
        status, raw = call(operator_origin, admin, "GET", "/v1/maintenance/archive")
        assert status == 200
        cutoff = json.loads(raw)["latest_sequence"]
        command = {
            "schema": "roboguide.archive-command/v0.2",
            "command_id": "original-archive",
            "end_sequence": cutoff,
            "budget_ms": 60_000,
        }
        assert call(operator_origin, viewer, "POST", "/v1/maintenance/archive", command)[0] == 403
        with closing(sqlite3.connect(database)) as connection, connection:
            before = connection.execute(
                "SELECT sequence,payload_schema,payload_json FROM events ORDER BY sequence"
            ).fetchall()
            connection.execute(
                "CREATE TRIGGER refuse_archive_floor BEFORE INSERT ON event_log_metadata "
                "WHEN NEW.key='history_floor' BEGIN SELECT RAISE(ABORT,"
                "'actual Archive SQL publication fault'); END"
            )
        assert call(operator_origin, admin, "POST", "/v1/maintenance/archive", command)[0] == 503
        original = rows()
        assert len(original) == 1 and original[0][2:] == ("pending", None)
        intent = json.loads(original[0][1])
        assert intent["checkpoint"]["schema"] == original_producer_schema
        publication = (
            archive
            / "archives-v1"
            / hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
            / ("archive-" + hashlib.sha256(b"original-archive").hexdigest())
        )
        assert (publication / "publication.json").is_file()
        target = publication / "content/objects-v1/sha256" / checksum[7:9] / checksum[7:]
        assert target.read_bytes() == payload
        original_files = {
            str(path.relative_to(publication)): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in publication.rglob("*")
            if path.is_file()
        }
        with closing(sqlite3.connect(database)) as connection, connection:
            assert (
                connection.execute(
                    "SELECT sequence,payload_schema,payload_json FROM events ORDER BY sequence"
                ).fetchall()
                == before
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='catalog_archive_events'"
            ).fetchone() == (0,)
            connection.execute("DROP TRIGGER refuse_archive_floor")
        stop(process)
        active_binary = binary
        process = launch()
        if upgrade_controller_storage:
            with closing(sqlite3.connect(database)) as connection:
                assert (
                    connection.execute(
                        "SELECT sequence,payload_schema,payload_json FROM events ORDER BY sequence"
                    ).fetchall()
                    == before
                )
            assert rows() == original
            save(directory / "original-event-rows.json", before)
        assert call(operator_origin, admin, "POST", "/v1/maintenance/archive", command)[0] == 200
        compacted = rows()
        assert compacted[0][1] == original[0][1] and compacted[0][2] == "compacted"
        assert call(operator_origin, admin, "POST", "/v1/maintenance/archive", command)[0] == 200
        assert rows() == compacted
        assert {
            str(path.relative_to(publication)): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in publication.rglob("*")
            if path.is_file()
        } == original_files
        assert call(artifact_origin, admin, "GET", "/v1/artifact-uploads/" + upload)[0] == 200
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum) == (200, payload)
        stop(process)
        process = launch()
        assert call(artifact_origin, admin, "GET", "/v1/artifact-uploads/" + upload)[0] == 200
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum) == (200, payload)
        assert json.loads(call(operator_origin, admin, "GET", "/v1/inventory")[1])["nodes"] == []
        abort_result = None
        if abort_unpublished:

            def restart_cleanup_controller() -> None:
                """Reap the original Controller before acquiring new actual writers."""
                nonlocal process
                stop(process)
                assert process.poll() is not None
                process = launch()

            abort_result = run_archive_abort(
                directory,
                operator_origin,
                artifact_origin,
                admin,
                viewer,
                call,
                restart_cleanup_controller,
                command["command_id"],
            )
            compacted = rows()
        stop(process)
        state_materialization = None
        if replica_state_probe is not None:
            state_materialization = run_state_materialization(
                directory,
                replica_state_probe,
                database,
                source,
                archive,
                launch,
                execute,
                call,
                artifact_origin,
                operator_origin,
                admin,
                upload,
                checksum,
                payload,
            )
        profile: dict[str, Any] = {
            "schema": "roboguide.recovery-profile/v0.16"
            if schema == "roboguide.controller-checkpoint/v52"
            else "roboguide.recovery-profile/v0.15"
            if schema == "roboguide.controller-checkpoint/v51"
            else "roboguide.recovery-profile/v0.13"
            if cleanup_authority
            else "roboguide.recovery-profile/v0.12",
            "profile_id": "original-content-archive",
            "revision": 1,
            "max_bytes": 32 * 1024 * 1024,
            "max_files": 512,
            "budget_seconds": 60,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "controller",
                    "kind": "controller",
                    "root": str(controller_root),
                    "database": "events.sqlite3",
                    "trees": [],
                    "depends_on": [],
                },
                {
                    "component_id": "artifact",
                    "kind": "artifact",
                    "root": str(source),
                    "database": None,
                    "trees": [],
                    "depends_on": ["controller"],
                },
                {
                    "component_id": "archive",
                    "kind": "archive",
                    "root": str(archive),
                    "database": None,
                    "trees": [],
                    "depends_on": ["controller", "artifact"],
                },
            ],
        }
        if controller_storage_v1:
            profile["schema"] = "roboguide.recovery-profile/v0.17"
            for component in profile["components"]:
                if component["kind"] == "controller":
                    component["storage_schema"] = "roboguide.controller-storage/v1"
        if schema == "roboguide.controller-checkpoint/v52":
            for component in profile["components"]:
                if component["kind"] == "artifact":
                    component["storage_schema"] = "roboguide.artifact-storage/v3"
        profile_path = directory / "profile.json"
        save(profile_path, profile)
        if controller_storage_v1:
            previous = json.loads(json.dumps(profile))
            previous["schema"] = "roboguide.recovery-profile/v0.16"
            for component in previous["components"]:
                if component["kind"] == "controller":
                    component.pop("storage_schema")
            previous_path = directory / "previous-controller-profile.json"
            save(previous_path, previous)
            recovery(
                "backup",
                "--profile",
                str(previous_path),
                "--destination",
                str(directory / "previous-controller-profile-refused"),
                "--command-id",
                "previous-controller-profile",
                passed=False,
            )
        # Source Catalog evidence, Archive destination and known consumers are all mandatory.
        for label, components in (
            ("missing-archive", profile["components"][:2]),
            ("missing-controller", [profile["components"][1] | {"depends_on": []}]),
        ):
            broken = directory / (label + ".json")
            save(broken, profile | {"components": components})
            recovery(
                "backup",
                "--profile",
                str(broken),
                "--destination",
                str(directory / label),
                "--command-id",
                label,
                passed=False,
            )
            assert not (directory / label).exists()
        bundle = directory / "bundle"
        created = recovery(
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(bundle),
            "--command-id",
            "original-cold-copy",
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
            "restore-original",
        )
        assert receipt["schema"] == (
            "roboguide.restore-receipt/v0.17"
            if controller_storage_v1
            else "roboguide.restore-receipt/v0.16"
            if schema == "roboguide.controller-checkpoint/v52"
            else "roboguide.restore-receipt/v0.15"
            if schema == "roboguide.controller-checkpoint/v51"
            else "roboguide.restore-receipt/v0.13"
            if cleanup_authority
            else "roboguide.restore-receipt/v0.12"
        )
        assert rows(restored / "stores/controller/events.sqlite3") == compacted
        process = launch(
            restored / "stores/controller/events.sqlite3",
            restored / "stores/artifact",
            restored / "stores/archive",
        )
        assert call(artifact_origin, admin, "GET", "/v1/artifact-uploads/" + upload)[0] == 200
        assert call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum) == (200, payload)
        stop(process)
        # Removing one receiving CAS entry still refuses after re-pinning the owned bundle.
        saved_manifest = json.loads((bundle / "manifest.json").read_bytes())
        target_name = "stores/archive/" + target.relative_to(archive).as_posix()
        target_raw = (bundle / target_name).read_bytes()
        (bundle / target_name).unlink()
        changed = dict(
            saved_manifest,
            files=[row for row in saved_manifest["files"] if row["path"] != target_name],
        )
        changed_raw = json.dumps(
            changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        (bundle / "manifest.json").write_bytes(changed_raw)
        recovery(
            "verify",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            "sha256:" + hashlib.sha256(changed_raw).hexdigest(),
            passed=False,
        )
        (bundle / target_name).write_bytes(target_raw)
        (bundle / "manifest.json").write_bytes(
            json.dumps(
                saved_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        )
        recovery("verify", "--bundle", str(bundle), "--manifest-digest", created["manifest_digest"])
        binding = (
            "stores/artifact/reference-authorities-v1/controllers/"
            + intent["request"]["authority_id"].split(":", 1)[1]
            + ".json"
        )
        binding_raw = (bundle / binding).read_bytes()
        (bundle / binding).unlink()
        missing_binding = dict(
            saved_manifest, files=[row for row in saved_manifest["files"] if row["path"] != binding]
        )
        missing_raw = json.dumps(
            missing_binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        (bundle / "manifest.json").write_bytes(missing_raw)
        recovery(
            "verify",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            "sha256:" + hashlib.sha256(missing_raw).hexdigest(),
            passed=False,
        )
        assert (
            b"original known Artifact consumer record is missing"
            in (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
        )
        (bundle / binding).write_bytes(binding_raw)
        (bundle / "manifest.json").write_bytes(
            json.dumps(
                saved_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        )
        recovery("verify", "--bundle", str(bundle), "--manifest-digest", created["manifest_digest"])
        if abort_result is not None:
            selected = next(
                row
                for row in saved_manifest["files"]
                if row["path"].endswith(
                    "abort-" + hashlib.sha256(b"original-current-cleanup").hexdigest() + ".json"
                )
            )
            tombstone_name = selected["path"]
            tombstone_bytes = (bundle / tombstone_name).read_bytes()
            (bundle / tombstone_name).unlink()
            missing_tombstone = dict(
                saved_manifest,
                files=[row for row in saved_manifest["files"] if row["path"] != tombstone_name],
            )
            changed_raw = json.dumps(
                missing_tombstone, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            (bundle / "manifest.json").write_bytes(changed_raw)
            recovery(
                "verify",
                "--bundle",
                str(bundle),
                "--manifest-digest",
                "sha256:" + hashlib.sha256(changed_raw).hexdigest(),
                passed=False,
            )
            assert (
                b"lost its durable tombstone"
                in (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
            )
            (bundle / tombstone_name).write_bytes(tombstone_bytes)
            (bundle / "manifest.json").write_bytes(
                json.dumps(
                    saved_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            )
            recovery(
                "verify", "--bundle", str(bundle), "--manifest-digest", created["manifest_digest"]
            )
        old_writer = None
        if old_controller is not None:
            old_root = directory / "old-writer"
            old_root.mkdir()
            old_database = old_root / "events.sqlite3"
            with (
                closing(sqlite3.connect(database)) as original_sql,
                closing(sqlite3.connect(old_database)) as copied_sql,
            ):
                original_sql.backup(copied_sql)
            execute(
                [
                    str(old_controller),
                    arguments[1],
                    str(old_database),
                    arguments[3],
                    arguments[4],
                    str(old_root / "fresh-cas"),
                ],
                False,
                {"ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v2"},
            )
            old_error = (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
            assert (
                b"views may not be indexed" if controller_storage_v1 else schema.encode()
            ) in old_error
            old_writer = {
                "binary_digest": "sha256:"
                + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
                "actual_exit_code": commands[-1]["exit_code"],
            }
        old_cli = None
        if old_recovery_ref is not None:
            extracted = subprocess.run(
                ["git", "archive", old_recovery_ref, "apps/recovery-tool/roboguide_recovery"],
                cwd=ROOT,
                capture_output=True,
                check=True,
                timeout=30,
            )
            old_root = directory / "old-cli"
            old_root.mkdir()
            with tarfile.open(fileobj=io.BytesIO(extracted.stdout)) as archive_file:
                for member in archive_file.getmembers():
                    if (
                        not (member.isfile() or member.isdir())
                        or ".." in Path(member.name).parts
                        or Path(member.name).is_absolute()
                    ):
                        raise ValueError("unsafe old CLI source archive")
                    archive_file.extract(member, old_root, filter="data")
            legacy = directory / "old-cli-profile.json"
            old_components = json.loads(json.dumps(profile["components"][:2]))
            for component in old_components:
                component.pop("storage_schema", None)
            save(
                legacy,
                profile
                if controller_storage_v1
                else profile
                | {
                    "schema": "roboguide.recovery-profile/v0.12"
                    if cleanup_authority
                    else "roboguide.recovery-profile/v0.11",
                    "components": old_components,
                },
            )
            execute(
                [
                    sys.executable,
                    "-m",
                    "roboguide_recovery",
                    "backup",
                    "--profile",
                    str(legacy),
                    "--destination",
                    str(directory / "old-cli-refused"),
                    "--command-id",
                    "old-cli",
                ],
                False,
                {"PYTHONPATH": str(old_root / "apps/recovery-tool")},
            )
            assert any(
                message in (directory / f"command-{len(commands) - 1}.stderr").read_bytes()
                for message in (
                    b"owner metadata is invalid",
                    b"Archive journal marker",
                    b"wrapper v49",
                    b"original Controller reference authority",
                    b"recovery profile is not closed or versioned",
                )
            )
            old_cli = {
                "source_commit": old_recovery_ref,
                "actual_exit_code": commands[-1]["exit_code"],
            }
        assert {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths
        } == frozen
        assert "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest() == binary_digest
        if upgrade_original or upgrade_controller_storage:
            assert old_controller is not None
            assert (
                "sha256:" + hashlib.sha256(old_controller.read_bytes()).hexdigest()
                == original_producer_digest
            )
        result = {
            "passed": True,
            "binary_digest": binary_digest,
            "original_owner": intent["request"]["source_owner"],
            "original_authority": intent["request"]["authority_id"],
            "actual_uploads": 2 if abort_result is not None else 1,
            "original_archive_abort": abort_result,
            "complete_original_state_materialization": state_materialization,
            "actual_original_snapshot_storage_producers": snapshot_producers,
            "actual_original_producer_journal": producer_journal,
            "actual_original_wrapper_upgrade": upgrade_original,
            "actual_controller_storage_v1": controller_storage_v1,
            "actual_original_controller_storage_upgrade": upgrade_controller_storage,
            "original_event_rows_verified_after_storage_upgrade": len(before)
            if upgrade_controller_storage
            else None,
            "controller_processes": [
                {"pid": child.pid, "arguments": child.args, "native_wait": child.poll()}
                for child in processes
            ],
            "original_producer_binary_digest": original_producer_digest,
            "original_embedded_checkpoint_schema": intent["checkpoint"]["schema"],
            "actual_catalog_publications": 0,
            "actual_archive_sql_failure": True,
            "exact_retry_copied_files": 0,
            "original_budget_unchanged": True,
            "upload_catalog_replayed_after_actual_restart": True,
            "cold_restore_catalog_and_archive": True,
            "receiving_missing_bytes_refused_after_manifest_repin": True,
            "undeclared_archive_and_consumer_refused": True,
            "old_controller": old_writer,
            "old_cli": old_cli,
            "registered_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
        }
        save(directory / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)


def main() -> None:
    """Run only an explicitly owned software/file acceptance; hardware remains unavailable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--abort-unpublished", action="store_true")
    parser.add_argument("--upgrade-original", action="store_true")
    parser.add_argument("--old-controller", type=Path)
    parser.add_argument("--old-recovery-ref")
    parser.add_argument("--replica-state-probe", type=Path)
    parser.add_argument("--replica-snapshot-probe", type=Path)
    parser.add_argument("--original-producer-journal", action="store_true")
    parser.add_argument("--old-replica-snapshot-probe", type=Path)
    parser.add_argument("--controller-storage-v1", action="store_true")
    parser.add_argument("--upgrade-controller-storage", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output.absolute(),
                args.skip_build,
                args.old_controller,
                args.old_recovery_ref,
                args.abort_unpublished,
                args.upgrade_original,
                args.replica_state_probe.resolve()
                if args.replica_state_probe is not None
                else None,
                args.replica_snapshot_probe.resolve()
                if args.replica_snapshot_probe is not None
                else None,
                args.original_producer_journal,
                args.old_replica_snapshot_probe.resolve()
                if args.old_replica_snapshot_probe is not None
                else None,
                args.controller_storage_v1,
                args.upgrade_controller_storage,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
