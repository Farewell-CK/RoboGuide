"""Cold-recover original archived Catalog evidence from one complete, reaped actual file profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from check_identity_processes import ROOT
from check_state_verifier_processes import save


def run(source: Path, directory: Path) -> dict[str, Any]:
    """Use actual retained SQL/files/CLI; no new Node, local engine, model or device is started."""
    source = source.resolve()
    evidence = json.loads((source / "result.json").read_bytes())
    exits = json.loads((source / "owned-process-exits.json").read_bytes())
    if (
        not evidence.get("passed")
        or not evidence.get("content_archive")
        or not exits.get("all_owned_processes_reaped")
    ):
        raise ValueError(
            "original actual file/Archive profile must complete and reap every process"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    profile: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.12",
        "profile_id": "actual-catalog-archive-recovery",
        "revision": 1,
        "max_bytes": 512 * 1024**2,
        "max_files": 1024,
        "budget_seconds": 60,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "controller",
                "kind": "controller",
                "root": str(source),
                "database": "controller.sqlite3",
                "trees": [],
                "depends_on": [],
            },
            {
                "component_id": "artifact",
                "kind": "artifact",
                "root": str(source / "cas"),
                "database": None,
                "trees": [],
                "depends_on": ["controller"],
            },
            {
                "component_id": "archive",
                "kind": "archive",
                "root": str(source / "content-archives"),
                "database": None,
                "trees": [],
                "depends_on": ["controller", "artifact"],
            },
        ],
    }
    selected = directory / "profile.json"
    save(selected, profile)
    commands: list[dict[str, Any]] = []
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    frozen = {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
            Path(__file__),
        ]
    }
    save(directory / "source-identity.json", frozen)

    def cli(*arguments: str) -> dict[str, Any]:
        """Retain exact production CLI arguments, raw output and actual child wait results."""
        command = [sys.executable, "-m", "roboguide_recovery", *arguments]
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, timeout=90
        )
        label = f"command-{len(commands)}"
        (directory / (label + ".stdout")).write_bytes(completed.stdout)
        (directory / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": command, "exit_code": completed.returncode})
        save(directory / "commands.json", commands)
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        result = json.loads(completed.stdout)
        assert isinstance(result, dict)
        return result

    def retained(database: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        """Read original Catalog envelopes and Archive commands without migration."""
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute(
                "SELECT sequence,data FROM catalog_archive_events ORDER BY sequence"
            ).fetchall(), connection.execute(
                "SELECT command_id,intent_json,state,receipt_json FROM event_archive_operations "
                "ORDER BY command_id"
            ).fetchall()

    original = retained(source / "controller.sqlite3")
    assert original[0] and original[1]
    catalog_records = []
    for _, raw in original[0]:
        payload = json.loads(json.loads(raw)["payload_json"])
        if "ArtifactCatalogReferenceTransition" in payload:
            catalog_records.append(payload["ArtifactCatalogReferenceTransition"]["record"])
    assert len(catalog_records) >= 2 and any(
        item["retirement"] is not None for item in catalog_records
    )
    bundle = directory / "bundle"
    result = cli(
        "backup",
        "--profile",
        str(selected),
        "--destination",
        str(bundle),
        "--command-id",
        "original-catalog-cold-copy",
    )
    manifest_digest = result["manifest_digest"]
    cli("verify", "--bundle", str(bundle), "--manifest-digest", manifest_digest)
    restored = directory / "restored"
    receipt = cli(
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        manifest_digest,
        "--destination",
        str(restored),
        "--command-id",
        "restore-original-catalog",
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.12"
    assert retained(restored / "stores/controller/controller.sqlite3") == original
    assert retained(source / "controller.sqlite3") == original
    assert (
        cli(
            "restore",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            manifest_digest,
            "--destination",
            str(restored),
            "--command-id",
            "restore-original-catalog",
        )
        == receipt
    )
    independent_reads = []
    for _, raw, state, receipt_digest in original[1]:
        assert state == "compacted" and isinstance(receipt_digest, str)
        intent = json.loads(raw)
        request = intent["request"]
        prefix = (
            restored
            / "stores/archive/archives-v1"
            / hashlib.sha256(request["authority_id"].encode()).hexdigest()
            / ("archive-" + hashlib.sha256(request["command_id"].encode()).hexdigest())
        )
        publication = json.loads((prefix / "publication.json").read_bytes())
        assert publication["intent"] == intent
        for item in intent["inputs"]:
            checksum = item["content_digest"][7:]
            path = prefix / "content/objects-v1/sha256" / checksum[:2] / checksum
            actual = path.read_bytes()
            assert (
                len(actual) == item["byte_size"]
                and "sha256:" + hashlib.sha256(actual).hexdigest() == item["content_digest"]
            )
            independent_reads.append({"target_owner": publication["target_owner"], **item})
    assert {
        str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
            Path(__file__),
        ]
    } == frozen
    output = {
        "passed": True,
        "actual_source_result_digest": "sha256:"
        + hashlib.sha256((source / "result.json").read_bytes()).hexdigest(),
        "actual_original_catalog_events": len(catalog_records),
        "actual_original_archive_operations": len(original[1]),
        "actual_cli_backup_verify_restore_retry": True,
        "original_authority_history_unchanged": True,
        "original_kernel_budgets_unchanged": True,
        "independent_receiving_content_reads": independent_reads,
        "new_software_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "manifest_digest": manifest_digest,
    }
    save(directory / "result.json", output)
    return output


def main() -> None:
    """Require a complete reaped actual profile and a fresh owned result root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output.absolute()), sort_keys=True))


if __name__ == "__main__":
    main()
