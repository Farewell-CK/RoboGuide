"""Adopt actual old publications through mTLS, SQL rollback and cold recovery."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import call as transport
from check_artifact_authority_processes import grant
from check_identity_processes import client, request
from check_state_verifier_processes import address, save, stop, wait_for

ROOT = Path(__file__).resolve().parents[2]


def call(
    origin: str, context: ssl.SSLContext, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, bytes]:
    """Encode exact JSON through the actual mTLS Artifact transport without role headers."""
    return transport(
        origin, context, method, path, None if body is None else json.dumps(body).encode()
    )


def run(source: Path, output: Path, old_controller: Path | None) -> dict[str, Any]:
    """Use real old process evidence; never fabricate a publication event or alter its manifest."""
    evidence = json.loads((source / "result.json").read_bytes())
    if evidence.get("passed") is not True or evidence.get("catalog_references") is not None:
        raise ValueError("adoption requires an actual complete unaccounted publication profile")
    original_exits = json.loads((source / "owned-process-exits.json").read_bytes())
    if (
        original_exits.get("all_owned_processes_reaped") is not True
        or not original_exits["processes"]
        or not all(type(row.get("returncode")) is int for row in original_exits["processes"])
    ):
        raise ValueError("original publication processes must have actual retained wait results")
    output.mkdir(parents=True, exist_ok=False)
    pinned_paths = [
        *sorted((ROOT / "apps/integration-server/src").rglob("*.rs")),
        *sorted((ROOT / "core/state/src").rglob("*.rs")),
        *sorted((ROOT / "core/domain/src").rglob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        Path(__file__),
        ROOT / "Cargo.lock",
    ]
    frozen_source = {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in pinned_paths
    }
    save(output / "source-identity.json", frozen_source)
    working = output / "working"
    working.mkdir()
    shutil.copytree(source / "pki", working / "pki")
    database = working / "controller.sqlite3"
    archive_root = working / "content-archives"
    archive_root.mkdir()
    # Hold the actual original managed writers before a consistent SQLite backup; copying only
    # the main inode would omit a reaped writer's committed WAL and regress Mission history.
    with ExitStack() as leases:
        for path in (source / "controller.sqlite3.writer.lock", source / "cas/.writer.lock"):
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            leases.callback(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            leases.callback(fcntl.flock, descriptor, fcntl.LOCK_UN)
        shutil.copytree(source / "cas", working / "cas")
        with (
            closing(
                sqlite3.connect((source / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
            ) as retained,
            closing(sqlite3.connect(database)) as copied,
        ):
            retained.backup(copied)
    for name in ("operator-authorization.json", "artifact-authorization.json"):
        shutil.copy2(source / name, working / name)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        checkpoint = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
        integration = json.loads(checkpoint["integration_json"])
        original_publications = connection.execute(
            "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,"
            "payload_json FROM events WHERE json_type(payload_json,'$.MemoryManifestPublished') "
            "IS NOT NULL ORDER BY sequence"
        ).fetchall()
        assert original_publications
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE "
            "json_type(payload_json,'$.ArtifactCatalogReferenceTransition') IS NOT NULL"
        ).fetchone() == (0,)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = "roboguide.artifact-storage/v3"
    environment["ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY"] = str(archive_root)
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
        integration["control"]["node_identity_registry"]
    )
    environment["TOKIO_WORKER_THREADS"] = "1"
    for plane in ("NODE", "OPERATOR", "ARTIFACT"):
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(working / "pki/server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(working / "pki/server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(working / "pki/ca.crt"),
            }
        )
    for plane in ("OPERATOR", "ARTIFACT"):
        environment[f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP"] = str(
            working / (plane.lower() + "-authorization.json")
        )
    admin, viewer = client(working / "pki", "admin"), client(working / "pki", "viewer")
    foreign = client(working / "pki", "admin-next")
    grpc, operator, artifact = (address() for _ in range(3))
    operator_origin, artifact_origin = "https://" + operator, "https://" + artifact
    binary = ROOT / "target/debug/integration-server"
    frozen_binary = hashlib.sha256(binary.read_bytes()).hexdigest()
    processes: list[subprocess.Popen[bytes]] = []
    logs = []
    commands: list[dict[str, Any]] = []

    def start() -> subprocess.Popen[bytes]:
        """Start one actual upgrade incarnation and retain only its own native wait/stdio."""
        log = (output / f"controller-{len(processes)}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [str(binary), grpc, str(database), operator, artifact, str(working / "cas")],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        processes.append(process)
        wait_for(
            lambda: (
                process.poll() is None and request(operator_origin, admin, "/healthz")[0] == 200
            ),
            "actual upgraded Controller readiness",
            60,
        )
        return process

    try:
        process = start()
        registry = request(operator_origin, admin, "/v1/artifact-authorizations")[1]["registry"]
        changed = copy.deepcopy(registry)
        changed["revision"] += 1
        leaf = (
            "sha256:"
            + hashlib.sha256(
                __import__("ssl").PEM_cert_to_DER_cert((working / "pki/admin.crt").read_text())
            ).hexdigest()
        )
        changed["grants"][leaf] = grant("current-retention-administrator", "admin", "local")
        foreign_leaf = (
            "sha256:"
            + hashlib.sha256(
                ssl.PEM_cert_to_DER_cert((working / "pki/admin-next.crt").read_text())
            ).hexdigest()
        )
        changed["grants"][foreign_leaf] = grant(
            "foreign-retention-administrator", "admin", "foreign"
        )
        status, value = request(
            operator_origin,
            admin,
            "/v1/artifact-authorizations",
            {
                "command_id": "adoption-current-authority",
                "expected_revision": registry["revision"],
                "registry": changed,
            },
        )
        assert status == 200, value
        save(working / "artifact-authorization.json", changed)
        memory_registry = request(operator_origin, admin, "/v1/memory-authorizations")[1][
            "registry"
        ]
        memory_revision = memory_registry["revision"]
        memory_registry["revision"] += 1
        for policy in memory_registry["policies"].values():
            policy["principals"]["current-retention-administrator"] = ["discover"]
        status, value = request(
            operator_origin,
            admin,
            "/v1/memory-authorizations",
            {
                "command_id": "adoption-explicit-discovery-only",
                "expected_revision": memory_revision,
                "registry": memory_registry,
            },
        )
        assert status == 200, value
        save(output / "current-discovery-authorization.json", memory_registry)
        receipts = []
        for index, row in enumerate(original_publications):
            manifest = json.loads(row[-1])["MemoryManifestPublished"]["manifest"]
            selector = manifest["selector"]
            path = (
                "/v1/artifact-references/" + selector["memory_id"] + "/" + selector["revision_id"]
            )
            assert call(artifact_origin, viewer, "GET", path + "/legacy")[0] == 403
            assert call(artifact_origin, foreign, "GET", path + "/legacy")[0] == 403
            status, raw = call(artifact_origin, admin, "GET", path + "/legacy")
            assert status == 200, raw
            observed = json.loads(raw)
            assert (
                not observed["has_current_reference"] and observed["historical_publisher"] is None
            )
            command = {
                "schema": "roboguide.legacy-catalog-adoption-command/v0.1",
                "command_id": "adopt-original-" + str(index),
                "original_publication_digest": observed["original_publication_digest"],
            }
            assert call(artifact_origin, foreign, "POST", path + "/adopt", command)[0] == 403
            assert (
                call(
                    artifact_origin,
                    admin,
                    "POST",
                    path + "/adopt",
                    command | {"original_publication_digest": "sha256:" + "0" * 64},
                )[0]
                == 409
            )
            # Real SQL fails after admission/candidate hashing; old Catalog must remain unaccounted.
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TRIGGER adoption_fault BEFORE INSERT ON events "
                    "WHEN NEW.payload_schema='domain.EventPayload.json/v30' "
                    "BEGIN SELECT RAISE(ABORT,'actual adoption receipt SQL fault'); END"
                )
            assert call(artifact_origin, admin, "POST", path + "/adopt", command)[0] >= 500
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM events WHERE payload_schema IN "
                    "('domain.EventPayload.json/v29','domain.EventPayload.json/v30')"
                ).fetchone() == (index * 2,)
                connection.execute("DROP TRIGGER adoption_fault")
            stop(process)
            process = start()
            status, raw = call(artifact_origin, admin, "POST", path + "/adopt", command)
            assert status == 200, raw
            result = json.loads(raw)
            assert not result["replayed"] and not result["new_read_or_use_permission"]
            receipt = result["receipt"]
            assert receipt["historical_publisher"] is None and receipt["historical_tenant"] is None
            assert receipt["authority"]["principal_id"] == "current-retention-administrator"
            assert receipt["publication"]["payload_json"] == row[-1]
            again = json.loads(call(artifact_origin, admin, "POST", path + "/adopt", command)[1])
            assert again["replayed"] and again["receipt"] == receipt
            assert (
                call(
                    artifact_origin,
                    admin,
                    "POST",
                    path + "/adopt",
                    command | {"command_id": "another-" + str(index)},
                )[0]
                == 409
            )
            receipts.append(receipt)
            commands.append({"path": path, "command": command, "receipt": receipt})
            save(output / "commands.json", commands)
        stop(process)
        process = start()
        for item in commands:
            raw = call(artifact_origin, admin, "GET", item["path"] + "/legacy")[1]
            assert json.loads(raw)["current_adoption"] == item["receipt"]
        stop(process)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            assert (
                connection.execute(
                    "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,"
                    "payload_schema,payload_json FROM events WHERE "
                    "json_type(payload_json,'$.MemoryManifestPublished') "
                    "IS NOT NULL ORDER BY sequence"
                ).fetchall()
                == original_publications
            )
        process = start()
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            cutoff = int(connection.execute("SELECT MAX(sequence) FROM events").fetchone()[0])
        archive_command = {
            "schema": "roboguide.archive-command/v0.2",
            "command_id": "archive-original-adoption",
            "end_sequence": cutoff,
            "budget_ms": 60_000,
        }
        status, archived = request(
            operator_origin, admin, "/v1/maintenance/archive", archive_command, timeout_seconds=45
        )
        assert status == 200, archived
        stop(process)
        process = start()
        for item in commands:
            raw = call(artifact_origin, admin, "GET", item["path"] + "/legacy")[1]
            assert json.loads(raw)["current_adoption"] == item["receipt"]
        stop(process)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            originals = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT data FROM catalog_archive_events ORDER BY sequence"
                )
            ]
            assert any(
                "ArtifactCatalogLegacyAdopted" in json.loads(row["payload_json"])
                for row in originals
            )
            assert [
                (
                    row["sequence"],
                    row["event_id"],
                    row["timestamp_ms"],
                    row["correlation_id"],
                    row["causation_id"],
                    row["payload_schema"],
                    row["payload_json"],
                )
                for row in originals
                if "MemoryManifestPublished" in json.loads(row["payload_json"])
            ] == original_publications
        profile = {
            "schema": "roboguide.recovery-profile/v0.16",
            "profile_id": "legacy-adoption",
            "revision": 1,
            "max_bytes": 32 * 1024 * 1024,
            "max_files": 512,
            "budget_seconds": 60,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "controller",
                    "kind": "controller",
                    "root": str(working),
                    "database": "controller.sqlite3",
                    "trees": [],
                    "depends_on": [],
                },
                {
                    "component_id": "artifact",
                    "kind": "artifact",
                    "storage_schema": "roboguide.artifact-storage/v3",
                    "root": str(working / "cas"),
                    "database": None,
                    "trees": [],
                    "depends_on": ["controller"],
                },
                {
                    "component_id": "archive",
                    "kind": "archive",
                    "root": str(archive_root),
                    "database": None,
                    "trees": [],
                    "depends_on": ["controller", "artifact"],
                },
            ],
        }
        profile_path = output / "recovery-profile.json"
        save(profile_path, profile)
        recovery_environment = environment | {"PYTHONPATH": str(ROOT / "apps/recovery-tool")}
        recovery_commands: list[dict[str, Any]] = []

        def recover(*arguments: str, passed: bool = True) -> dict[str, Any]:
            """Retain actual CLI exits and raw outputs without changing original history."""
            argv = [sys.executable, "-m", "roboguide_recovery", *arguments]
            completed = subprocess.run(
                argv, cwd=ROOT, env=recovery_environment, capture_output=True, timeout=90
            )
            label = f"recovery-{len(recovery_commands)}"
            (output / (label + ".stdout")).write_bytes(completed.stdout)
            (output / (label + ".stderr")).write_bytes(completed.stderr)
            recovery_commands.append({"argv": argv, "exit_code": completed.returncode})
            save(output / "recovery-commands.json", recovery_commands)
            if not passed:
                assert completed.returncode != 0
                return {"stderr": completed.stderr.decode(errors="replace")}
            assert completed.returncode == 0, completed.stderr.decode(errors="replace")
            return dict(json.loads(completed.stdout))

        backed = output / "bundle"
        backup = recover(
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(backed),
            "--command-id",
            "original-backup",
        )
        pin = backup["manifest_digest"]
        recover("verify", "--bundle", str(backed), "--manifest-digest", pin)
        restored = output / "restored"
        restore_args = (
            "restore",
            "--bundle",
            str(backed),
            "--manifest-digest",
            pin,
            "--destination",
            str(restored),
            "--command-id",
            "original-restore",
        )
        receipt = recover(*restore_args)
        assert receipt["schema"] == "roboguide.restore-receipt/v0.16"
        assert recover(*restore_args) == receipt
        old_profile = output / "old-profile.json"
        old_definition = json.loads(json.dumps(profile))
        old_definition["schema"] = "roboguide.recovery-profile/v0.15"
        for component in old_definition["components"]:
            component.pop("storage_schema", None)
        save(old_profile, old_definition)
        refused = recover(
            "backup",
            "--profile",
            str(old_profile),
            "--destination",
            str(output / "old-refused"),
            "--command-id",
            "old-profile",
            passed=False,
        )
        assert "recovery v0.16" in refused["stderr"] and not (output / "old-refused").exists()
        old_proof = None
        if old_controller is not None:
            old_sql = output / "old-controller.sqlite3"
            shutil.copy2(restored / "stores/controller/controller.sqlite3", old_sql)
            shutil.copytree(restored / "stores/artifact", output / "old-cas")
            old = subprocess.run(
                [
                    str(old_controller),
                    address(),
                    str(old_sql),
                    address(),
                    address(),
                    str(output / "old-cas"),
                ],
                cwd=ROOT,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("ROBOGUIDE_")
                }
                | {"ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3"},
                capture_output=True,
                timeout=15,
            )
            (output / "old-controller.stderr").write_bytes(old.stderr)
            assert old.returncode != 0 and any(
                value in old.stderr.decode(errors="replace")
                for value in (
                    "v51",
                    "v52",
                    "v30",
                    "unsupported",
                    "unknown variant `ArtifactCatalogLegacyAdopted`",
                )
            )
            old_proof = {
                "binary_digest": "sha256:"
                + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
                "exit_code": old.returncode,
            }
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == frozen_binary
        assert {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in pinned_paths
        } == frozen_source
        result = {
            "passed": True,
            "new_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "actual_legacy_software_nodes": evidence["software_nodes"],
            "original_publications_unchanged": True,
            "actual_foreign_tenant_discovery_and_adoption_refused": True,
            "actual_adoptions": len(receipts),
            "original_sql_rollback_without_reference": True,
            "exact_receipts_after_actual_restart": True,
            "actual_cold_backup_verify_restore_retry": True,
            "actual_complete_adoption_archive_and_restart": True,
            "old_profile_refused": True,
            "actual_old_binary": old_proof,
            "binary_digest": "sha256:" + frozen_binary,
            "original_source_result_digest": "sha256:"
            + hashlib.sha256((source / "result.json").read_bytes()).hexdigest(),
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()
        save(
            output / "owned-process-exits.json",
            {
                "all_owned_processes_reaped": all(value.poll() is not None for value in processes),
                "processes": [
                    {"pid": value.pid, "returncode": value.returncode} for value in processes
                ],
            },
        )


def main() -> None:
    """Require an actual completed original process result and a new owned upgrade directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.source.resolve(),
                args.output.absolute(),
                None if args.old_controller is None else args.old_controller.resolve(),
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
