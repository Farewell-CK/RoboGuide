"""Cold-restore actual scoped map histories after owned processes exit, retaining local deletion."""

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

from check_recovery_bundle_processes import logical_rows
from check_state_verifier_processes import ROOT, address, save
from memory_preparation_profile import node_environment


def retained_rows(path: Path) -> dict[str, set[str]]:
    """Observe actual pre-existing rows separately from new Snapshot operations and pins."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        tables = [
            row[0] for row in sql.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        ]
        return {
            table: {
                repr(row) for row in sql.execute('SELECT * FROM "' + table.replace('"', '""') + '"')
            }
            for table in tables
        }


def run(
    source: Path, output: Path, old_controller: Path, old_node: Path, old_memory: Path
) -> dict[str, Any]:
    """Use actual CLI files, raw SQL and genuine old writers; create no new Node or device."""
    source = source.resolve(strict=True)
    original = json.loads((source / "result.json").read_bytes())
    cleanup = json.loads((source / "owned-process-exits.json").read_bytes())
    assert original["passed"] and cleanup["all_owned_processes_reaped"]
    assert json.loads((source / "scoped-lifecycle-result.json").read_bytes())["passed"]
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    components: list[dict[str, Any]] = []
    for identity, kind, root, database, dependencies, storage in [
        (
            "controller",
            "controller",
            source,
            "controller.sqlite3",
            ["cas"],
            "roboguide.controller-storage/legacy",
        ),
        ("cas", "artifact", source / "cas", None, [], "roboguide.artifact-storage/v3"),
        (
            "producer-node",
            "node",
            source / "node-state",
            "execution-journal.sqlite3",
            ["controller"],
            None,
        ),
        (
            "consumer-node",
            "node",
            source / "consumer/node-state",
            "execution-journal.sqlite3",
            ["controller", "memory"],
            None,
        ),
        ("memory", "memory", source / "consumer/backend", None, ["controller"], None),
    ]:
        components.append(
            {
                "component_id": identity,
                "kind": kind,
                "root": str(root),
                "database": database,
                "trees": [],
                "depends_on": dependencies,
                **({"storage_schema": storage} if storage else {}),
            }
        )
    if (source / "original-request.json").exists():
        for identity, kind, database in (
            ("mission-request", "mission", "mission.sqlite3"),
            ("runtime-mi", "runtime-mi", "runtime-mi.sqlite3"),
        ):
            assert (source / database).is_file()
            components.append(
                {
                    "component_id": identity,
                    "kind": kind,
                    "root": str(source),
                    "database": database,
                    "trees": [],
                    "depends_on": ["controller"],
                }
            )
    profile = {
        "schema": "roboguide.recovery-profile/v0.26",
        "profile_id": "actual-scoped-map-lifecycle",
        "revision": 1,
        "max_bytes": 2 * 1024**3,
        "max_files": 10000,
        "budget_seconds": 120,
        "artifact_replica_sets": [],
        "components": components,
    }
    profile_path = output / "profile.json"
    save(profile_path, profile)
    prior: dict[str, dict[str, set[str]]] = {}
    for component in components:
        if component["kind"] == "artifact":
            continue
        names = (
            [component["database"]]
            if component["database"]
            else [
                "memory.sqlite3",
                "memory-use.sqlite3",
                "file-jobs.sqlite3",
                "active-map/active-map.sqlite3",
            ]
        )
        for name in names:
            prior[component["component_id"] + "/" + name] = retained_rows(
                Path(component["root"]) / name
            )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def execute(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Retain real original argv, native result and exact stdout/stderr for every CLI phase."""
        argv = [sys.executable, "-m", "roboguide_recovery", *arguments]
        result = subprocess.run(
            argv, cwd=ROOT, env=environment, capture_output=True, timeout=140, check=False
        )
        commands.append(
            {
                "arguments": argv,
                "exit_code": result.returncode,
                "stdout": result.stdout.decode(),
                "stderr": result.stderr.decode(),
            }
        )
        save(output / "commands.json", commands)
        assert (result.returncode == 0) == passed, commands[-1]
        return dict(json.loads(result.stdout)) if passed else commands[-1]

    bundle = output / "bundle"
    operation = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    backup_id, restore_id = "scoped-backup-" + operation, "scoped-restore-" + operation
    backed_up = execute(
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(bundle),
        "--command-id",
        backup_id,
    )
    pin = backed_up["manifest_digest"]
    execute("verify", "--bundle", str(bundle), "--manifest-digest", pin)
    restored = output / "restored"
    receipt = execute(
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        pin,
        "--destination",
        str(restored),
        "--command-id",
        restore_id,
    )
    assert receipt["schema"] == "roboguide.restore-receipt/v0.26"
    assert (
        execute(
            "backup",
            "--profile",
            str(profile_path),
            "--destination",
            str(bundle),
            "--command-id",
            backup_id,
        )
        == backed_up
    )
    assert (
        execute(
            "restore",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            pin,
            "--destination",
            str(restored),
            "--command-id",
            restore_id,
        )
        == receipt
    )
    roots = {item["component_id"]: Path(item["root"]) for item in receipt["components"]}
    originals: dict[str, Any] = {}
    for component in components:
        identity = component["component_id"]
        if identity == "cas":
            continue
        names = (
            [component["database"]]
            if component["database"]
            else [
                "memory.sqlite3",
                "memory-use.sqlite3",
                "file-jobs.sqlite3",
                "active-map/active-map.sqlite3",
            ]
        )
        for name in names:
            bundled = bundle / "stores" / identity / name
            before = logical_rows(bundled)
            after = logical_rows(roots[identity] / name)
            assert before == after, (identity, name)
            rows = retained_rows(bundled)
            assert all(
                records <= rows[table] for table, records in prior[identity + "/" + name].items()
            ), (identity, name)
            originals[identity + "/" + name] = before
    with closing(sqlite3.connect(roots["controller"] / "controller.sqlite3")) as sql:
        schema, raw = sql.execute(
            "SELECT schema,checkpoint_json FROM controller_checkpoint"
        ).fetchone()
        assert schema == "roboguide.controller-checkpoint/v57"
        authority = json.loads(raw)
        assert authority["memory_handoffs"] and authority["runtime_revisions"]
    with closing(sqlite3.connect(roots["memory"] / "memory.sqlite3")) as sql:
        assert sql.execute("PRAGMA user_version").fetchone() == (5,)
        instance, provider = sql.execute("SELECT instance,provider FROM identity").fetchone()
        retired = sql.execute("SELECT count(*) FROM retired_imports").fetchone()[0]
        assert retired >= 4
        checksum, state = sql.execute(
            "SELECT content_digest,state FROM content_tombstones"
        ).fetchone()
        assert state == "deleted" and not (roots["memory"] / "content" / checksum[7:]).exists()
    rejected: list[dict[str, Any]] = []
    old_environment = dict(environment)
    old_environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = "roboguide.artifact-storage/v3"
    old_environment["PYTHONPATH"] = str(old_memory.resolve(strict=True))
    node_config = output / "old-node.toml"
    text = (
        (source / "consumer/node.toml")
        .read_text()
        .replace("node-config/v0.15", "node-config/v0.14")
    )
    text = text.replace('contract = "roboguide.memory-provider-availability/v0.2"\n', "")
    text = text.replace(str(source / "consumer/node-state"), str(roots["consumer-node"]))
    node_config.write_text(text)
    memory_code = (
        "from pathlib import Path; from roboguide_memory_provider.store import MemoryStore; "
        "import sys; MemoryStore(Path(sys.argv[1]),sys.argv[2],(Path(sys.argv[1]),),"
        "536870912,enable_snapshot_references=True)"
    )
    for label, argv, message in [
        (
            "controller",
            [
                str(old_controller.resolve(strict=True)),
                address(),
                str(roots["controller"] / "controller.sqlite3"),
                address(),
                address(),
                str(roots["cas"]),
            ],
            "v57",
        ),
        ("node", [str(old_node.resolve(strict=True)), str(node_config)], "newer storage authority"),
        (
            "memory",
            [sys.executable, "-c", memory_code, str(roots["memory"]), provider],
            "unsupported Memory backend schema",
        ),
    ]:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=node_environment(old_environment, source, "enrolment-next")
            if label == "node"
            else old_environment,
            capture_output=True,
            timeout=15,
            check=False,
        )
        observation: dict[str, Any] = {
            "owner": label,
            "arguments": argv,
            "native_wait": completed.returncode,
            "stdout": completed.stdout.decode(),
            "stderr": completed.stderr.decode(),
        }
        rejected.append(observation)
        save(output / "old-writer-observations.json", rejected)
        assert completed.returncode != 0 and message in observation["stderr"], observation
    for component in components:
        if component["kind"] == "artifact":
            continue
        names = (
            [component["database"]]
            if component["database"]
            else [
                "memory.sqlite3",
                "memory-use.sqlite3",
                "file-jobs.sqlite3",
                "active-map/active-map.sqlite3",
            ]
        )
        for name in names:
            assert (
                logical_rows(roots[component["component_id"]] / name)
                == originals[component["component_id"] + "/" + name]
            )
    result = {
        "passed": True,
        "manifest_digest": pin,
        "schema": receipt["schema"],
        "original_sql": originals,
        "old_writers_refused": rejected,
        "original_provider_instance": instance,
        "retired_imports": retired,
        "local_deleted_bytes_remain_absent": True,
        "new_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "source_result_digest": "sha256:"
        + hashlib.sha256((source / "result.json").read_bytes()).hexdigest(),
    }
    save(output / "result.json", result)
    return result


def main() -> None:
    """Require explicit actual source, genuine old binaries/package and a new output root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path, required=True)
    parser.add_argument("--old-node", type=Path, required=True)
    parser.add_argument("--old-memory-package", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.source,
                args.output,
                args.old_controller,
                args.old_node,
                args.old_memory_package,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
