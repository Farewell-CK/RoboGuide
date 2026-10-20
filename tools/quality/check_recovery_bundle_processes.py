"""Restore real completed Controller/Node/Memory/CAS stores through the shipped recovery CLI."""

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

from check_artifact_authority_processes import call
from check_identity_processes import client
from check_identity_processes import request as tls_request
from check_state_verifier_processes import ROOT, address, save, stop, wait_for
from memory_preparation_profile import node_environment
from memory_use_profile import request as local_request


def logical_rows(path: Path) -> dict[str, Any]:
    """Independently hash every original SQL table/row, including budgets and audit receipts."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        result: dict[str, Any] = {"schema": schema}
        for kind, name, _, _ in schema:
            if kind == "table":
                quoted = '"' + name.replace('"', '""') + '"'
                rows = sorted(repr(row) for row in connection.execute(f"SELECT * FROM {quoted}"))
                result[name] = {
                    "rows": len(rows),
                    "digest": hashlib.sha256(
                        json.dumps(rows, separators=(",", ":")).encode()
                    ).hexdigest(),
                }
        return result


def run(source: Path, output: Path, old_node: Path) -> dict[str, Any]:
    """Reuse actual finished software work, without creating a Node inventory or device facts."""
    source = source.resolve(strict=True)
    original_result = json.loads((source / "result.json").read_bytes())
    cleanup = json.loads((source / "owned-process-exits.json").read_bytes())
    assert original_result["passed"] and cleanup["all_owned_processes_reaped"]
    assert (
        original_result["devices"]
        == original_result["models"]
        == original_result["simulators"]
        == 0
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    profile: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.1",
        "profile_id": "original-file-process-stores",
        "revision": 1,
        "max_bytes": 1024 * 1024 * 1024,
        "max_files": 10000,
        "budget_seconds": 120,
        "components": [
            {
                "component_id": "controller",
                "kind": "controller",
                "root": str(source),
                "database": "controller.sqlite3",
                "trees": [],
                "depends_on": ["cas"],
            },
            {
                "component_id": "cas",
                "kind": "artifact",
                "root": str(source / "cas"),
                "database": None,
                "trees": [],
                "depends_on": [],
            },
        ],
    }
    for name in ("source", "consumer"):
        node_root = source / name / "node-state"
        profile["components"].extend(
            [
                {
                    "component_id": name + "-node",
                    "kind": "node",
                    "root": str(node_root),
                    "database": "execution-journal.sqlite3",
                    "trees": sorted(path.name for path in node_root.iterdir() if path.is_dir()),
                    "depends_on": [name + "-backend"],
                },
                {
                    "component_id": name + "-backend",
                    "kind": "memory",
                    "root": str(source / name / "backend"),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                },
            ]
        )
    save(output / "profile.json", profile)
    databases = [source / "controller.sqlite3"]
    for name in ("source", "consumer"):
        databases.append(source / name / "node-state/execution-journal.sqlite3")
        databases.extend(sorted((source / name / "backend").glob("*.sqlite3")))
    before = {str(path.relative_to(source)): logical_rows(path) for path in databases}
    save(output / "original-sql-evidence.json", before)
    code_paths = sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py"))
    code_paths += [
        Path(__file__),
        ROOT / "target/debug/integration-server",
        ROOT / "target/debug/roboguide-node",
        old_node,
    ]
    identity = {
        str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in code_paths
    }
    save(output / "source-identity.json", identity)

    def command(*arguments: str) -> dict[str, Any]:
        """Invoke the shipped CLI in a separate process and retain the exact original output."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=130,
            check=True,
        )
        result = json.loads(completed.stdout)
        assert isinstance(result, dict)
        return result

    created = command(
        "backup",
        "--profile",
        str(output / "profile.json"),
        "--destination",
        str(output / "bundle"),
        "--command-id",
        "actual-backup",
    )
    save(output / "backup-receipt.json", created)
    assert (
        command(
            "verify",
            "--bundle",
            str(output / "bundle"),
            "--manifest-digest",
            created["manifest_digest"],
        )["verified"]
        is True
    )
    receipt = command(
        "restore",
        "--bundle",
        str(output / "bundle"),
        "--manifest-digest",
        created["manifest_digest"],
        "--destination",
        str(output / "restored"),
        "--command-id",
        "actual-restore",
    )
    save(output / "restore-receipt.json", receipt)
    assert (
        command(
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "actual-backup",
        )
        == created
    )
    assert (
        command(
            "restore",
            "--bundle",
            str(output / "bundle"),
            "--manifest-digest",
            created["manifest_digest"],
            "--destination",
            str(output / "restored"),
            "--command-id",
            "actual-restore",
        )
        == receipt
    )
    restored = output / "restored/stores"
    manifest = json.loads((output / "bundle/manifest.json").read_bytes())
    component_roots = {
        value["component_id"]: Path(value["root"]) for value in profile["components"]
    }
    verified_tables = 0
    for row in manifest["files"]:
        parts = Path(row["path"]).parts
        target = output / "restored" / row["path"]
        original = component_roots[parts[1]].joinpath(*parts[2:])
        if row["kind"] == "sqlite":
            assert logical_rows(target) == logical_rows(original)
            verified_tables += len(row["sqlite"]["tables"])
        else:
            assert (
                hashlib.sha256(target.read_bytes()).digest()
                == hashlib.sha256(original.read_bytes()).digest()
            )
    processes: list[subprocess.Popen[bytes]] = []
    logs = []
    try:
        # The actual pre-change v12 binary must refuse v13 before recovering or sending work.
        original_config = (source / "consumer/node.toml").read_text()
        configured = original_config.replace(
            str(source / "consumer/node-state"), str(restored / "consumer-node")
        )
        config = output / "restored-node.toml"
        config.write_text(configured)
        journal = restored / "consumer-node/execution-journal.sqlite3"
        prior = logical_rows(journal)
        original_node_environment = node_environment(environment, source, "enrolment-next")
        rejected = subprocess.run(
            [str(old_node), str(config)],
            cwd=ROOT,
            env=original_node_environment,
            capture_output=True,
            timeout=12,
        )
        (output / "old-node.stderr").write_bytes(rejected.stderr)
        assert rejected.returncode != 0 and b"newer storage authority" in rejected.stderr
        assert logical_rows(journal) == prior

        # A real new Node owner retains the journal; no fake file lock grants this result.
        node_log = (output / "node-owner.log").open("wb")
        logs.append(node_log)
        owner = subprocess.Popen(
            [str(ROOT / "target/debug/roboguide-node"), str(config)],
            cwd=ROOT,
            env=original_node_environment,
            stdout=node_log,
            stderr=node_log,
        )
        processes.append(owner)
        wait_for(
            lambda: (
                (journal.with_name(journal.name + ".writer.lock")).exists()
                and owner.poll() is None
                and node_log.tell() > 0
            ),
            "actual Node process lease",
        )
        # Target the actual restored owner, keeping the original source stores untouched.
        active_profile = json.loads((output / "profile.json").read_bytes())
        for value in active_profile["components"]:
            value["root"] = str(restored / value["component_id"])
        # New lock paths are created only by actual services; narrow the profile to the owned Node.
        active_profile["components"] = [
            next(
                value
                for value in active_profile["components"]
                if value["component_id"] == "consumer-node"
            )
        ]
        active_profile["components"][0]["depends_on"] = []
        save(output / "active-profile.json", active_profile)
        active = subprocess.run(
            [
                sys.executable,
                "-m",
                "roboguide_recovery",
                "backup",
                "--profile",
                str(output / "active-profile.json"),
                "--destination",
                str(output / "blocked-bundle"),
                "--command-id",
                "actual-active-owner",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=15,
        )
        (output / "active-writer.stderr").write_bytes(active.stderr)
        assert active.returncode != 0 and b"writer is still active" in active.stderr
        assert not (output / "blocked-bundle").exists()
        stop(owner)

        with closing(sqlite3.connect(restored / "controller/controller.sqlite3")) as connection:
            wrapper = json.loads(
                connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                    0
                ]
            )
        inner = json.loads(wrapper["integration_json"])
        environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
            inner["control"]["node_identity_registry"]
        )
        for plane in ("NODE", "OPERATOR", "ARTIFACT"):
            environment.update(
                {
                    f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(source / "pki/server.crt"),
                    f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(source / "pki/server.key"),
                    f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(source / "pki/ca.crt"),
                }
            )
        for plane in ("OPERATOR", "ARTIFACT"):
            environment[f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP"] = str(
                source / (plane.lower() + "-authorization.json")
            )
        grpc, operator, artifact_address = address(), address(), address()
        controller_log = (output / "controller-restored.log").open("wb")
        logs.append(controller_log)
        controller = subprocess.Popen(
            [
                str(ROOT / "target/debug/integration-server"),
                grpc,
                str(restored / "controller/controller.sqlite3"),
                operator,
                artifact_address,
                str(restored / "cas"),
            ],
            cwd=ROOT,
            env=environment,
            stdout=controller_log,
            stderr=controller_log,
        )
        processes.append(controller)
        context = client(source / "pki", "admin")
        origin = "https://" + operator
        wait_for(
            lambda: tls_request(origin, context, "/healthz")[0] == 200,
            "Controller reads the actual restored authority",
        )
        plan = json.loads((source / "mission.json").read_bytes())
        mission_id = plan["mission"]["id"]
        status, mission = tls_request(origin, context, f"/v1/missions/{mission_id}")
        assert status == 200 and mission["status"] == "Completed", mission
        attempts = tls_request(origin, context, "/v1/execution-attempts")[1]["attempts"]
        assert len(attempts) == 2 and all(value["status"] == "Completed" for value in attempts)
        preparations = tls_request(origin, context, "/v1/memory/preparations")[1]["records"]
        originals = json.loads((source / "preparations.json").read_bytes())
        assert {key: row["deadline_ms"] for key, row in preparations.items()} == {
            key: row["deadline_ms"] for key, row in originals.items()
        }
        memory = original_result["source_manifest"]
        status, catalog = call(
            "https://" + artifact_address,
            client(source / "pki", "viewer"),
            "GET",
            f"/v1/memories/{memory['selector']['memory_id']}/revisions/{memory['selector']['revision_id']}",
        )
        assert status == 200, catalog
        status, actual_bytes = call(
            "https://" + artifact_address,
            client(source / "pki", "viewer"),
            "GET",
            "/v1/artifacts/" + memory["artifact"]["content_digest"],
        )
        assert (
            status == 200
            and "sha256:" + hashlib.sha256(actual_bytes).hexdigest()
            == memory["artifact"]["content_digest"]
        )
        save(output / "restored-mission.json", mission)
        save(output / "restored-attempts.json", attempts)
        save(output / "restored-catalog.json", json.loads(catalog))
        backend_command = json.loads((source / "deployment.json").read_bytes())["backend_commands"][
            1
        ]
        backend_command[backend_command.index("--root") + 1] = str(restored / "consumer-backend")
        backend_command[backend_command.index("--input-root") + 1] = str(restored / "consumer-node")
        backend_environment = dict(environment)
        backend_environment["PYTHONPATH"] = str(ROOT / "integrations/memory-provider")
        backend_log = (output / "backend-restored.log").open("wb")
        logs.append(backend_log)
        backend = subprocess.Popen(
            backend_command,
            cwd=ROOT,
            env=backend_environment,
            stdout=backend_log,
            stderr=backend_log,
        )
        processes.append(backend)
        local_origin = "unix://" + str(restored / "consumer-backend/local.sock")
        wait_for(
            lambda: local_request(local_origin, "/v1/instance")[0] == 200,
            "actual restored original backend",
        )
        with closing(sqlite3.connect(restored / "consumer-backend/memory.sqlite3")) as connection:
            original = json.loads(
                connection.execute("SELECT original_json FROM imports").fetchone()[0]
            )
        status, availability = local_request(
            local_origin, "/v1/availability", {"memory_import": original}, method="GET"
        )
        assert status == 200 and availability["availability"]["available"] is False
        tombstone_command = json.loads((source / "reclamation-command.json").read_bytes())
        status, reclamation = local_request(
            local_origin,
            "/v1/content/reclamation",
            {"command_id": tombstone_command["command_id"]},
            method="GET",
        )
        assert status == 200 and reclamation["state"] == "deleted"
        save(output / "restored-provider-availability.json", availability)
        save(output / "restored-provider-reclamation.json", reclamation)
        result = {
            "passed": True,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "source_software_nodes": original_result["software_nodes"],
            "restored_components": len(profile["components"]),
            "verified_sql_tables": verified_tables,
            "manifest_digest": created["manifest_digest"],
            "actual_old_node_rejected": True,
            "actual_active_writer_refused": True,
            "restored_completed_attempts": len(attempts),
            "original_deadlines_preserved": True,
            "restored_cas_http_digest": memory["artifact"]["content_digest"],
            "original_provider_tombstone_preserved": True,
            "restored_imported_history_is_current_availability": False,
            "source_identity": identity,
            "runtime": {
                "python_version": sys.version,
                "python_executable": str(Path(sys.executable).resolve()),
                "python_binary_digest": "sha256:"
                + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                "sqlite_version": sqlite3.sqlite_version,
            },
            "physical_continuity": "not-established",
        }
        assert {str(path.relative_to(source)): logical_rows(path) for path in databases} == before
        assert {
            str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in code_paths
        } == identity
        save(output / "result.json", result)
        return result
    except BaseException as error:
        save(output / "failure.json", {"error": repr(error), "source_identity": identity})
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for handle in logs:
            handle.close()
        save(
            output / "owned-process-exits.json",
            {
                "all_owned_processes_reaped": all(
                    process.poll() is not None for process in processes
                ),
                "pids": [p.pid for p in processes],
            },
        )


def main() -> None:
    """Run only the explicit owned software recovery check against an already completed profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--old-node", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.old_node), sort_keys=True))


if __name__ == "__main__":
    main()
