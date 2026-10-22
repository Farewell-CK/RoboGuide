"""Check the shipped import/Replica/revoke/GC lifecycle with actual SQL and zero Nodes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from check_memory_reference_processes import (
    PROVIDER,
    ROOT,
    UnixConnection,
    _import_fixture,
    request,
    save,
    start,
    stop,
)
from roboguide_memory_provider.replica_references import ReplicaReferences
from roboguide_memory_provider.store import canonical, original_content_digest
from roboguide_memory_provider.use_leases import kernel_clock


def use_command(imported: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """Build an explicitly local protocol fixture, never a registered Node or Control grant."""
    original = imported["original"]
    invocation = original["original_invocation"]
    identifiers = {
        "preparation_id": uuid.uuid4().hex,
        "node_id": "unregistered-protocol-fixture",
        "source_session_id": uuid.uuid4().hex,
        **{
            key: invocation[key]
            for key in ("execution_id", "group_id", "mission_id", "task_id", "role_id")
        },
    }
    invocation.update(
        memory_preparation_id=identifiers["preparation_id"],
        session_id=identifiers["source_session_id"],
    )
    original.update(
        identity_digest=hashlib.sha256(canonical(invocation)).hexdigest(),
        original_content_digest=original_content_digest(invocation),
    )
    preparation = {
        "schema": "roboguide.memory-preparation/v0.2",
        **identifiers,
        "tenant_id": "local-protocol-fixture",
        "registry_id": uuid.uuid4().hex,
        "inputs": [
            {
                "manifest": imported["manifest"],
                "typed_map": None,
                "consumer": {"provider_id": identity["provider_id"]},
                "import_id": original["import_id"],
            }
        ],
        "invocation": {
            key: invocation[key]
            for key in ("execution_id", "group_id", "mission_id", "task_id", "role_id")
        },
    }
    lease_id = "use-" + hashlib.sha256(identifiers["preparation_id"].encode()).hexdigest()
    encoded = canonical(preparation).decode()
    control = {
        key: preparation[key]
        for key in (
            "preparation_id",
            "tenant_id",
            "group_id",
            "execution_id",
            "node_id",
            "source_session_id",
            "registry_id",
        )
    } | {
        "schema": "roboguide.memory-use-control/v0.1",
        "command_id": f"memory-use-grant-{lease_id}-1",
        "lease_id": lease_id,
        "generation": 1,
        "operation": "grant",
        "preparation_digest": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
        "configuration_digest": "sha256:" + hashlib.sha256(canonical(identity)).hexdigest(),
        "registry_revision": 1,
        "membership": {
            "generation": 1,
            "bindings_digest": "sha256:" + hashlib.sha256(canonical(identifiers)).hexdigest(),
        },
        "valid_for_ms": 30_000,
    }
    boot, namespace, lower, _ = kernel_clock()
    return {
        "schema": "roboguide.memory-use-provider-command/v0.1",
        "control": control,
        "preparation_json": encoded,
        "imports": [original],
        "receiver_budget": {
            "boot_id": boot,
            "time_namespace": namespace,
            "received_lower_ms": lower,
            "deadline_lower_ms": lower + 30_000,
        },
    }


def run(output: Path, runtime: Path) -> dict[str, Any]:
    """Retain actual processes, requests, independent SQL, hashes and fault outcomes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    root, inputs = runtime / "provider", runtime / "inputs"
    if root.exists() or inputs.exists():
        raise ValueError("runtime directory must not contain previous provider data")
    inputs.mkdir(mode=0o700)
    source = inputs / "source.bin"
    source.write_bytes(Path(__file__).read_bytes())
    channel = root / "local.sock"
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    trace: list[dict[str, Any]] = []
    save(
        output / "source-identity.json",
        {
            str(path.relative_to(ROOT)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                ROOT / "tools/quality/check_memory_reference_processes.py",
                *sorted((PROVIDER / "roboguide_memory_provider").glob("*.py")),
            )
        },
    )

    def launch() -> subprocess.Popen[bytes]:
        """Start only the actual owned reference provider and retain its real PID/log."""
        log = (output / f"provider-{len(logs)}.stderr").open("wb")
        logs.append(log)
        process = start(root, inputs, log)
        processes.append(process)
        trace.append({"operation": "spawn", "pid": process.pid})
        return process

    def call(route: str, body: dict[str, Any], method: str = "POST") -> tuple[int, dict[str, Any]]:
        """Record each actual local protocol request and its original response."""
        status, receipt = request(channel, route, body, method)
        trace.append(
            {
                "route": route,
                "method": method,
                "request": body,
                "status": status,
                "response": receipt,
            }
        )
        save(output / "trace.json", trace)
        return status, receipt

    try:
        first = launch()
        status, identity = call("/v1/instance", {}, "GET")
        assert status == 200
        imported = _import_fixture(source, identity["provider_id"], identity["instance_id"])
        imported["manifest"]["scope"] = {"kind": "global"}
        imported["original"]["original_invocation"]["manifest_json"] = canonical(
            imported["manifest"]
        ).decode()
        grant = use_command(imported, identity)
        payload = {
            "memory_import": imported["original"],
            "memory_manifest": imported["manifest"],
            "staged_artifact": str(source),
        }
        with closing(sqlite3.connect(root / "memory.sqlite3")) as observer:
            observer.execute(
                "CREATE TRIGGER fail_replica BEFORE INSERT ON content_references "
                "WHEN NEW.kind='replica' BEGIN SELECT RAISE(ABORT,'replica fault'); END"
            )
            observer.commit()
        status, _ = call("/v1/import", payload)
        assert status == 503
        with closing(sqlite3.connect(root / "memory.sqlite3")) as observer:
            assert observer.execute("SELECT COUNT(*) FROM imports").fetchone() == (0,)
            assert observer.execute("SELECT COUNT(*) FROM content_references").fetchone() == (0,)
            observer.execute("DROP TRIGGER fail_replica")
            observer.commit()
        assert not tuple((root / "content").iterdir())
        lost_response = UnixConnection(channel)
        try:
            lost_response.request(
                "POST",
                "/v1/import",
                body=canonical(payload),
                headers={"Content-Type": "application/json"},
            )
            # The actual peer closes without reading the response. Completion is established
            # only by later readonly original-provider observation, never another POST.
        finally:
            lost_response.close()
        trace.append(
            {
                "route": "/v1/import",
                "method": "POST",
                "request": payload,
                "response_observed": False,
            }
        )
        deadline = time.monotonic() + 10
        while True:
            status, completed = call(
                "/v1/import-query", {"memory_import": imported["original"]}, "GET"
            )
            if status == 200 and completed["result"]["outcome"] == "completed":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("lost import response did not reconcile through original reads")
            time.sleep(0.02)
        source.unlink()
        query = {
            "schema": "roboguide.content-reference-query/v0.1",
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            "reference_id": "replica:" + ReplicaReferences.owner(imported["original"]),
        }
        status, acquired = call("/v1/content/references", query, "GET")
        assert status == 200 and acquired["state"] == "active"
        assert call("/v1/memory-use/control", grant)[0] == 200
        reclaim = {
            "schema": "roboguide.content-reclamation-command/v0.1",
            "command_id": uuid.uuid4().hex,
            "provider_id": identity["provider_id"],
            "provider_instance_id": identity["instance_id"],
            **imported["artifact"],
        }
        assert call("/v1/content/tombstone", reclaim)[1]["state"] == "blocked"
        assert stop(first, channel) == 0
        second = launch()
        assert call("/v1/content/references", query, "GET") == (200, acquired)
        assert call("/v1/content/tombstone", reclaim)[1]["state"] == "blocked"
        revoked = copy.deepcopy(grant)
        revoked["control"].update(
            operation="revoke",
            generation=0,
            valid_for_ms=0,
            command_id=f"memory-use-revoke-{grant['control']['lease_id']}-0",
        )
        revoked.update(preparation_json="", imports=[], receiver_budget=None)
        assert call("/v1/memory-use/control", revoked)[1]["status"] == "revoked"
        assert call("/v1/content/tombstone", reclaim)[1]["state"] == "tombstoned"
        deleted = call("/v1/content/collect", reclaim)
        assert deleted[0] == 200 and deleted[1]["state"] == "deleted"
        assert not (root / "content" / imported["artifact"]["content_digest"][7:]).exists()
        assert stop(second, channel) == 0
        third = launch()
        assert call("/v1/content/references", query, "GET")[1]["state"] == "released"
        assert call("/v1/import", payload)[0] == 409
        assert call("/v1/content/collect", reclaim) == deleted
        assert stop(third, channel) == 0
        with closing(sqlite3.connect(root / "memory.sqlite3")) as observer:
            rows = observer.execute(
                "SELECT reference_id,content_digest,kind,owner,generation,active "
                "FROM content_references ORDER BY reference_id"
            ).fetchall()
            assert [row[4:] for row in rows if row[2] == "replica"] == [(1, 0)]
            assert observer.execute("SELECT COUNT(*) FROM retired_imports").fetchone() == (1,)
        shutil.copytree(root, output / "final-provider")
        result = {
            "passed": True,
            "runtime": str(runtime),
            "provider": identity,
            "sql_rows": rows,
            "actual_processes": [process.pid for process in processes],
            "sql_abort_preceded_file_effect": True,
            "restart_retained_original_pin": True,
            "original_revoke_preceded_tombstone": True,
            "actual_local_content_unlinked": True,
            "released_pin_not_reactivated": True,
            "lost_import_response_reconciled_readonly": True,
            "registered_nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
            "protocol_fixture": True,
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                stop(process, channel)
        for log in logs:
            log.close()
        save(output / "trace.json", trace)


def main() -> None:
    """Use a separate short socket directory while retaining evidence at the declared path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    arguments = parser.parse_args()
    runtime = arguments.runtime_root or Path(tempfile.mkdtemp(prefix="rg-replica-"))
    started = time.monotonic()
    result = run(arguments.output.resolve(), runtime.resolve())
    print(json.dumps(result | {"elapsed_seconds": time.monotonic() - started}, sort_keys=True))


if __name__ == "__main__":
    main()
