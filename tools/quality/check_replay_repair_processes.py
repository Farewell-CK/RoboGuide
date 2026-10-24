"""Repair actual interrupted committed replay inputs without rewriting original stream Unknown.

Only declared existing infrastructure roots and verified managed Artifact sources are used.
No embodied Nodes, devices, models or simulators are started.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from check_complete_producer_gc_processes import cluster, retain_forensic, rows
from check_replica_content_processes import Processes, digest, save
from check_replica_content_recovery_processes import assemble, command, profile

ROOT = Path(__file__).resolve().parents[2]


def request(config: dict[str, Any], operation: str, **fields: Any) -> dict[str, Any]:
    """Bind one closed local operation to its actual original Root/backing and existing parent."""
    root = Path(config["content"]["root"])
    identity = json.loads((root / ".content-creation.json").read_text())
    value = {
        "operation": operation,
        "member": {
            "content_root": str(root),
            "profile_identity": identity["profile_identity"],
            "member": identity["member"],
            "content_layout": "roboguide.replica-content-layout/v0.8",
        },
        "restore_binding_digest": config["content"].get("restore_binding_digest"),
        **fields,
    }
    if operation in {"inspect-replay-content", "repair-replay-content"}:
        value["parent"] = {
            "schema": "roboguide.replica-storage/v0.9",
            "path": str(Path(config["storage_directory"]) / "replica.sqlite3"),
            "initialization": config["content"].get("initialization"),
        }
    return value


def parent_rows(config: dict[str, Any]) -> dict[str, Any]:
    """Freeze exact original Log and apply/commit pointers independently of command responses."""
    path = Path(config["storage_directory"]) / "replica.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        return {
            "log": dict(sql.execute("SELECT log_index,data FROM replica_v1_log")),
            "pointers": dict(
                sql.execute(
                    "SELECT key,value FROM replica_v1_meta WHERE key IN ('applied','committed')"
                )
            ),
        }


def run(
    configurations: Path, bundle: Path, checksum: str, probe: Path, supervisor: Path, output: Path
) -> dict[str, Any]:
    """Retain real interrupted inputs, repair independently and rejoin actual mTLS peers."""
    assert checksum == "sha256:" + digest(bundle / "manifest.json")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    configs: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(configurations.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        configs[config["replica"]["replica_id"]] = (path, config)
    assert configs
    retain_forensic(configs, output)
    files = [Path(__file__), probe, supervisor]
    files += sorted((ROOT / "core/controller-replication").rglob("*.rs"))
    files += sorted((ROOT / "apps/controller-replica/src").glob("*.rs"))
    files += [
        ROOT / "tools/quality/check_complete_producer_gc_processes.py",
        ROOT / "tools/quality/check_replica_content_recovery_processes.py",
    ]
    identities = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identities)
    cli = Processes(supervisor, output, ("--content-maintenance",))
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "scope": "original committed replay input repair and actual infrastructure rejoin only",
        "repairs": [],
        "startup_refusals": [],
    }
    original_streams = {
        member: rows(Path(config["content"]["root"]), "content_streams")
        for member, (_, config) in configs.items()
    }
    assert any(
        value["receipt"] is None
        for retained in original_streams.values()
        for value in retained.values()
    ), "actual retained interrupted stream is required"
    paths = {}
    completed_copies: list[tuple[dict[str, Any], dict[str, Any], int, int]] = []
    try:
        command(
            output,
            "verify-source",
            ["verify", "--bundle", str(bundle), "--manifest-digest", checksum],
        )
        command(
            output,
            "restore-source",
            [
                "restore",
                "--bundle",
                str(bundle),
                "--manifest-digest",
                checksum,
                "--destination",
                str(output / "source"),
                "--command-id",
                "actual-replay-repair-source",
            ],
        )
        manifest = json.loads((bundle / "manifest.json").read_text())
        sources = {}
        for component in manifest["profile"]["components"]:
            if component["kind"] == "artifact":
                source = output / "source/stores" / component["component_id"]
                owner = json.loads((source / ".retention-owner.json").read_text())["owner_id"]
                sources[owner] = source
        for member, (_, config) in configs.items():
            path = output / f"infrastructure-{member}.json"
            save(
                path,
                {
                    "replica": config["replica"],
                    "content_root": config["content"]["root"],
                    "parent_sql": str(Path(config["storage_directory"]) / "replica.sqlite3"),
                    "member_stores": config["content"]["bootstrap_owners"],
                    "staging_gc": True,
                    "restore_binding_digest": config["content"].get("restore_binding_digest"),
                    "initialization_parent": True,
                    "initialization": config["content"].get("initialization"),
                },
            )
            paths[member] = path
            inspection = cli.run(request(config, "inspect-replay-content"), f"inspect-{member}")
            missing = inspection["unavailable"]
            if not missing:
                continue
            before = parent_rows(config)
            started = time.monotonic()
            refused = subprocess.run(
                [str(probe), "serve", str(path)], input=b"", capture_output=True, timeout=15
            )
            observation: dict[str, Any] = {
                "member": member,
                "returncode": refused.returncode,
                "stdout": refused.stdout.decode(),
                "stderr": refused.stderr.decode(),
                "actual_seconds": time.monotonic() - started,
            }
            result["startup_refusals"].append(observation)
            save(output / f"startup-refusal-{member}.json", observation)
            assert refused.returncode != 0
            assert "committed replay content requires explicit repair" in (
                observation["stdout"] + observation["stderr"]
            )
            assert parent_rows(config) == before
            absent = request(config, "inspect-replay-content")
            absent["parent"]["path"] = str(output / f"absent-parent-{member}.sqlite3")
            cli.refuse(absent, f"absent-parent-{member}", "No such file")
            assert not Path(absent["parent"]["path"]).exists()
            assert not Path(absent["parent"]["path"] + ".writer.lock").exists()
            for index, required in enumerate(missing):
                source = sources[required["content"]["source_owner"]]
                repair = request(
                    config,
                    "repair-replay-content",
                    log_index=required["log_index"],
                    entry_digest=required["entry_digest"],
                    content=required["content"],
                    source_cas=str(source),
                    budget_ms=30_000,
                )
                wrong = {**repair, "entry_digest": "f" * 64}
                cli.refuse(wrong, f"wrong-entry-{member}-{index}", "entry digest differs")
                foreign = next(value for value in sources.values() if value != source)
                cli.refuse(
                    {**repair, "source_cas": str(foreign)},
                    f"wrong-owner-{member}-{index}",
                    "owns another original Artifact namespace",
                )
                absent_source = output / f"absent-source-{member}-{index}"
                cli.refuse(
                    {**repair, "source_cas": str(absent_source)},
                    f"absent-source-{member}-{index}",
                    "No such file",
                )
                assert not absent_source.exists()
                copied = cli.run(repair, f"actual-copy-{member}-{index}")
                assert copied["receipt"] is not None
                unavailable = source.with_name("unavailable-" + source.name)
                source.rename(unavailable)
                try:
                    assert cli.run(repair, f"readonly-original-{member}-{index}") == copied
                    cli.refuse(
                        {**repair, "budget_ms": 29_999},
                        f"changed-budget-{member}-{index}",
                        "different immutable input",
                    )
                    cli.refuse(
                        {**repair, "source_cas": str(absent_source)},
                        f"changed-source-{member}-{index}",
                        "different immutable input",
                    )
                finally:
                    unavailable.rename(source)
                assert parent_rows(config) == before
                completed_copies.append((config, copied, member, index))
                result["repairs"].append(
                    {"member": member, "original_requirement": required, "new_copy": copied}
                )
            assert not cli.run(request(config, "inspect-replay-content"), f"ready-{member}")[
                "unavailable"
            ]
        assert result["repairs"] and result["startup_refusals"]
        result["rejoin"] = cluster(probe, paths, output, "actual-repaired-rejoin", False)
        assert result["rejoin"]["passed"]
        result["released_copies"] = []
        for config, copied, member, index in completed_copies:
            released = cli.run(
                request(config, "retire-completed-content-copy", command_id=copied["command_id"]),
                f"release-own-copy-{member}-{index}",
            )
            assert released == copied
            result["released_copies"].append(released)
        result["restart"] = cluster(probe, paths, output, "actual-repaired-restart", False)
        assert result["restart"]["passed"]
        for member, (_, config) in configs.items():
            actual = rows(Path(config["content"]["root"]), "content_streams")
            assert all(actual[key] == value for key, value in original_streams[member].items())
        deployment = output / "deployment"
        deployment.mkdir(mode=0o700)
        inputs = configurations.parent / "restored/stores/inputs"
        for tree in ("tls", "node-tls"):
            if (inputs / tree).exists():
                shutil.copytree(inputs / tree, deployment / tree)
        for member, (_, config) in configs.items():
            save(deployment / f"configuration-{member}.json", config)
        backup = output / "post-repair"
        backup.mkdir(mode=0o700)
        save(backup / "profile.json", profile(deployment, backup))
        saved = command(
            backup,
            "backup",
            [
                "backup",
                "--profile",
                str(backup / "profile.json"),
                "--destination",
                str(backup / "bundle"),
                "--command-id",
                "actual-after-replay-repair",
            ],
        )
        result["post_repair_bundle"] = str(backup / "bundle")
        result["post_repair_manifest_digest"] = saved["manifest_digest"]
        cold = output / "cold-recovery"
        cold.mkdir(mode=0o700)
        command(
            cold,
            "verify-post-repair",
            [
                "verify",
                "--bundle",
                str(backup / "bundle"),
                "--manifest-digest",
                saved["manifest_digest"],
            ],
        )
        command(
            cold,
            "restore-post-repair",
            [
                "restore",
                "--bundle",
                str(backup / "bundle"),
                "--manifest-digest",
                saved["manifest_digest"],
                "--destination",
                str(cold / "restored"),
                "--command-id",
                "actual-replay-repair-cold-restore",
            ],
        )
        with tempfile.TemporaryDirectory(prefix="rg-replay-repair-ipc-") as ipc:
            restored = assemble(cold / "restored", Path(ipc), supervisor)
            cold_paths = {member: path for member, (path, _) in restored.items()}
            result["cold_recovery"] = cluster(
                probe, cold_paths, cold, "actual-repaired-cold-rejoin", False
            )
            assert result["cold_recovery"]["passed"]
            result["cold_restart"] = cluster(
                probe, cold_paths, cold, "actual-repaired-cold-restart", False
            )
            assert result["cold_restart"]["passed"]
            for member, (path, _) in restored.items():
                assert (
                    rows(Path(json.loads(path.read_text())["content"]["root"]), "content_streams")
                    == original_streams[member]
                )
        result["post_repair_cold_manifest_digest"] = saved["manifest_digest"]
        assert {str(path): digest(path) for path in files} == identities
        result["original_stream_rows_unchanged"] = True
        result["passed"] = True
    finally:
        cli.cleanup()
        result["actual_maintenance_processes"] = [row for _, row in cli.owned]
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require real interrupted declarations, a complete pinned source and frozen binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("configurations", "bundle", "probe", "supervisor", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-digest", required=True)
    args = parser.parse_args()
    run(
        args.configurations.resolve(strict=True),
        args.bundle.resolve(strict=True),
        args.manifest_digest,
        args.probe.resolve(strict=True),
        args.supervisor.resolve(strict=True),
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
