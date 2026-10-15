"""Prove actual archived binaries reject prepared Memory authority, including zero-event storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tomllib
import uuid
from pathlib import Path
from typing import Any

from check_spatial_upgrade_gates import logical_digest
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for


def tables(database: Path) -> tuple[str, ...]:
    """Enumerate actual authority tables instead of omitting new preparation/revocation storage."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )


def run(directory: Path, old_node: Path, old_controller: Path) -> dict[str, Any]:
    """Retain original authority rows while exercising actual old-process startup fences."""
    admitted = json.loads((directory / "result.json").read_bytes())
    cleanup = json.loads((directory / "owned-process-exits.json").read_bytes())
    if admitted.get("passed") is not True or cleanup.get("all_owned_processes_reaped") is not True:
        raise ValueError("original actual file profile did not complete and reap its processes")
    if any((Path("/proc") / str(item["pid"])).exists() for item in cleanup["processes"]):
        raise ValueError("an original owned PID still exists")
    output = directory / ("upgrade-" + uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    receipts: list[dict[str, Any]] = []

    def rejected(arguments: list[str], marker: str, name: str) -> None:
        """Require the storage fence; an unrelated configuration error does not establish it."""
        process = subprocess.run(
            arguments, cwd=ROOT, env=environment, capture_output=True, timeout=12
        )
        (output / (name + ".stdout")).write_bytes(process.stdout)
        (output / (name + ".stderr")).write_bytes(process.stderr)
        assert process.returncode != 0 and marker in process.stderr.decode(errors="replace"), (
            process.stderr
        )
        receipts.append({"case": name, "returncode": process.returncode, "observed_fence": marker})

    for index, owner in enumerate(("source", "consumer")):
        config = directory / owner / "node.toml"
        document = tomllib.loads(config.read_text())
        database = Path(document["state_directory"]) / "execution-journal.sqlite3"
        before = logical_digest(database, tables(database))
        compatible = output / (owner + "-v09.toml")
        text = config.read_text().split("\n[memory_providers.availability]", 1)[0]
        text = text.replace("roboguide.node-config/v0.10", "roboguide.node-config/v0.9")
        compatible.write_text(text)
        name = "enrolment-first" if index == 0 else "enrolment-next"
        for prefix in ("ROBOGUIDE_ARTIFACT_CLIENT", "ROBOGUIDE_NODE_MTLS_CLIENT"):
            environment[prefix + "_CERT"] = str(directory / "pki" / (name + ".crt"))
            environment[prefix + "_KEY"] = str(directory / "pki" / (name + ".key"))
        environment["ROBOGUIDE_ARTIFACT_CLIENT_CA"] = str(directory / "pki/ca.crt")
        environment["ROBOGUIDE_NODE_MTLS_SERVER_CA"] = str(directory / "pki/ca.crt")
        environment["ROBOGUIDE_NODE_MTLS_DOMAIN"] = "localhost"
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
            assert (
                connection.execute("SELECT version FROM node_journal_storage_schema").fetchone()[0]
                == 9
            )
        rejected(
            [str(old_node), str(compatible)],
            "journal was written by a newer storage authority",
            owner + "-old-node-v8",
        )
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version=8")
        try:
            rejected(
                [str(old_node), str(compatible)],
                "journal storage marker is unsupported",
                owner + "-old-node-v8-header-reset",
            )
        finally:
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA user_version=9")
        assert logical_digest(database, tables(database)) == before
    database = directory / "controller.sqlite3"
    before = logical_digest(database, tables(database))
    grpc, operator, artifact = (address() for _ in range(3))
    rejected(
        [str(old_controller), grpc, str(database), operator, artifact, str(directory / "cas")],
        "unsupported checkpoint schema roboguide.controller-checkpoint/v35",
        "old-controller-v34",
    )
    assert logical_digest(database, tables(database)) == before
    current = ROOT / "target/debug/integration-server"
    assert (
        "sha256:" + hashlib.sha256(current.read_bytes()).hexdigest()
        == admitted["source_identity"]["target/debug/integration-server"]
    )
    fresh = output / "zero-event"
    fresh.mkdir(mode=0o700)
    grpc, operator, artifact = (address() for _ in range(3))
    arguments = [
        str(current),
        grpc,
        str(fresh / "controller.sqlite3"),
        operator,
        artifact,
        str(fresh / "cas"),
    ]
    environment["ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN"] = "1"
    with (fresh / "controller.log").open("wb") as log:
        process = subprocess.Popen(arguments, cwd=ROOT, env=environment, stdout=log, stderr=log)
    try:
        origin = "http://" + operator
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "zero-event Controller startup")
        status, response = request(
            origin,
            "/v1/memory-authorizations",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": 0,
                "registry": {
                    "schema_version": "roboguide.memory-authorization/v0.2",
                    "registry_id": uuid.uuid4().hex,
                    "revision": 1,
                    "policies": {},
                },
            },
        )
        assert status == 200, response
        assert request(origin, "/v1/inventory")[1]["nodes"] == []
    finally:
        stop(process)
    database = fresh / "controller.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    before = logical_digest(database, tables(database))
    rejected(
        [str(old_controller), grpc, str(database), operator, artifact, str(fresh / "cas")],
        "unsupported checkpoint schema roboguide.controller-checkpoint/v35",
        "old-controller-v34-zero-event",
    )
    assert logical_digest(database, tables(database)) == before
    result = {
        "passed": True,
        "devices": 0,
        "receipts": receipts,
        "original_authority_rows_unchanged": True,
        "probe_directory": str(output),
        "old_node_digest": "sha256:" + hashlib.sha256(old_node.read_bytes()).hexdigest(),
        "old_controller_digest": "sha256:"
        + hashlib.sha256(old_controller.read_bytes()).hexdigest(),
    }
    save(directory / "upgrade-gates.json", result)
    return result


def main() -> None:
    """Require explicit archived binaries and a previously completed owned process profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--old-node", required=True, type=Path)
    parser.add_argument("--old-controller", required=True, type=Path)
    arguments = parser.parse_args()
    print(
        json.dumps(
            run(arguments.directory, arguments.old_node, arguments.old_controller), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
