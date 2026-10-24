"""Restore one completed committed-input repair bundle and rejoin actual peers.

The gate consumes a previously verified post-repair bundle.  It does not repeat an
Artifact Snapshot operation, create a Node or device, or infer physical continuity.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from check_complete_producer_gc_processes import cluster, rows
from check_replica_content_processes import digest, save
from check_replica_content_recovery_processes import assemble, command

ROOT = Path(__file__).resolve().parents[2]


def infrastructure_config(source: dict[str, Any], restored: dict[str, Any], output: Path) -> Path:
    """Bind a restored Controller projection to the existing network probe contract."""
    member = restored["replica"]["replica_id"]
    value = {
        "replica": source["replica"],
        "content_root": restored["content"]["root"],
        "parent_sql": str(Path(restored["storage_directory"]) / "replica.sqlite3"),
        "member_stores": source["member_stores"],
        "staging_gc": True,
        "restore_binding_digest": restored["content"].get("restore_binding_digest"),
        "initialization_parent": True,
        "initialization": restored["content"].get("initialization"),
    }
    path = output / f"infrastructure-{member}.json"
    save(path, value)
    return path


def repair_operation_present(root: Path, command_id: str) -> bool:
    """Require the exact completed repair operation in restored SQLite evidence."""
    with closing(sqlite3.connect(root.joinpath("content.sqlite3"))) as sql:
        return any(
            command_id in raw for (raw,) in sql.execute("SELECT record FROM content_operations")
        )


def run(
    source_configurations: Path,
    bundle: Path,
    manifest_digest: str,
    probe: Path,
    supervisor: Path,
    output: Path,
) -> dict[str, Any]:
    """Verify, restore and restart actual repaired peers while preserving old stream rows."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source: dict[int, dict[str, Any]] = {
        json.loads(path.read_text())["replica"]["replica_id"]: json.loads(path.read_text())
        for path in sorted(source_configurations.glob("infrastructure-*.json"))
    }
    if not source:
        raise ValueError("actual replay-repair cold gate requires declared infrastructure members")
    original_streams = {
        member: rows(Path(config["content_root"]), "content_streams")
        for member, config in source.items()
    }
    if not any(
        value["receipt"] is None
        for member_rows in original_streams.values()
        for value in member_rows.values()
    ):
        raise ValueError("actual cold source must retain an original Unknown stream")
    source_identity = {
        str(path): digest(path)
        for path in [
            Path(__file__),
            probe,
            supervisor,
            ROOT / "tools/quality/check_complete_producer_gc_processes.py",
            ROOT / "tools/quality/check_replica_content_recovery_processes.py",
        ]
    }
    save(output / "source-identity.json", source_identity)
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "scope": "post-repair bundle cold restore and actual infrastructure rejoin only",
        "source_stream_rows": original_streams,
        "manifest_digest": manifest_digest,
    }
    cold = output / "restored"
    try:
        command(
            output,
            "verify",
            ["verify", "--bundle", str(bundle), "--manifest-digest", manifest_digest],
        )
        command(
            output,
            "restore",
            [
                "restore",
                "--bundle",
                str(bundle),
                "--manifest-digest",
                manifest_digest,
                "--destination",
                str(cold),
                "--command-id",
                "actual-replay-repair-cold-restore",
            ],
        )
        with tempfile.TemporaryDirectory(prefix="rg-replay-repair-cold-ipc-") as ipc:
            restored = assemble(cold, Path(ipc), supervisor)
            paths = {
                member: infrastructure_config(source[member], config, output)
                for member, (_, config) in restored.items()
            }
            result["cold_rejoin"] = cluster(probe, paths, output, "actual-cold-rejoin", False)
            assert result["cold_rejoin"]["passed"]
            result["cold_restart"] = cluster(probe, paths, output, "actual-cold-restart", False)
            assert result["cold_restart"]["passed"]
            result["restored_stream_rows_unchanged"] = True
            repair_command = ""
            repair_members: list[int] = []
            for member, (path, _) in restored.items():
                config = json.loads(path.read_text())
                root = Path(config["content"]["root"])
                assert rows(root, "content_streams") == original_streams[member]
                with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
                    for (raw,) in sql.execute("SELECT record FROM content_operations"):
                        if "replay-repair:" in raw:
                            repair_command = (
                                repair_command or json.loads(raw)["intent"]["command_id"]
                            )
                            repair_members.append(member)
                            break
            assert repair_command and repair_members
            assert all(
                repair_operation_present(
                    Path(json.loads(restored[member][0].read_text())["content"]["root"]),
                    repair_command,
                )
                for member in repair_members
            )
            result["repair_command_id"] = repair_command
            result["repair_members"] = repair_members
        result["passed"] = True
    finally:
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require an explicit verified post-repair bundle and frozen actual binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-configurations", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--supervisor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.source_configurations.resolve(strict=True),
        args.bundle.resolve(strict=True),
        args.manifest_digest,
        args.probe.resolve(strict=True),
        args.supervisor.resolve(strict=True),
        args.output.resolve(),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
