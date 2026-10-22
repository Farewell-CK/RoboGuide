"""Verify actual published Catalog/lineage recovery using one quiescent owned process result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from check_identity_processes import ROOT
from check_state_verifier_processes import address, save


def run(source: Path, directory: Path, old_controller: Path | None = None) -> dict[str, Any]:
    """Use original actual SQL/CAS and CLI processes; create no Node, provider or device process."""
    source = source.resolve()
    evidence = json.loads((source / "result.json").read_bytes())
    exits = json.loads((source / "owned-process-exits.json").read_bytes())
    if not evidence.get("passed") or not exits.get("all_owned_processes_reaped"):
        raise ValueError("original real process profile must be complete and actually reaped")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory = directory.resolve()
    working = directory / "working-source"
    working.mkdir()
    shutil.copy2(source / "controller.sqlite3", working / "controller.sqlite3")
    shutil.copy2(
        source / "controller.sqlite3.writer.lock", working / "controller.sqlite3.writer.lock"
    )
    for suffix in ("-wal", "-shm"):
        original = source / ("controller.sqlite3" + suffix)
        if original.exists():
            shutil.copy2(original, working / original.name)
    excluded: list[str] = []

    def omit_sockets(parent: str, names: list[str]) -> list[str]:
        """Exclude only process endpoints, retaining every original data file and evidence field."""
        selected = []
        for name in names:
            path = Path(parent) / name
            if stat.S_ISSOCK(path.lstat().st_mode):
                selected.append(name)
                excluded.append(str(path.relative_to(source)))
        return selected

    shutil.copytree(source / "cas", working / "cas", ignore=omit_sockets)
    profile: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.16",
        "profile_id": "actual-catalog-original-owner",
        "revision": 1,
        "max_bytes": 512 * 1024**2,
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
                "depends_on": ["artifact"],
            },
            {
                "component_id": "artifact",
                "kind": "artifact",
                "storage_schema": "roboguide.artifact-storage/v3",
                "root": str(working / "cas"),
                "database": None,
                "trees": [],
                "depends_on": [],
            },
        ],
    }
    profile_path = directory / "profile.json"
    save(profile_path, profile)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def execute(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Retain actual CLI argv, native exits and raw outputs before interpreting any result."""
        argv = [sys.executable, "-m", "roboguide_recovery", *arguments]
        completed = subprocess.run(argv, cwd=ROOT, env=environment, capture_output=True, timeout=90)
        label = "command-" + str(len(commands))
        (directory / (label + ".stdout")).write_bytes(completed.stdout)
        (directory / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": argv, "exit_code": completed.returncode})
        save(directory / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {
                "exit_code": completed.returncode,
                "stderr": completed.stderr.decode(errors="replace"),
            }
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        value = json.loads(completed.stdout)
        assert isinstance(value, dict)
        return value

    def original_heads(database: Path) -> dict[tuple[str, str], dict[str, Any]]:
        """Independently read original acquisition/retirement heads through readonly SQL."""
        heads = {}
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            for (raw,) in connection.execute(
                "SELECT payload_json FROM events "
                "WHERE payload_schema='domain.EventPayload.json/v29' ORDER BY sequence"
            ):
                record = json.loads(raw)["ArtifactCatalogReferenceTransition"]["record"]
                heads[(record["selector"]["memory_id"], record["selector"]["revision_id"])] = record
        return heads

    heads = original_heads(working / "controller.sqlite3")
    active = [record for record in heads.values() if record["retirement"] is None]
    if not active or not any(
        item["kind"] == "lineage" for record in active for item in record["dependencies"]
    ):
        raise ValueError("actual source must retain a real committed child lineage")
    bundle = directory / "bundle"
    receipt = execute(
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(bundle),
        "--command-id",
        "original-catalog",
    )
    pin = receipt["manifest_digest"]
    execute("verify", "--bundle", str(bundle), "--manifest-digest", pin)
    restored = directory / "restored"
    restore_arguments = (
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        pin,
        "--destination",
        str(restored),
        "--command-id",
        "original-restore",
    )
    restored_receipt = execute(*restore_arguments)
    assert restored_receipt["schema"] == "roboguide.restore-receipt/v0.16"
    assert execute(*restore_arguments) == restored_receipt
    assert original_heads(restored / "stores/controller/controller.sqlite3") == heads
    old_writer = None
    if old_controller is not None:
        old_controller = old_controller.resolve(strict=True)
        old_working = directory / "old-controller"
        old_working.mkdir()
        old_database = old_working / "controller.sqlite3"
        shutil.copy2(restored / "stores/controller/controller.sqlite3", old_database)
        before = original_heads(old_database)
        old_schema = subprocess.check_output(
            [str(old_controller), "--application-schema"], cwd=ROOT, text=True, timeout=15
        ).strip()
        retained_old_owner = old_schema in {
            "roboguide.controller-checkpoint/v49",
            "roboguide.controller-checkpoint/v50",
            "roboguide.controller-checkpoint/v51",
        }
        old_cas = old_working / ("retained-cas" if retained_old_owner else "legacy-cas")
        old_environment = dict(environment)
        if retained_old_owner:
            shutil.copytree(restored / "stores/artifact", old_cas)
            old_environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = "roboguide.artifact-storage/v3"
        argv = [
            str(old_controller),
            address(),
            str(old_database),
            address(),
            address(),
            str(old_cas),
        ]
        try:
            completed = subprocess.run(
                argv, cwd=ROOT, env=old_environment, capture_output=True, timeout=15
            )
        except subprocess.TimeoutExpired as error:
            raise AssertionError(
                "actual old Controller did not refuse the original current authority"
            ) from error
        (old_working / "stdout").write_bytes(completed.stdout)
        (old_working / "stderr").write_bytes(completed.stderr)
        assert completed.returncode != 0
        message = completed.stderr.decode(errors="replace")
        assert "unsupported" in message.lower() and any(
            version in message for version in ("v29", "v48", "v49", "v50", "v51", "v52")
        ), message
        assert original_heads(old_database) == before
        old_writer = {
            "argv": argv,
            "exit_code": completed.returncode,
            "binary_digest": "sha256:" + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
            "original_reference_authority_unchanged": True,
            "retained_layout_was_not_the_rejection_cause": True,
        }
        save(old_working / "result.json", old_writer)
    original_owner = json.loads((working / "cas/.retention-owner.json").read_bytes())
    assert (
        json.loads((restored / "stores/artifact/.retention-owner.json").read_bytes())
        == original_owner
    )
    independent_bytes = []
    for record in active:
        for dependency in record["dependencies"]:
            assert dependency["store_owner_id"] == original_owner["owner_id"]
            checksum = dependency["content_digest"][7:]
            path = restored / "stores/artifact/objects-v1/sha256" / checksum[:2] / checksum
            assert (
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                == dependency["content_digest"]
            )
            assert path.stat().st_size == dependency["byte_size"]
            independent_bytes.append(
                {
                    "owner": dependency["store_owner_id"],
                    "digest": dependency["content_digest"],
                    "byte_size": dependency["byte_size"],
                    "kind": dependency["kind"],
                }
            )
    old_profile = directory / "old-profile.json"
    old_definition = json.loads(json.dumps(profile))
    old_definition["schema"] = "roboguide.recovery-profile/v0.9"
    for component in old_definition["components"]:
        component.pop("storage_schema", None)
    save(old_profile, old_definition)
    refused_profile = execute(
        "backup",
        "--profile",
        str(old_profile),
        "--destination",
        str(directory / "old-profile-refused"),
        "--command-id",
        "old-profile",
        passed=False,
    )
    assert any(
        message in refused_profile["stderr"]
        for message in ("recovery v0.10", "owner metadata is invalid")
    )
    assert not (directory / "old-profile-refused").exists()
    selected = next(
        item for record in active for item in record["dependencies"] if item["kind"] == "lineage"
    )
    checksum = selected["content_digest"][7:]
    original_path = working / "cas/objects-v1/sha256" / checksum[:2] / checksum
    original_path.unlink()
    missing = execute(
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(directory / "missing-content-refused"),
        "--command-id",
        "missing-content",
        passed=False,
    )
    assert "exact bundled CAS bytes" in missing["stderr"]
    assert not (directory / "missing-content-refused").exists()
    assert original_heads(working / "controller.sqlite3") == heads
    result = {
        "passed": True,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "new_node_processes": 0,
        "source_software_nodes": evidence["software_nodes"],
        "actual_original_catalog_records": len(heads),
        "actual_active_records": len(active),
        "actual_independent_content_reads": independent_bytes,
        "original_reference_authority_unchanged": True,
        "actual_cli_backup_verify_restore_retry": True,
        "actual_old_controller": old_writer,
        "missing_original_content_refused_before_publication": True,
        "excluded_runtime_sockets": excluded,
        "manifest_digest": pin,
        "original_source_result_digest": "sha256:"
        + hashlib.sha256((source / "result.json").read_bytes()).hexdigest(),
    }
    save(directory / "result.json", result)
    return result


def main() -> None:
    """Require complete software-process evidence and a new owned output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.old_controller), sort_keys=True))


if __name__ == "__main__":
    main()
