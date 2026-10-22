"""Verify source GC through actual TLS, SQL, filesystem faults and cold restore."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import call as transport
from check_artifact_authority_processes import grant
from check_catalog_adoption_processes import call
from check_identity_processes import client, request
from check_state_verifier_processes import address, save, stop, wait_for

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/recovery-tool"))
from roboguide_recovery.artifact_source_references import catalog_rows, upload_history  # noqa: E402
from roboguide_recovery.bundle import Budget  # noqa: E402
from roboguide_recovery.profile import acquire_all, validate  # noqa: E402
from roboguide_recovery.snapshot_references import canonical, digest  # noqa: E402


def run(source: Path, output: Path, old_controller: Path | None) -> dict[str, Any]:
    """Reclaim original adoption or MI bytes without mutating their historical source stores."""
    original_result = json.loads((source / "result.json").read_bytes())
    original_profile: dict[str, Any]
    revision_source = original_result.get("case") == "revision-mi"
    assert original_result["passed"] and (
        original_result.get("actual_adoptions", 0) > 0
        or (
            revision_source
            and original_result["catalog_references"]["actual_child_publications"] > 0
        )
    )
    exits = json.loads((source / "owned-process-exits.json").read_bytes())
    assert exits["all_owned_processes_reaped"] and all(
        type(row["returncode"]) is int for row in exits["processes"]
    )
    if revision_source:
        original_profile = {
            "schema": "roboguide.recovery-profile/v0.16",
            "profile_id": "actual-mi-source-retention",
            "revision": 1,
            "max_bytes": 512 * 1024**2,
            "max_files": 512,
            "budget_seconds": 60,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "controller",
                    "kind": "controller",
                    "root": str(source),
                    "database": "controller.sqlite3",
                    "trees": [],
                    "depends_on": ["artifact"],
                },
                {
                    "component_id": "artifact",
                    "kind": "artifact",
                    "storage_schema": "roboguide.artifact-storage/v3",
                    "root": str(source / "cas"),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                },
            ],
        }
    else:
        original_profile = json.loads((source / "recovery-profile.json").read_bytes())
    components = validate(original_profile)
    original_controller = next(value for value in components if value["kind"] == "controller")
    original_artifact = next(value for value in components if value["kind"] == "artifact")
    output.mkdir(parents=True, exist_ok=False)
    save(output / "original-source-profile.json", original_profile)
    working = output / "working"
    working.mkdir()
    pinned = [
        *sorted((ROOT / "core").rglob("*.rs")),
        *sorted((ROOT / "apps/integration-server/src").rglob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        ROOT / "tools/quality/check_artifact_authority_processes.py",
        ROOT / "tools/quality/check_catalog_adoption_processes.py",
        ROOT / "tools/quality/check_identity_processes.py",
        ROOT / "tools/quality/check_state_verifier_processes.py",
        Path(__file__),
        ROOT / "Cargo.lock",
    ]
    source_digests = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in pinned}
    save(output / "source-identity.json", source_digests)
    database = working / "controller.sqlite3"
    with acquire_all(components, profile_schema=original_profile["schema"]):
        shutil.copytree(Path(original_artifact["root"]), working / "cas")
        with (
            closing(
                sqlite3.connect(
                    (Path(original_controller["root"]) / original_controller["database"]).as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as retained,
            closing(sqlite3.connect(database)) as copied,
        ):
            retained.backup(copied)
    for name in ("pki",):
        shutil.copytree(Path(original_controller["root"]) / name, working / name)
    for name in ("artifact-authorization.json", "operator-authorization.json"):
        shutil.copy2(Path(original_controller["root"]) / name, working / name)
    if revision_source:
        authorization_path = working / "artifact-authorization.json"
        authorization = json.loads(authorization_path.read_bytes())
        actual_foreign_leaf = digest(
            ssl.PEM_cert_to_DER_cert((working / "pki/reenrolment.crt").read_text())
        )
        authorization["revision"] += 1
        authorization["grants"][actual_foreign_leaf] = grant(
            "retention-foreign-administrator", "admin", "retention-other-tenant"
        )
        save(authorization_path, authorization)
    profile = json.loads(json.dumps(original_profile))
    profile.update(schema="roboguide.recovery-profile/v0.16", profile_id="source-reclamation")
    for component in profile["components"]:
        if component["kind"] == "controller":
            component["root"] = str(working)
        elif component["kind"] == "artifact":
            component.update(
                root=str(working / "cas"), storage_schema="roboguide.artifact-storage/v5"
            )
    profile_path = output / "profile.json"
    save(profile_path, profile)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        checkpoint = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
        integration = json.loads(checkpoint["integration_json"])
        rows = catalog_rows(connection, Budget(profile))
    uploads, _ = upload_history(rows)
    heads = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if "ArtifactCatalogReferenceTransition" in payload:
            record = payload["ArtifactCatalogReferenceTransition"]["record"]
            heads[tuple(record["selector"].values())] = record
    if revision_source:
        child = next(
            value
            for value in heads.values()
            if any(item["kind"] == "lineage" for item in value["dependencies"])
        )
        parent = next(value for value in child["dependencies"] if value["kind"] == "lineage")
        reference = heads[tuple(parent["selector"].values())]
    else:
        commands = json.loads((source / "commands.json").read_bytes())
        reference = commands[0]["receipt"]["record"]
    direct = next(value for value in reference["dependencies"] if value["kind"] == "catalog")
    checksum, size = direct["content_digest"], direct["byte_size"]
    references = sorted(
        (
            value
            for value in heads.values()
            if value["retirement"] is None
            and any(item["content_digest"] == checksum for item in value["dependencies"])
        ),
        key=lambda value: len(value["dependencies"]),
    )
    assert references
    content_path = working / "cas/objects-v1/sha256" / checksum[7:9] / checksum[7:]
    original_bytes = content_path.read_bytes()
    assert digest(original_bytes) == checksum and len(original_bytes) == size
    selected_uploads = [
        value
        for value in uploads.values()
        if value["content_digest"] == checksum and value["phase"] == "completed"
    ]
    assert selected_uploads
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment.update(
        ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA="roboguide.artifact-storage/v5",
        ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY=str(working / "released-content-archives"),
        ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS=json.dumps(
            integration["control"]["node_identity_registry"]
        ),
        TOKIO_WORKER_THREADS="1",
    )
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
    admin, viewer, foreign = (
        client(working / "pki", name)
        for name in ("admin", "viewer", "reenrolment" if revision_source else "admin-next")
    )
    grpc, operator, artifact = (address() for _ in range(3))
    operator_origin, artifact_origin = "https://" + operator, "https://" + artifact
    binary = ROOT / "target/debug/integration-server"
    binary_digest = digest(binary.read_bytes())
    read_probe = ROOT / "target/debug/examples/reclamation_probe"
    probe_digest = digest(read_probe.read_bytes())
    processes: list[subprocess.Popen[bytes]] = []
    logs = []
    cli_commands: list[dict[str, Any]] = []
    recovery_environment = environment | {"PYTHONPATH": str(ROOT / "apps/recovery-tool")}

    def start() -> subprocess.Popen[bytes]:
        """Retain each actual Controller incarnation and independently wait before replacement."""
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
            "actual source GC Controller readiness",
            60,
        )
        return process

    def recover(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Keep actual CLI outputs/exits; a failure remains a failure beside its retained intent."""
        argv = [sys.executable, "-m", "roboguide_recovery", *arguments]
        result = subprocess.run(
            argv, cwd=ROOT, env=recovery_environment, capture_output=True, timeout=90
        )
        index = len(cli_commands)
        (output / f"cli-{index}.stdout").write_bytes(result.stdout)
        (output / f"cli-{index}.stderr").write_bytes(result.stderr)
        cli_commands.append({"argv": argv, "exit_code": result.returncode})
        save(output / "cli-commands.json", cli_commands)
        if not passed:
            assert result.returncode != 0
            return {"stderr": result.stderr.decode(errors="replace")}
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return dict(json.loads(result.stdout))

    def gc(command: str, *, passed: bool = True) -> dict[str, Any]:
        """Issue one explicit actual source GC command without changing its digest or reason."""
        return recover(
            "gc-artifact",
            "--profile",
            str(profile_path),
            "--artifact",
            original_artifact["component_id"],
            "--content-digest",
            checksum,
            "--byte-size",
            str(size),
            "--command-id",
            command,
            "--reason",
            "explicit-original-retention-release",
            passed=passed,
        )

    try:
        process = start()
        own_upload_id = "actual-current-owner-original-upload"
        assert (
            call(
                artifact_origin, admin, "POST", "/v1/artifact-uploads", {"upload_id": own_upload_id}
            )[0]
            == 201
        )
        assert (
            transport(
                artifact_origin,
                admin,
                "POST",
                "/v1/artifact-uploads/" + own_upload_id + "/content",
                original_bytes,
            )[0]
            == 202
        )
        status, raw = call(
            artifact_origin,
            admin,
            "POST",
            "/v1/artifact-uploads/" + own_upload_id + "/finalize",
            {"content_digest": checksum, "byte_size": size},
        )
        assert status == 201, raw
        own_upload = json.loads(raw)["receipt"]
        selected_uploads.append(own_upload)
        assert "writer" in gc("active-writer", passed=False)["stderr"]
        stop(process)
        snapshot_retirements = []
        pending = [
            path
            for path in (working / "cas/maintenance-v1/operations").iterdir()
            if not (path / "receipt.json").exists()
        ]
        if pending:
            assert "Snapshot" in gc("active-original-snapshot", passed=False)["stderr"]
        for path in pending:
            snapshot = json.loads((path / "intent.json").read_bytes())
            original_profile_path = source / "old-profile.json"
            snapshot_arguments = (
                "retire-artifact-snapshot",
                "--profile",
                str(profile_path),
                "--original-profile",
                str(original_profile_path),
                "--artifact",
                original_artifact["component_id"],
                "--operation-id",
                snapshot["operation_id"],
                "--original-intent-digest",
                digest(canonical(snapshot)),
                "--command-id",
                "retire-upgraded-original-snapshot",
                "--reason",
                "drained-original-unpublished-copy",
            )
            descriptor = os.open(
                Path(original_artifact["root"]) / ".writer.lock", os.O_RDWR | os.O_NOFOLLOW
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert "writer" in recover(*snapshot_arguments, passed=False)["stderr"]
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            retired_snapshot = recover(*snapshot_arguments)
            assert not retired_snapshot["replayed"] and retired_snapshot["copied_files"] == 0
            assert recover(*snapshot_arguments)["receipt"] == retired_snapshot["receipt"]
            assert json.loads((path / "intent.json").read_bytes()) == snapshot
            snapshot_retirements.append(retired_snapshot["receipt"])
        assert "Catalog/lineage" in gc("active-catalog", passed=False)["stderr"]
        process = start()
        retired = []
        for index, original in enumerate(selected_uploads):
            path = "/v1/artifact-uploads/" + original["upload_id"]
            command = {
                "schema": "roboguide.artifact-upload-retirement-command/v0.1",
                "command_id": "retire-original-upload-" + str(index),
                "original_upload_digest": digest(canonical(original)),
                "reason": "release-original-file-upload",
            }
            for context in (viewer, foreign):
                assert call(artifact_origin, context, "POST", path + "/retire", command)[0] == 403
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TRIGGER upload_retire_fault BEFORE INSERT ON events "
                    "WHEN NEW.payload_schema='domain.EventPayload.json/v31' "
                    "BEGIN SELECT RAISE(ABORT,'actual upload-retirement SQL fault'); END"
                )
            assert call(artifact_origin, admin, "POST", path + "/retire", command)[0] >= 500
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE payload_schema='domain.EventPayload.json/v31'"
                ).fetchone() == (index,)
                connection.execute("DROP TRIGGER upload_retire_fault")
            stop(process)
            process = start()
            status, raw = call(artifact_origin, admin, "POST", path + "/retire", command)
            assert status == 200, raw
            receipt = json.loads(raw)["receipt"]
            assert receipt["upload"] == original and content_path.read_bytes() == original_bytes
            replay = json.loads(call(artifact_origin, admin, "POST", path + "/retire", command)[1])
            assert replay["replayed"] and replay["receipt"] == receipt
            if original["upload_id"] == own_upload_id:
                assert (
                    call(
                        artifact_origin,
                        admin,
                        "POST",
                        path + "/finalize",
                        {"content_digest": checksum, "byte_size": size},
                    )[0]
                    == 409
                )
            retired.append(receipt)
        stop(process)
        assert "Catalog/lineage" in gc("upload-retired-catalog-active", passed=False)["stderr"]
        process = start()
        catalog_retirements = []
        revision_revocation = None
        for index, reference in enumerate(references):
            reference_path = (
                "/v1/artifact-references/"
                + reference["selector"]["memory_id"]
                + "/"
                + reference["selector"]["revision_id"]
                + "/retire"
            )
            command = {
                "command_id": "retire-original-file-catalog-" + str(index),
                "original_digest": digest(canonical(reference)),
            }
            if revision_source and index == 0:
                status, raw = call(artifact_origin, admin, "POST", reference_path, command)
                assert status == 409 and b"runtime revision" in raw, raw
                (output / "active-revision-retains-catalog.json").write_bytes(raw)
                mission = next(iter(checkpoint["runtime_revisions"]))
                revision_path = "/v1/missions/" + mission + "/plan-revisions"
                status, current = request(operator_origin, admin, revision_path)
                assert status == 200, current
                revoke = {
                    "schema_version": "roboguide.runtime-plan-revision/v0.3",
                    "command_id": "explicit-original-completed-mi-policy-revoke",
                    "expected_revision": current["authority"]["revision"],
                    "action": {"kind": "revoke-policy"},
                }
                status, revision_revocation = request(operator_origin, admin, revision_path, revoke)
                assert status == 200, revision_revocation
                assert (
                    request(operator_origin, admin, revision_path, revoke)[1] == revision_revocation
                )
                save(output / "original-mi-policy-revocation.json", revision_revocation)
            status, raw = call(artifact_origin, admin, "POST", reference_path, command)
            assert status == 200, raw
            catalog_retirements.append(json.loads(raw))
            if index < len(references) - 1:
                stop(process)
                assert (
                    "Catalog/lineage" in gc("parent-retired-child-active", passed=False)["stderr"]
                )
                process = start()
        stop(process)
        # Actual same-UID directory permissions allow reading/hash and SQL intent but deny unlink.
        original_mode = content_path.parent.stat().st_mode & 0o777
        content_path.parent.chmod(0o555)
        try:
            refused = gc("original-unlink-unknown", passed=False)
            assert "PermissionError" in refused["stderr"] and content_path.is_file()
        finally:
            content_path.parent.chmod(original_mode)
        unknown = gc("original-unlink-unknown")
        assert (
            unknown["phase"] == "unknown"
            and unknown["effects_performed"] == 0
            and content_path.is_file()
        )
        first_intent = unknown["intent"]
        observed_read = subprocess.run(
            [str(read_probe), str(working / "cas"), checksum, str(size)],
            cwd=ROOT,
            capture_output=True,
            timeout=15,
        )
        (output / "original-tombstone-read.stdout").write_bytes(observed_read.stdout)
        (output / "original-tombstone-read.stderr").write_bytes(observed_read.stderr)
        assert (
            observed_read.returncode == 0 and json.loads(observed_read.stdout)["readable"] is False
        )
        assert content_path.is_file()
        process = start()
        assert transport(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum)[0] == 403
        stop(process)
        completed = gc("explicit-original-remainder")
        assert completed["receipt"]["result"] == "deleted" and not content_path.exists()
        unchanged = gc("original-unlink-unknown")
        assert unchanged["intent"] == first_intent and unchanged["phase"] == "unknown"
        content_path.write_bytes(original_bytes)
        assert (
            gc("explicit-original-remainder")["effects_performed"] == 0 and content_path.is_file()
        )
        process = start()
        assert transport(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum)[0] == 403
        upload_id = "new-upload-cannot-revive-original-content"
        assert (
            call(artifact_origin, admin, "POST", "/v1/artifact-uploads", {"upload_id": upload_id})[
                0
            ]
            == 201
        )
        assert (
            transport(
                artifact_origin,
                admin,
                "POST",
                "/v1/artifact-uploads/" + upload_id + "/content",
                original_bytes,
            )[0]
            == 202
        )
        assert (
            call(
                artifact_origin,
                admin,
                "POST",
                "/v1/artifact-uploads/" + upload_id + "/finalize",
                {"content_digest": checksum, "byte_size": size},
            )[0]
            == 404
        )
        assert call(artifact_origin, admin, "DELETE", "/v1/artifact-uploads/" + upload_id)[0] == 200
        stop(process)
        assert (
            gc("explicit-reintroduced-remainder")["effects_performed"] == 1
            and not content_path.exists()
        )
        process = start()
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            cutoff = connection.execute("SELECT MAX(sequence) FROM events").fetchone()[0]
        archive_command = {
            "schema": "roboguide.archive-command/v0.2",
            "command_id": "archive-after-original-source-reclamation",
            "end_sequence": cutoff,
            "budget_ms": 60_000,
        }
        status, archived = request(
            operator_origin, admin, "/v1/maintenance/archive", archive_command, timeout_seconds=45
        )
        assert status == 200, archived
        assert (
            request(operator_origin, admin, "/v1/maintenance/archive", archive_command)[1]
            == archived
        )
        save(output / "actual-released-history-archive.json", archived)
        stop(process)
        assert not content_path.exists()
        profile["components"].append(
            {
                "component_id": "released-history-archive",
                "kind": "archive",
                "root": str(working / "released-content-archives"),
                "database": None,
                "trees": [],
                "depends_on": [
                    original_controller["component_id"],
                    original_artifact["component_id"],
                ],
            }
        )
        save(profile_path, profile)
        backup = recover(
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "gc-cold-backup",
        )
        pin = backup["manifest_digest"]
        recover("verify", "--bundle", str(output / "bundle"), "--manifest-digest", pin)
        arguments = (
            "restore",
            "--bundle",
            str(output / "bundle"),
            "--manifest-digest",
            pin,
            "--destination",
            str(output / "restored"),
            "--command-id",
            "gc-cold-restore",
        )
        receipt = recover(*arguments)
        assert (
            receipt["schema"] == "roboguide.restore-receipt/v0.16"
            and recover(*arguments) == receipt
        )
        old_profile = json.loads(json.dumps(profile))
        old_profile["schema"] = "roboguide.recovery-profile/v0.15"
        for component in old_profile["components"]:
            component.pop("storage_schema", None)
        old_profile_path = output / "old-profile.json"
        save(old_profile_path, old_profile)
        refused_profile = recover(
            "backup",
            "--profile",
            str(old_profile_path),
            "--destination",
            str(output / "old-refused"),
            "--command-id",
            "old-profile",
            passed=False,
        )
        assert (
            "owner metadata" in refused_profile["stderr"] and not (output / "old-refused").exists()
        )
        tampered = output / "tampered-bundle"
        shutil.copytree(output / "bundle", tampered)
        manifest = json.loads((tampered / "manifest.json").read_bytes())
        gc_record = next(
            value
            for value in manifest["files"]
            if value["path"].endswith("reclamation-v1/journal.sqlite3")
        )
        tampered_sql = tampered / gc_record["path"]
        with sqlite3.connect(tampered_sql) as connection:
            connection.execute("DELETE FROM gc_tombstones")
        raw = tampered_sql.read_bytes()
        gc_record.update(content_digest=digest(raw), byte_size=len(raw))
        changed_manifest = canonical(manifest)
        (tampered / "manifest.json").write_bytes(changed_manifest)
        refused_loss = recover(
            "verify",
            "--bundle",
            str(tampered),
            "--manifest-digest",
            digest(changed_manifest),
            passed=False,
        )
        assert "mandatory command/tombstone row" in refused_loss["stderr"]
        old_proof = None
        if old_controller is not None:
            old = subprocess.run(
                [
                    str(old_controller),
                    address(),
                    str(output / "old-controller.sqlite3"),
                    address(),
                    address(),
                    str(working / "cas"),
                ],
                cwd=ROOT,
                env=environment
                | {"ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3"},
                capture_output=True,
                timeout=15,
            )
            (output / "old-controller.stderr").write_bytes(old.stderr)
            assert (
                old.returncode != 0
                and "original maintenance journal requires explicit Artifact storage v2"
                in old.stderr.decode(errors="replace")
            )
            old_sql = output / "old-application.sqlite3"
            shutil.copy2(output / "restored/stores/controller/controller.sqlite3", old_sql)
            old_application = subprocess.run(
                [
                    str(old_controller),
                    address(),
                    str(old_sql),
                    address(),
                    address(),
                    str(output / "old-application-cas"),
                ],
                cwd=ROOT,
                env=environment
                | {"ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3"},
                capture_output=True,
                timeout=15,
            )
            (output / "old-application.stderr").write_bytes(old_application.stderr)
            assert old_application.returncode != 0 and any(
                value in old_application.stderr.decode(errors="replace")
                for value in ("v52", "v31", "unknown variant `ArtifactUploadRetired`")
            )
            old_proof = {
                "binary_digest": digest(old_controller.read_bytes()),
                "exit_code": old.returncode,
                "application_exit_code": old_application.returncode,
            }
        assert {
            str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in pinned
        } == source_digests
        assert digest(binary.read_bytes()) == binary_digest
        assert digest(read_probe.read_bytes()) == probe_digest
        result = {
            "passed": True,
            "new_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "binary_digest": binary_digest,
            "read_probe_digest": probe_digest,
            "actual_old_binary": old_proof,
            "actual_original_upload_retirements": retired,
            "actual_original_catalog_retirements": catalog_retirements,
            "actual_mi_policy_revocation": revision_revocation,
            "actual_parent_release_kept_child_pin": revision_source,
            "actual_upgraded_snapshot_retirements": snapshot_retirements,
            "catalog_remained_independent": True,
            "actual_sql_rollback_and_restart": True,
            "actual_unlink_permission_failure": True,
            "original_unknown_retained": True,
            "old_id_never_repeated_unlink": True,
            "actual_tombstone_blocks_reads_and_new_upload": True,
            "actual_content_unlinked": True,
            "actual_released_history_archive_without_deleted_bytes": True,
            "actual_cold_backup_verify_restore_retry": True,
            "old_profile_refused": True,
            "repinned_missing_tombstone_refused": True,
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
    """Require original completed software evidence and a new destination; never invent Nodes."""
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
