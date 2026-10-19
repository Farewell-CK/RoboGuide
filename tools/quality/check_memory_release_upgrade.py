"""Check actual old Controller rejection of scoped Memory release authority, without device work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from check_spatial_upgrade_gates import logical_digest
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for


def tables(database: Path) -> tuple[str, ...]:
    """Cover every actual authority table independently of transient SQLite WAL layout."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def run(directory: Path, old_controller: Path) -> dict[str, Any]:
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
        assert (
            observed.returncode != 0
            and b"unsupported checkpoint schema roboguide.controller-checkpoint/v38"
            in observed.stderr
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
    with (empty / "controller.log").open("wb") as log:
        process = subprocess.Popen(
            [str(current), grpc, str(database), operator, artifact, str(empty / "cas")],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
    try:
        origin = "http://" + operator
        wait_for(
            lambda: request(origin, "/healthz")[0] == 200, "current zero-Node Controller starts"
        )
        assert request(origin, "/v1/inventory")[1]["nodes"] == []
    finally:
        stop(process)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert (
            connection.execute("SELECT schema FROM controller_checkpoint").fetchone()[0]
            == "roboguide.controller-checkpoint/v38"
        )
    reject(database, "zero-node-zero-event")
    value = {
        "passed": True,
        "schema": "roboguide.controller-checkpoint/v38",
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
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.directory, arguments.old_controller), sort_keys=True))


if __name__ == "__main__":
    main()
