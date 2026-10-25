"""Use the actual recovery CLI on original stopped calendar owners and exact SQL/CAS bytes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from check_recovery_bundle_processes import logical_rows
from check_state_verifier_processes import ROOT, address, save
from recovery_profile_selection import restore_schema, standalone_schema


def recover(source: Path, output: Path, old_controller: Path | None) -> dict[str, Any]:
    """Preserve original topology, Node journal and bytes without starting services."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    profile: dict[str, Any] = {
        "schema": standalone_schema(source / "controller.sqlite3"),
        "profile_id": "original-software-calendar-stores",
        "revision": 1,
        "max_bytes": 1024 * 1024 * 1024,
        "max_files": 10000,
        "budget_seconds": 30,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "controller",
                "kind": "controller",
                "root": str(source),
                "database": "controller.sqlite3",
                "trees": [],
                "depends_on": ["cas"],
                "storage_schema": "roboguide.controller-storage/v1",
            },
            {
                "component_id": "cas",
                "kind": "artifact",
                "root": str(source / "cas"),
                "database": None,
                "trees": [],
                "depends_on": [],
                "storage_schema": "roboguide.artifact-storage/v3",
            },
            {
                "component_id": "node",
                "kind": "node",
                "root": str(source / "node-state"),
                "database": "execution-journal.sqlite3",
                "trees": [],
                "depends_on": ["controller"],
            },
        ],
    }
    save(output / "profile.json", profile)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")

    def command(*arguments: str) -> dict[str, Any]:
        """Retain actual CLI outcomes without retrying failed mutations."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            env=environment,
            cwd=ROOT,
            capture_output=True,
            timeout=35,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.decode(errors="replace"))
        result: dict[str, Any] = json.loads(completed.stdout)
        return result

    operation = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    backup = (
        "backup",
        "--profile",
        str(output / "profile.json"),
        "--destination",
        str(output / "bundle"),
        "--command-id",
        "calendar-backup-" + operation,
    )
    created = command(*backup)
    save(output / "backup-receipt.json", created)
    bundle = ("--bundle", str(output / "bundle"), "--manifest-digest", created["manifest_digest"])
    assert command("verify", *bundle)["verified"] is True
    restore = (
        "restore",
        *bundle,
        "--destination",
        str(output / "restored"),
        "--command-id",
        "calendar-restore-" + operation,
    )
    receipt = command(*restore)
    assert receipt["schema"] == restore_schema(profile["schema"])
    assert command(*backup) == created and command(*restore) == receipt
    save(output / "restore-receipt.json", receipt)
    roots = {item["component_id"]: Path(item["root"]) for item in receipt["components"]}
    for identity, database in [
        ("controller", "controller.sqlite3"),
        ("node", "execution-journal.sqlite3"),
    ]:
        assert logical_rows(output / "bundle/stores" / identity / database) == logical_rows(
            roots[identity] / database
        )
    with closing(sqlite3.connect(roots["controller"] / "controller.sqlite3")) as sql:
        schema, raw = sql.execute(
            "SELECT schema,checkpoint_json FROM controller_checkpoint"
        ).fetchone()
    authority = json.loads(json.loads(raw)["integration_json"])["control"]
    assert (
        schema in {"roboguide.controller-checkpoint/v58", "roboguide.controller-checkpoint/v59"}
        and authority["resource_topology_history"]
    )
    refusal = None
    if old_controller is not None:
        binary = old_controller.resolve(strict=True)
        assert subprocess.check_output(
            [str(binary), "--application-schema"], timeout=5
        ).decode().strip() in {
            "roboguide.controller-checkpoint/v57",
            "roboguide.controller-checkpoint/v58",
        }
        before = logical_rows(roots["controller"] / "controller.sqlite3")
        old_environment = environment | {
            "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "1",
            "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
        }
        refused = subprocess.run(
            [
                str(binary),
                address(),
                str(roots["controller"] / "controller.sqlite3"),
                address(),
                address(),
                str(roots["cas"]),
            ],
            env=old_environment,
            capture_output=True,
            timeout=10,
        )
        assert (
            refused.returncode != 0
            and f"unsupported checkpoint schema {schema}" in refused.stderr.decode()
        )
        assert logical_rows(roots["controller"] / "controller.sqlite3") == before
        refusal = {
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "native_wait": refused.returncode,
            "stderr": refused.stderr.decode(),
        }
    result = {
        "passed": True,
        "manifest_digest": created["manifest_digest"],
        "receipt_schema": receipt["schema"],
        "all_original_sql_preserved": True,
        "restored_services_started": 0,
        "old_controller_refusal": refusal,
        "external_compute_provider_in_bundle": False,
        "devices": 0,
    }
    save(output / "result.json", result)
    return result
