"""Check original coordinated Archive bytes, mandatory journals and genuine prior-writer refusal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import digest, save
from check_replica_content_recovery_processes import ROOT, command
from check_state_verifier_processes import address


def rehash(counter: Path, manifest: dict[str, Any], name: str) -> str:
    """Rehash independent damaged SQL while preserving the frozen original bundle."""
    database = counter / name
    entry = next(value for value in manifest["files"] if value["path"] == name)
    entry["byte_size"] = database.stat().st_size
    entry["content_digest"] = "sha256:" + digest(database)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    raw = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\nimport json,sys\n"
            "from roboguide_recovery.bundle import Budget,_identity\n"
            "p=json.loads(Path(sys.argv[2]).read_bytes())['profile']\n"
            "print(json.dumps(_identity(Path(sys.argv[1]),Budget(p))))\n",
            str(database),
            str(counter / "manifest.json"),
        ],
        env=environment,
        timeout=30,
    )
    entry["sqlite"] = json.loads(raw)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (counter / "manifest.json").write_bytes(encoded)
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def refuse(output: Path, bundle: Path, checksum: str, label: str) -> list[dict[str, Any]]:
    """Require both verify and restore to reject before publishing any restored authority."""
    observations = []
    destination = output / (label + "-refused-destination")
    for action in ("verify", "restore"):
        arguments = [action, "--bundle", str(bundle), "--manifest-digest", checksum]
        if action == "restore":
            arguments += ["--destination", str(destination), "--command-id", label]
        observations.append(command(output, label + "-" + action, arguments, refused=True))
    assert not destination.exists()
    return observations


def archive_controller(bundle: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Select actual materialized Archive rows rather than assuming any follower is current."""
    choices: list[tuple[int, dict[str, Any]]] = []
    for component in manifest["profile"]["components"]:
        if component["kind"] != "controller":
            continue
        database = bundle / "stores" / component["component_id"] / component["database"]
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
            count = sql.execute("SELECT COUNT(*) FROM coordinated_archive_operations").fetchone()[0]
        choices.append((count, component))
    count, selected = max(choices, key=lambda pair: pair[0])
    assert count > 0
    return selected


def previous_writer(bundle: Path, output: Path, binary: Path) -> dict[str, Any]:
    """Start and reap the prior same-wrapper writer on a disposable original SQL copy."""
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    component = archive_controller(bundle, manifest)
    original = bundle / "stores" / component["component_id"] / component["database"]
    database = output / "prior-writer.sqlite3"
    shutil.copyfile(original, database)
    database.chmod(0o600)
    before = digest(database)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment.update(
        {
            "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "1",
            "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
        }
    )
    actual = subprocess.run(
        [
            str(binary),
            address(),
            str(database),
            address(),
            address(),
            str(output / "prior-writer-cas"),
        ],
        env=environment,
        capture_output=True,
        timeout=30,
    )
    observation: dict[str, Any] = {
        "binary": str(binary),
        "binary_sha256": digest(binary),
        "returncode": actual.returncode,
        "stdout": actual.stdout.decode(),
        "stderr": actual.stderr.decode(),
        "original_sql_unchanged": digest(database) == before,
    }
    save(output / "prior-writer-refusal.json", observation)
    assert actual.returncode != 0 and observation["original_sql_unchanged"]
    assert (
        "mandatory original Archive journal marker is missing or changed" in observation["stderr"]
    )
    return observation


def run(bundle: Path, checksum: str, output: Path, old_controller: Path | None) -> dict[str, Any]:
    """Preserve a verified fixed bundle while damaging only independent actual file copies."""
    assert checksum == "sha256:" + digest(bundle / "manifest.json")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["profile"]["schema"] == "roboguide.recovery-profile/v0.30"
    identity = {str(path): digest(path) for path in bundle.rglob("*") if path.is_file()}
    result: dict[str, Any] = {
        "passed": False,
        "manifest_digest": checksum,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    try:
        command(
            output,
            "original-verify",
            ["verify", "--bundle", str(bundle), "--manifest-digest", checksum],
        )
        controller = archive_controller(bundle, manifest)
        name = "stores/" + controller["component_id"] + "/" + controller["database"]
        with closing(sqlite3.connect((bundle / name).as_uri() + "?mode=ro", uri=True)) as sql:
            raw = sql.execute("SELECT record_json FROM coordinated_archive_operations").fetchall()
        records = [json.loads(row[0]) for row in raw]
        assert records
        with tempfile.TemporaryDirectory(prefix="rg-archive-negative-") as temporary:
            workspace = Path(temporary)
            missing = workspace / "missing-package"
            shutil.copytree(bundle, missing)
            key = next(
                row["intent"]["package"] for row in records if row["completed_ms"] is not None
            )
            package = next(
                candidate
                for root in manifest["profile"]["components"]
                if root["kind"] == "replica-content"
                for candidate in [
                    missing
                    / "stores"
                    / root["component_id"]
                    / "content-v1"
                    / key["source_owner"][15:]
                    / key["content_digest"][7:]
                ]
                if candidate.is_file()
            )
            assert package.is_file()
            package.unlink()
            result["missing_actual_package"] = refuse(
                output, missing, checksum, "missing-archive-package"
            )
            table = workspace / "missing-journal"
            shutil.copytree(bundle, table)
            damaged = table / name
            damaged.chmod(0o600)
            with closing(sqlite3.connect(damaged)) as sql:
                sql.execute("DROP TABLE coordinated_archive_operations")
                sql.commit()
            rehashed = rehash(table, json.loads((table / "manifest.json").read_bytes()), name)
            result["missing_mandatory_journal"] = refuse(
                output, table, rehashed, "missing-archive-journal"
            )
        result["genuine_previous_writer"] = (
            None if old_controller is None else previous_writer(bundle, output, old_controller)
        )
        assert {str(path): digest(path) for path in bundle.rglob("*") if path.is_file()} == identity
        result["passed"] = True
    finally:
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require the exact original complete bundle and optional genuine earlier executable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--old-controller", type=Path)
    args = parser.parse_args()
    run(
        args.bundle.resolve(strict=True),
        args.manifest_digest,
        args.output.resolve(),
        None if args.old_controller is None else args.old_controller.resolve(strict=True),
    )


if __name__ == "__main__":
    main()
