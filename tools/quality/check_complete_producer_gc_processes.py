"""Compose actual retired-upload producers, mTLS Snapshot/purge and final local CAS GC.

Use only an explicit complete original bundle and real owned infrastructure processes.
No embodied Nodes, devices, models, simulators or physical continuity are created.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from check_network_content_processes import Actor
from check_replica_content_processes import Processes, digest, save
from check_replica_content_recovery_processes import assemble, command, profile

ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "content_producers": "operation_id",
    "content_readers": "reader_id",
    "content_streams": "stream_id",
    "content_references": "reference_id",
    "content_gc_commands": "operation_id",
    "content_tombstones": "content_key",
    "content_staging_gc_commands": "operation_id",
    "content_staging_gc_items": "item_id",
}


def rows(root: Path, table: str) -> dict[str, Any]:
    """Read only the closed managed table set from actual readonly original SQL."""
    column = TABLES[table]
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        return {
            key: json.loads(raw) for key, raw in sql.execute(f"SELECT {column},record FROM {table}")
        }


def references(root: Path, key: dict[str, Any]) -> list[dict[str, Any]]:
    """Select all actual independent active references for exactly one qualified key."""
    return [
        value
        for value in rows(root, "content_references").values()
        if value["active"] and value["reference"]["content"] == key
    ]


def retain_forensic(configs: dict[int, tuple[Path, dict[str, Any]]], output: Path) -> None:
    """Freeze exact original paired writers for forensic evidence, never a valid recovery bundle."""
    with ExitStack() as held:
        paths = []
        for _, config in configs.values():
            parent = Path(config["storage_directory"]) / "replica.sqlite3"
            root = Path(config["content"]["root"])
            paths += [
                parent,
                Path(str(parent) + ".writer.lock"),
                root / ".writer.lock",
                root / "content.sqlite3",
            ]
        for path in sorted(paths):
            handle = held.enter_context(os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            actual = os.fstat(handle.fileno())
            current = path.stat(follow_symlinks=False)
            assert (actual.st_dev, actual.st_ino) == (current.st_dev, current.st_ino)
        forensic = output / "forensic-only"
        forensic.mkdir(mode=0o700)
        observed = []
        for member, (_, config) in configs.items():
            root = Path(config["content"]["root"])
            shutil.copytree(root, forensic / f"root-{member}")
            parent = Path(config["storage_directory"])
            target = forensic / f"parent-{member}"
            target.mkdir(mode=0o700)
            for file in parent.glob("replica.sqlite3*"):
                assert file.is_file() and not file.is_symlink()
                shutil.copyfile(file, target / file.name)
            observed.append(
                {"member": member, "actual_root": str(root), "actual_parent": str(parent)}
            )
        files = {
            str(path.relative_to(forensic)): digest(path)
            for path in forensic.rglob("*")
            if path.is_file()
        }
        save(
            output / "forensic-only.json",
            {
                "scope": "exact raw forensic files; not a verified backup or restore grant",
                "originals": observed,
                "files": files,
            },
        )


def local(config: dict[str, Any], operation: str, **fields: Any) -> dict[str, Any]:
    """Declare only the exact existing Root8/backing and closed OS-admin operation."""
    root = Path(config["content"]["root"])
    identity = json.loads((root / ".content-creation.json").read_text())
    return {
        "operation": operation,
        "member": {
            "content_root": str(root),
            "profile_identity": identity["profile_identity"],
            "member": identity["member"],
            "content_layout": "roboguide.replica-content-layout/v0.8",
        },
        "restore_binding_digest": config["content"]["restore_binding_digest"],
        **fields,
    }


def retire(config: dict[str, Any], cli: Processes, phase: str) -> None:
    """Delegate original streams and uncertain readers to their actual end-proof APIs."""
    root = Path(config["content"]["root"])
    member = config["replica"]["replica_id"]
    for identifier, original in rows(root, "content_streams").items():
        receipt = cli.run(
            local(
                config,
                "retire-content-stream",
                command_id=original["intent"]["command_id"],
                budget_ms=30_000,
            ),
            f"{phase}-stream-{member}-{identifier[:8]}",
        )
        assert receipt["original"] == original
    for identifier, original in rows(root, "content_readers").items():
        if original["receipt"] is None and original["process_end"] is None:
            cli.run(
                local(
                    config, "retire-ended-content-reader", reader_id=original["intent"]["reader_id"]
                ),
                f"{phase}-reader-{member}-{identifier[:8]}",
            )


def leader(actors: dict[int, Actor], expected: int | None = None) -> int:
    """Wait only for an actual current metadata quorum; labels alone grant no readiness."""
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        for member, actor in actors.items():
            if expected is not None and member != expected:
                continue
            status = actor.ask({"action": "status"})
            if (
                status["epoch"] is not None
                and status["quorum"]
                and status["readiness"]["content_ready"]
            ):
                return member
        time.sleep(0.1)
    raise ValueError("actual original infrastructure quorum unavailable within observation bound")


def cluster(
    probe: Path, paths: dict[int, Path], output: Path, phase: str, compact: bool
) -> dict[str, Any]:
    """Own each actual incarnation through original waits and optional member-local compaction."""
    owned: list[Actor] = []
    actors: dict[int, Actor] = {}
    result: dict[str, Any] = {
        "passed": False,
        "actual_processes": [],
        "compactions": [],
        "first_maintenance_budget_ms": 30_000,
    }

    def record_compaction(member: int) -> None:
        """Retain the first whole-operation budget and every actual reply or failure time."""
        observation: dict[str, Any] = {"member": member, "result": None, "budget_ms": 30_000}
        result["compactions"].append(observation)
        started = time.monotonic()
        try:
            observation["result"] = actors[member].ask(
                {"action": "compact-snapshot", "budget_ms": 30_000}
            )
        finally:
            observation["actual_seconds"] = time.monotonic() - started

    with ExitStack() as logs:
        try:
            for member, path in paths.items():
                log = logs.enter_context((output / f"{phase}-{member}.log").open("wb"))
                actors[member] = Actor(probe, path, log, owned)
            current = leader(actors)
            remaining = set(paths)
            if compact:
                record_compaction(current)
                remaining.remove(current)
            deadline = time.monotonic() + 60
            while True:
                complete = actors[current].ask({"action": "quorum"}, required=False)
                if set(complete.get("complete_members", [])) == set(paths):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "actual current required bytes did not reach every original member"
                    )
                time.sleep(0.1)
                current = leader(actors)
            result["original_complete_quorum"] = complete
            if compact:
                while remaining:
                    target = min(remaining)
                    source_member = current
                    actors[current].ask(
                        {
                            "action": "transfer-to",
                            "member": target,
                            "transfer_id": f"original-producer-gc-{current}-{target}",
                        }
                    )
                    current = leader(actors, target)
                    assert actors[target].ask({"action": "reconcile-transfer"})[
                        "original_transfer_reconciled"
                    ]
                    assert actors[source_member].ask({"action": "reconcile-transfer"})[
                        "original_transfer_reconciled"
                    ]
                    record_compaction(current)
                    remaining.remove(current)
            result["passed"] = True
        finally:
            cleanup_errors = []
            for actor in reversed(owned):
                try:
                    actor.stop()
                except Exception as error:
                    cleanup_errors.append(str(error))
                    if actor.process.poll() is None:
                        actor.process.kill()
                    actor.record["returncode"] = actor.process.wait(timeout=10)
                    actor.record["forced_cleanup"] = True
                finally:
                    result["actual_processes"].append(actor.record)
                    if actor.record.get("returncode") != 0:
                        result["passed"] = False
            if cleanup_errors:
                result["passed"] = False
                result["cleanup_errors"] = cleanup_errors
            save(output / f"{phase}.json", result)
            if cleanup_errors:
                raise ValueError(
                    "actual infrastructure cleanup failed: " + "; ".join(cleanup_errors)
                )
    return result


def run(
    source: Path,
    bundle: Path,
    checksum: str,
    probe: Path,
    supervisor: Path,
    output: Path,
    existing: Path | None = None,
) -> dict[str, Any]:
    """Restore actual inputs, prove blockers, compact real parents and reclaim exact bytes."""
    original = json.loads((source / "result.json").read_text())
    assert original["passed"] and all(
        row["returncode"] == 0 for row in original["actual_supervisors"]
    )
    assert checksum == "sha256:" + digest(bundle / "manifest.json")
    retired = original["actual_completed_retirement"]["receipt"]
    key = {
        "source_owner": retired["store_owner_id"],
        "content_digest": retired["upload"]["content_digest"],
        "byte_size": retired["upload"]["byte_size"],
    }
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = [
        probe,
        supervisor,
        Path(__file__),
        ROOT / "core/controller-replication/examples/content_network_probe.rs",
        ROOT / "apps/controller-replica/src/content_maintenance.rs",
        ROOT / "tools/quality/check_replica_content_recovery_processes.py",
        ROOT / "tools/quality/check_network_content_processes.py",
        ROOT / "tools/quality/check_replica_content_processes.py",
    ]
    files += sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py"))
    files += sorted((ROOT / "core/controller-replication").rglob("*.rs"))
    files += sorted((ROOT / "apps/controller-replica/src").glob("*.rs"))
    identity = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identity)
    cli = Processes(supervisor, output, ("--content-maintenance",))
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "source_contains_original_software_node_history": original.get(
            "source_contains_original_software_node_history", False
        ),
        "scope": "actual producer/member-local CAS lifecycle; no global erasure or physical proof",
        "retired_content": key,
        "original_bundle": str(bundle),
        "original_manifest_digest": checksum,
        "members": [],
    }
    try:
        command(
            output,
            "verify-original",
            ["verify", "--bundle", str(bundle), "--manifest-digest", checksum],
        )
        if existing is None:
            command(
                output,
                "restore-original",
                [
                    "restore",
                    "--bundle",
                    str(bundle),
                    "--manifest-digest",
                    checksum,
                    "--destination",
                    str(output / "restored"),
                    "--command-id",
                    "actual-complete-producer-gc",
                ],
            )
        with tempfile.TemporaryDirectory(prefix="rg-prod-gc-") as ipc:
            if existing is None:
                configs = assemble(output / "restored", Path(ipc), supervisor)
            else:
                configs = {}
                for path in sorted(existing.glob("configuration-*.json")):
                    config = json.loads(path.read_text())
                    configs[config["replica"]["replica_id"]] = (path, config)
                assert configs
                retain_forensic(configs, output)
                result["existing_configurations"] = str(existing)
                result["origin_bundle_is_not_current_state"] = True
            paths: dict[int, Path] = {}
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
                        "restore_binding_digest": config["content"]["restore_binding_digest"],
                        "initialization_parent": config.get(
                            "coordinated_archive_base", "roboguide.controller-replica-process/v0.7"
                        )
                        == "roboguide.controller-replica-process/v0.7",
                        "coordinated_archive": config.get("coordinated_archive_base") is not None,
                        "initialization": config["content"].get("initialization"),
                    },
                )
                paths[member] = path
            before: dict[int, dict[str, Any]] = {}
            original_streams = {}
            for member, (_, config) in configs.items():
                root = Path(config["content"]["root"])
                before[member] = rows(root, "content_producers")
                original_streams[member] = rows(root, "content_streams")
                retire(config, cli, "before-purge")
                blockers = references(root, key)
                assert blockers and any(row["reference"]["kind"] == "log" for row in blockers)
                blocked = cli.run(
                    local(
                        config,
                        "collect-content",
                        command_id="original-before-parent-retirement",
                        budget_ms=30_000,
                    ),
                    f"blocked-gc-{member}",
                )
                assert all(
                    item["receipt"]["content"] != key
                    for item in blocked["original"]["intent"]["items"]
                )
                blob = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
                assert (
                    blob.stat().st_size == key["byte_size"]
                    and "sha256:" + digest(blob) == key["content_digest"]
                )
                result["members"].append(
                    {"member": member, "original_blockers": blockers, "blocked_gc": blocked}
                )
            result["compaction"] = cluster(probe, paths, output, "actual-parent-retirement", True)
            assert result["compaction"]["passed"]
            assert key not in result["compaction"]["original_complete_quorum"]["target"]["inputs"]
            for observation in result["members"]:
                member = observation["member"]
                config = configs[member][1]
                root = Path(config["content"]["root"])
                retire(config, cli, "after-purge")
                active = references(root, key)
                observation["remaining_references"] = active
                save(output / f"references-after-purge-{member}.json", active)
                assert not active, f"independent original references remain on {member}"
                after = rows(root, "content_producers")
                streams = rows(root, "content_streams")
                assert all(streams[key] == value for key, value in original_streams[member].items())
                for identifier, prior in before[member].items():
                    assert after[identifier]["intent"] == prior["intent"]
                    for field in ("publication", "worker_closed_ms", "observation", "resolution"):
                        if prior[field] is not None:
                            assert after[identifier][field] == prior[field]
                collected = cli.run(
                    local(
                        config,
                        "collect-content",
                        command_id="original-after-parent-retirement",
                        budget_ms=30_000,
                    ),
                    f"final-gc-{member}",
                )
                assert key in [
                    item["receipt"]["content"] for item in collected["original"]["intent"]["items"]
                ]
                assert collected["unknown"] == 0 and collected["deleted"] >= 1
                assert (
                    cli.run(
                        local(
                            config,
                            "collect-content",
                            command_id="original-after-parent-retirement",
                            budget_ms=30_000,
                        ),
                        f"same-id-gc-{member}",
                    )
                    == collected
                )
                blob = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
                assert not blob.exists()
                observation["final_gc"] = collected
                targets = [
                    {"kind": "stream", "command_id": value["intent"]["command_id"]}
                    for value in rows(root, "content_streams").values()
                ]
                if targets:
                    staging = cli.run(
                        local(
                            config,
                            "collect-staging",
                            command_id="original-after-complete-producer-retirement",
                            targets=targets,
                            budget_ms=30_000,
                        ),
                        f"final-staging-{member}",
                    )
                    assert staging["unknown"] == 0
                    observation["staging_gc"] = staging
                assert not list((root / "staging-v1").iterdir())
            result["restart"] = cluster(probe, paths, output, "actual-after-gc-restart", False)
            assert result["restart"]["passed"]
            deployment = output / "deployment"
            deployment.mkdir(mode=0o700)
            inputs = (
                output / "restored/stores/inputs"
                if existing is None
                else existing.parent / "restored/stores/inputs"
            )
            for tree in ("tls", "node-tls"):
                if (inputs / tree).exists():
                    shutil.copytree(inputs / tree, deployment / tree)
            for member, (_, config) in configs.items():
                save(deployment / f"configuration-{member}.json", config)
            backup = output / "post-gc"
            backup.mkdir(mode=0o700)
            declaration = profile(deployment, backup)
            save(backup / "profile.json", declaration)
            completed = command(
                backup,
                "backup",
                [
                    "backup",
                    "--profile",
                    str(backup / "profile.json"),
                    "--destination",
                    str(backup / "bundle"),
                    "--command-id",
                    "actual-after-complete-producer-gc",
                ],
            )
            result["post_gc_bundle"] = str(backup / "bundle")
            result["post_gc_manifest_digest"] = completed["manifest_digest"]
        assert {str(path): digest(path) for path in files} == identity
        result["passed"] = True
    finally:
        cli.cleanup()
        result["actual_maintenance_processes"] = [row for _, row in cli.owned]
        save(output / "result.json", result)
    return result


def verify_recovery(
    original: Path, cold: Path, *, expected_manifest: str | None = None
) -> dict[str, Any]:
    """Independently check original GC/producer evidence and absent bytes after cold leaders."""
    source = json.loads((original / "result.json").read_text())
    restored = json.loads((cold / "result.json").read_text())
    assert source["passed"] and restored["passed"]
    assert restored["manifest_digest"] == (
        source["post_gc_manifest_digest"] if expected_manifest is None else expected_manifest
    )
    assert all(row["returncode"] == 0 for row in restored["actual_processes"])
    expected = {row["member"] for row in source["members"]}
    assert {row["member"] for row in restored["observed_leaders"]} == expected
    observed = []
    for member in sorted(expected):
        config = json.loads((original / f"infrastructure-{member}.json").read_text())
        old_root = Path(config["content_root"])
        new_root = cold / f"restored/stores/root-{member}"
        for table in (
            "content_gc_commands",
            "content_tombstones",
            "content_staging_gc_commands",
            "content_staging_gc_items",
        ):
            assert rows(new_root, table) == rows(old_root, table)
        new_producers = rows(new_root, "content_producers")
        for identifier, prior in rows(old_root, "content_producers").items():
            actual = new_producers[identifier]
            assert actual["intent"] == prior["intent"]
            for field in ("publication", "worker_closed_ms", "observation", "resolution"):
                if prior[field] is not None:
                    assert actual[field] == prior[field]
        key = source["retired_content"]
        assert not references(new_root, key)
        assert not (
            new_root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
        ).exists()
        parent = cold / f"restored/stores/parent-{member}/replica.sqlite3"
        with closing(sqlite3.connect(parent.as_uri() + "?mode=ro", uri=True)) as sql:
            current = json.loads(
                sql.execute(
                    "SELECT value FROM replica_v1_meta WHERE key='content_state'"
                ).fetchone()[0]
            )
        inputs = current["candidate"]["inputs"]
        assert key not in inputs
        for content in inputs:
            file = (
                new_root
                / "content-v1"
                / content["source_owner"][15:]
                / content["content_digest"][7:]
            )
            assert file.stat().st_size == content["byte_size"]
            assert "sha256:" + digest(file) == content["content_digest"]
        observed.append(
            {
                "member": member,
                "original_gc_and_tombstones": True,
                "original_producers": len(new_producers),
                "retired_bytes_absent": True,
                "independently_hashed_required_inputs": inputs,
            }
        )
    result = {
        "passed": True,
        "manifest_digest": restored["manifest_digest"],
        "members": observed,
        "scope": "actual original GC/producer preservation and byte absence, not global erasure",
    }
    save(cold / "independent-gc-recovery.json", result)
    return result


def main() -> None:
    """Require explicit retained real sources, pinned bundle and frozen maintained executables."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "bundle", "probe", "supervisor", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-digest", required=True)
    parser.add_argument("--existing-configurations", type=Path)
    args = parser.parse_args()
    run(
        args.source.resolve(strict=True),
        args.bundle.resolve(strict=True),
        args.manifest_digest,
        args.probe.resolve(strict=True),
        args.supervisor.resolve(strict=True),
        args.output.resolve(),
        None
        if args.existing_configurations is None
        else args.existing_configurations.resolve(strict=True),
    )


if __name__ == "__main__":
    main()
