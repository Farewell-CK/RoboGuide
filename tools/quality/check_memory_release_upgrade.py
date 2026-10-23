"""Check actual old Controller rejection of scoped Memory release authority, without device work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from check_spatial_upgrade_gates import logical_digest
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for


def tables(database: Path) -> tuple[str, ...]:
    """Cover every actual authority table independently of transient SQLite WAL layout."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection, connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def run(directory: Path, old_controller: Path, old_node: Path | None = None) -> dict[str, Any]:
    """Preserve completed actual Node histories and reject downgrade at nonempty and empty heads."""
    result = json.loads((directory / "result.json").read_bytes())
    cleanup = json.loads((directory / "owned-process-exits.json").read_bytes())
    assert result["passed"] is True and cleanup["all_owned_processes_reaped"] is True
    assert all(not (Path("/proc") / str(row["pid"])).exists() for row in cleanup["processes"])
    current = ROOT / "target/debug/integration-server"
    assert (
        "sha256:" + hashlib.sha256(current.read_bytes()).hexdigest()
        == result["source_identity"]["target/debug/integration-server"]
    )
    expected_schema = subprocess.check_output(
        [str(current), "--application-schema"], cwd=ROOT, text=True
    ).strip()
    output = directory / ("release-upgrade-" + uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    receipts = []

    def reject(database: Path, name: str) -> None:
        """Require the actual schema rejection and unchanged complete logical authority rows."""
        before = logical_digest(database, tables(database))
        grpc, operator, artifact = address(), address(), address()
        observed = subprocess.run(
            [str(old_controller), grpc, str(database), operator, artifact, str(output / "cas")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=15,
        )
        (output / (name + ".stdout")).write_bytes(observed.stdout)
        (output / (name + ".stderr")).write_bytes(observed.stderr)
        assert observed.returncode != 0 and (
            ("unsupported checkpoint schema " + expected_schema).encode() in observed.stderr
            or (
                name == "actual-completed-memory-group"
                and any(
                    (
                        "unsupported event payload schema domain.EventPayload.json/v" + str(version)
                    ).encode()
                    in observed.stderr
                    for version in (26, 27)
                )
            )
        ), observed.stderr
        after = logical_digest(database, tables(database))
        assert before == after
        receipts.append(
            {"case": name, "returncode": observed.returncode, "before": before, "after": after}
        )

    reject(directory / "controller.sqlite3", "actual-completed-memory-group")
    empty = output / "zero-event"
    empty.mkdir(mode=0o700)
    grpc, operator, artifact = address(), address(), address()
    database = empty / "controller.sqlite3"
    migration_schemas = []
    origin = "http://" + operator
    for binary, label in ((old_controller, "legacy"), (current, "upgraded")):
        with (empty / f"{label}-controller.log").open("wb") as log:
            process = subprocess.Popen(
                [str(binary), grpc, str(database), operator, artifact, str(empty / "cas")],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
            )
        try:
            wait_for(
                lambda: request(origin, "/healthz")[0] == 200,
                f"{label} zero-Node Controller starts",
            )
            assert request(origin, "/v1/inventory")[1]["nodes"] == []
        finally:
            stop(process)
        with (
            closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection,
            connection,
        ):
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
            migration_schemas.append(
                connection.execute("SELECT schema FROM controller_checkpoint").fetchone()[0]
            )
    assert migration_schemas[0] != expected_schema
    assert migration_schemas[1] == expected_schema
    receipts.append(
        {"case": "actual-zero-node-forward-migration", "schemas": migration_schemas, "nodes": 0}
    )
    reject(database, "zero-node-zero-event")
    if old_node is not None:
        database = directory / "consumer/node-state/execution-journal.sqlite3"
        before = logical_digest(database, tables(database))
        original = directory / "consumer/node.toml"
        config = directory / "consumer" / ("old-node-" + uuid.uuid4().hex + ".toml")
        config.write_text(
            original.read_text()
            .replace("node-config/v0.13", "node-config/v0.11")
            .replace("node-config/v0.12", "node-config/v0.11")
        )
        node_environment = dict(environment)
        for suffix, filename in (
            ("CERT", "enrolment-next.crt"),
            ("KEY", "enrolment-next.key"),
            ("CA", "ca.crt"),
        ):
            node_environment["ROBOGUIDE_ARTIFACT_CLIENT_" + suffix] = str(
                directory / "pki" / filename
            )
        observed = subprocess.run(
            [str(old_node), str(config)],
            cwd=ROOT,
            env=node_environment,
            capture_output=True,
            timeout=15,
        )
        (output / "old-node.stderr").write_bytes(observed.stderr)
        assert (
            observed.returncode != 0
            and b"journal was written by a newer storage authority" in observed.stderr
        ), observed.stderr
        after = logical_digest(database, tables(database))
        assert before == after
        receipts.append(
            {
                "case": "actual-node-journal-old-binary",
                "returncode": observed.returncode,
                "before": before,
                "after": after,
                "old_binary": "sha256:" + hashlib.sha256(old_node.read_bytes()).hexdigest(),
            }
        )
    value = {
        "passed": True,
        "schema": expected_schema,
        "old_binary": "sha256:" + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
        "cases": receipts,
        "devices": 0,
    }
    save(output / "result.json", value)
    return value


def main() -> None:
    """Run only an explicitly selected completed profile and archived Controller executable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path, required=True)
    parser.add_argument("--old-node", type=Path)
    arguments = parser.parse_args()
    print(
        json.dumps(
            run(arguments.directory, arguments.old_controller, arguments.old_node), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
