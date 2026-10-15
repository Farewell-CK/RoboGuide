"""Check archived binaries against completed private spatial authority without replaying work."""

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

from check_identity_processes import client, request
from check_state_verifier_processes import ROOT, address, stop, wait_for


def logical_digest(database: Path, tables: tuple[str, ...]) -> str:
    """Hash actual ordered authority rows independently of WAL/checkpoint file layout."""
    checksum = hashlib.sha256()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        for table in tables:
            checksum.update(table.encode() + b"\0")
            columns = len(connection.execute(f"SELECT * FROM {table} LIMIT 0").description or ())
            ordering = ",".join(str(index + 1) for index in range(columns))
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {ordering}"):
                encoded = [
                    {
                        "type": type(value).__name__,
                        "value": value.hex() if isinstance(value, bytes) else value,
                    }
                    for value in row
                ]
                checksum.update(json.dumps(encoded, separators=(",", ":")).encode() + b"\0")
    return "sha256:" + checksum.hexdigest()


def run(directory: Path, old_node: Path, old_controller: Path, output: Path) -> dict[str, Any]:
    """Preserve authority and retain real old-binary rejection logs after owned processes stop."""
    if output.exists():
        raise FileExistsError(output)
    admitted = json.loads((directory / "result.json").read_bytes())
    if admitted.get("passed") is not True:
        raise ValueError("original spatial process admission did not pass")
    if admitted.get("transport_profile") != "actual-mutual-tls-and-enrolled-node":
        raise ValueError(
            "upgrade check requires the actual authenticated original software profile"
        )
    cleanup = json.loads((directory / "owned-process-exits.json").read_bytes())
    if cleanup.get("all_owned_processes_reaped") is not True or any(
        (Path("/proc") / str(item["pid"])).exists() for item in cleanup["processes"]
    ):
        raise ValueError("original owned processes are not conclusively stopped")
    config = directory / "node.toml"
    configuration = tomllib.loads(config.read_text())
    state_root = Path(configuration["state_directory"])
    node_database = state_root / "execution-journal.sqlite3"
    controller_database = directory / "controller.sqlite3"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    certificates = directory / "pki"
    for prefix in ("ROBOGUIDE_ARTIFACT_CLIENT", "ROBOGUIDE_NODE_MTLS_CLIENT"):
        environment[prefix + "_CERT"] = str(certificates / "enrolment-first.crt")
        environment[prefix + "_KEY"] = str(certificates / "enrolment-first.key")
    environment["ROBOGUIDE_ARTIFACT_CLIENT_CA"] = str(certificates / "ca.crt")
    environment["ROBOGUIDE_NODE_MTLS_SERVER_CA"] = str(certificates / "ca.crt")
    environment["ROBOGUIDE_NODE_MTLS_DOMAIN"] = "localhost"
    for plane in ("NODE", "OPERATOR", "ARTIFACT"):
        environment[f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT"] = str(certificates / "server.crt")
        environment[f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY"] = str(certificates / "server.key")
        environment[f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA"] = str(certificates / "ca.crt")
        if plane != "NODE":
            environment[f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP"] = str(
                directory / (plane.lower() + "-authorization.json")
            )
    with sqlite3.connect(f"file:{controller_database}?mode=ro", uri=True) as connection:
        checkpoint = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
    registry = json.loads(checkpoint["integration_json"])["control"]["node_identity_registry"]
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
        {
            "schema": registry["schema"],
            "registry_id": registry["registry_id"],
            "records": {},
        }
    )
    node_tables = (
        "execution_authority_v7",
        "prepared_artifacts",
        "local_dispatch_authorizations",
        "memory_import_authority_v7",
        "node_journal_storage_schema",
    )
    before_node = logical_digest(node_database, node_tables)
    before_controller = logical_digest(
        controller_database, ("events", "controller_checkpoint", "control_command_journal")
    )
    probe_root = directory / ("upgrade-probe-" + uuid.uuid4().hex)
    probe_root.mkdir(mode=0o700)
    legacy = probe_root / "node-v08-downgrade-probe.toml"
    lines = []
    in_output = False
    for line in config.read_text().splitlines():
        if line.startswith("["):
            in_output = line == "[[artifacts.output_bindings]]"
        if in_output and line.startswith(("producer_provider_id =", "payload_schema =")):
            continue
        lines.append(
            line.replace(
                'schema = "roboguide.node-config/v0.9"', 'schema = "roboguide.node-config/v0.8"'
            )
        )
    with legacy.open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    receipts = []

    def rejected(command: list[str], marker: str | tuple[str, ...], name: str) -> None:
        """Require a real storage/schema failure, preserving exact process outputs for review."""
        process = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, timeout=15
        )
        (probe_root / (name + ".stdout")).write_bytes(process.stdout)
        (probe_root / (name + ".stderr")).write_bytes(process.stderr)
        assert process.returncode != 0, "archived binary did not reject new authority"
        markers = (marker,) if isinstance(marker, str) else marker
        observed = next(
            (value for value in markers if value in process.stderr.decode(errors="replace")), None
        )
        assert observed, process.stderr.decode(errors="replace")
        receipts.append(
            {"name": name, "returncode": process.returncode, "observed_fence": observed}
        )

    with sqlite3.connect(f"file:{node_database}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert (
            connection.execute("SELECT version FROM node_journal_storage_schema").fetchone()[0] == 8
        )
    rejected(
        [str(old_node), str(legacy)],
        "journal was written by a newer storage authority",
        "old-node-v7",
    )
    with sqlite3.connect(node_database) as connection:
        connection.execute("PRAGMA user_version=7")
    try:
        rejected(
            [str(old_node), str(legacy)],
            "journal storage marker is unsupported",
            "old-node-v7-header-reset",
        )
    finally:
        with sqlite3.connect(node_database) as connection:
            connection.execute("PRAGMA user_version=8")
    assert logical_digest(node_database, node_tables) == before_node
    old_schema = (
        subprocess.check_output([str(old_controller), "--application-schema"], timeout=5)
        .decode()
        .strip()
    )
    assert old_schema in {
        "roboguide.controller-checkpoint/v32",
        "roboguide.controller-checkpoint/v33",
    }
    grpc, operator, artifact = (address() for _ in range(3))
    rejected(
        [
            str(old_controller),
            grpc,
            str(controller_database),
            operator,
            artifact,
            str(directory / "cas"),
        ],
        (
            "unsupported checkpoint schema " + checkpoint["schema"],
            "unsupported event payload schema domain.EventPayload.json/v24",
        ),
        "old-controller-" + old_schema.rsplit("/", 1)[1],
    )
    assert (
        logical_digest(
            controller_database, ("events", "controller_checkpoint", "control_command_journal")
        )
        == before_controller
    )
    current_controller = ROOT / "target/debug/integration-server"
    assert (
        hashlib.sha256(current_controller.read_bytes()).hexdigest()
        == admitted["binaries"]["integration-server"]
    )
    fresh = probe_root / "zero-event"
    fresh.mkdir(mode=0o700)
    grpc, operator, artifact = (address() for _ in range(3))
    current_arguments = [
        str(current_controller),
        grpc,
        str(fresh / "controller.sqlite3"),
        operator,
        artifact,
        str(fresh / "cas"),
    ]
    with (fresh / "current-controller.log").open("xb") as log:
        process = subprocess.Popen(
            current_arguments, cwd=ROOT, env=environment, stdout=log, stderr=log
        )
    admin = client(certificates, "admin")
    try:
        wait_for(
            lambda: request("https://" + operator, admin, "/healthz")[0] == 200,
            "fresh current metadata profile",
        )
        status, receipt = request(
            "https://" + operator,
            admin,
            "/v1/memory-authorizations",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": 0,
                "registry": {
                    "schema_version": "roboguide.memory-authorization/v0.2",
                    "registry_id": "zero-event-" + uuid.uuid4().hex,
                    "revision": 1,
                    "policies": {},
                },
            },
        )
        assert status == 200, receipt
        assert request("https://" + operator, admin, "/v1/inventory")[1]["nodes"] == []
    finally:
        stop(process)
    fresh_database = fresh / "controller.sqlite3"
    with sqlite3.connect(f"file:{fresh_database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        fresh_checkpoint = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
        assert fresh_checkpoint["schema"] == checkpoint["schema"]
    before_fresh = logical_digest(
        fresh_database, ("events", "controller_checkpoint", "control_command_journal")
    )
    legacy_arguments = [str(old_controller), *current_arguments[1:]]
    rejected(
        legacy_arguments,
        "unsupported checkpoint schema " + checkpoint["schema"],
        "old-controller-" + old_schema.rsplit("/", 1)[1] + "-zero-events",
    )
    assert (
        logical_digest(
            fresh_database, ("events", "controller_checkpoint", "control_command_journal")
        )
        == before_fresh
    )
    result = {
        "passed": True,
        "new_nodes_registered": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "journal_authority_unchanged": True,
        "controller_authority_unchanged": True,
        "zero_event_wrapper_fence": True,
        "zero_event_inventory": 0,
        "probe_root": str(probe_root),
        "node_journal_version": 8,
        "controller_schema": checkpoint["schema"],
        "receipts": receipts,
        "archived_binary_sha256": {
            "node": hashlib.sha256(old_node.read_bytes()).hexdigest(),
            "controller": hashlib.sha256(old_controller.read_bytes()).hexdigest(),
        },
        "original_process_receipt": str(directory / "result.json"),
    }
    with output.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    """Probe archived binaries after the owned authenticated process run has stopped."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--old-node", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.directory, args.old_node, args.old_controller, args.output)))


if __name__ == "__main__":
    main()
