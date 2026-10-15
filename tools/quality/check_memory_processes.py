"""Exercise real file Memory, Node Protocol queries, SQL crash windows and binary downgrade gates.

This profile runs software processes and actual immutable files with zero devices. The Local
Memory backend is shipped code. Query does not repeat an import, and independent file verification
remains necessary for Mission satisfaction. No simulator, model provider or experiment runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from check_state_verifier_processes import (
    ROOT,
    ComputeHandler,
    ComputeServer,
    address,
    install_policies,
    node_config,
    plan,
    request,
    save,
    stop,
    wait_for,
)


def consume(directory: Path, output: Path) -> None:
    """Wait for authorized file input, then hash actual backend bytes in an independent child."""
    deadline = time.monotonic() + 90
    while not (directory / "consume-permit.json").is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("actual file input was not authorized within its original budget")
        time.sleep(0.02)
    permit = json.loads((directory / "consume-permit.json").read_bytes())
    source = directory / "backend/content" / permit["content_digest"][7:]
    before = source.stat()
    checksum = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(32768):
            checksum.update(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("actual backend content changed during consumption")
    save(
        output,
        {
            "schema_version": "roboguide.software-file-result/v0.1",
            "files": [
                {"name": permit["name"], "size": before.st_size, "sha256": checksum.hexdigest()}
            ],
        },
    )


class MemoryComputeHandler(ComputeHandler):
    """Use original process observations while starting an actual Memory-dependent file consumer."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract.
        """Launch an exact actual consumer; status and cancellation retain OS polling."""
        if self.path != "/execute":
            super().do_POST()
            return
        server: ComputeServer = self.server  # type: ignore[assignment]
        body = json.loads(self.rfile.read(min(int(self.headers["Content-Length"]), 65536)))
        with server.lock:
            identity, invocation = body["attempt_id"], body["invocation"]
            if identity in server.jobs:
                self.respond({"execution_id": identity})
                return
            if server.jobs:
                self.respond({"error": "bounded real consumer queue already has an attempt"}, 409)
                return
            output = server.directory / (uuid.uuid4().hex + ".result.json")
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__)),
                    "--consume",
                    str(server.directory),
                    str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            server.jobs[identity] = process, output, invocation
            server.invocations.append({"attempt_id": identity, "invocation": invocation})
            save(server.directory / "actual-invocations.json", server.invocations)
            self.respond({"execution_id": identity})


def memory_config(origin: str, artifact: str, directory: Path) -> str:
    """Declare fixed actual provider operations and the explicit typed continuity contract."""
    result = f'''
[artifacts]
endpoint = "{artifact}"
cache_directory = {json.dumps(str(directory / "cache"))}
max_artifact_bytes = 1048576
chunk_size_bytes = 32768
connect_timeout_ms = 3000
read_timeout_ms = 3000
[[connections]]
driver = "http"
id = "memory-http"
local_system = "posix-compute"
endpoint = "{origin}"
timeout_ms = 3000
[[memory_providers]]
id = "files"
owner = "posix-compute"
kind = "artifact"
scope = "global"
visibility = "exchangeable"
payload_schema = "roboguide.software-source-file/v0.1"
media_type = "application/octet-stream"
storage_directory = "memory/files"
[memory_providers.discover]
manifests_pointer = "/steps/discover/manifests"
[[memory_providers.discover.steps]]
id = "discover"
connection = "memory-http"
[memory_providers.discover.steps.operation]
kind = "http"
method = "GET"
path = "/v1/discover"
[memory_providers.export]
artifact_path_pointer = "/steps/export/artifact_path"
[[memory_providers.export.steps]]
id = "export"
connection = "memory-http"
[memory_providers.export.steps.operation]
kind = "http"
method = "GET"
path = "/v1/export"
[memory_providers.import]
[[memory_providers.import.steps]]
id = "import"
connection = "memory-http"
[memory_providers.import.steps.operation]
kind = "http"
method = "POST"
path = "/v1/import"
[memory_providers.continuity]
import_result_pointer = "/steps/import/result"
instance_pointer = "/instance_id"
result_pointer = "/result"
[memory_providers.continuity.instance]
id = "instance"
connection = "memory-http"
[memory_providers.continuity.instance.operation]
kind = "http"
method = "GET"
path = "/v1/instance"
[memory_providers.continuity.query]
id = "query"
connection = "memory-http"
[memory_providers.continuity.query.operation]
kind = "http"
method = "GET"
path = "/v1/import-query"
'''
    for section, fields in [
        ("export.steps", ["memory_manifest"]),
        ("import.steps", ["memory_manifest", "memory_import", "staged_artifact"]),
        ("continuity.query", ["memory_import"]),
    ]:
        # TOML table ownership must follow each exact step declaration, so insert before the
        # next provider section rather than append bindings to a different last array element.
        bindings = ""
        for field in fields:
            bindings += f"""
[[memory_providers.{section}.request.bindings]]
target = "/{field}"
[memory_providers.{section}.request.bindings.value]
kind = "pointer"
pointer = "/invocation/{field}"
"""
        marker = {
            "export.steps": "[memory_providers.import]",
            "import.steps": "[memory_providers.continuity]",
            "continuity.query": None,
        }[section]
        if marker is None:
            result += bindings
        else:
            result = result.replace(marker, bindings + marker)
    return result


def run(directory: Path, old_node_binary: Path) -> dict[str, Any]:
    """Run shipped Controller/Node/backend processes, actual file effects and independent proof."""
    directory.mkdir(mode=0o700)
    (directory / "inputs").mkdir()
    source = ROOT / "core/domain/src/memory.rs"
    shutil.copyfile(source, directory / "inputs" / source.name)
    payload = source.read_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    node, mission = uuid.uuid4().hex, uuid.uuid4().hex
    grpc, http, artifact, memory = address(), address(), address(), address()
    origin, cas, backend = "http://" + http, "http://" + artifact, "http://" + memory
    handoff = directory / "node-state/memory/files"
    handoff.mkdir(parents=True)
    shutil.copyfile(source, handoff / "source.bin")
    manifest: dict[str, Any] = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex},
        "kind": "artifact",
        "provider_id": "files",
        "owner": {"owner": "node", "node_id": node, "local_system_id": "posix-compute"},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": "roboguide.software-source-file/v0.1",
        "media_type": "application/octet-stream",
        "artifact": {"content_digest": digest, "byte_size": len(payload)},
        "source_mission_id": None,
        "source_task_ref": None,
        "source_execution_id": None,
        "created_at": time.time_ns() // 1_000_000,
    }
    save(directory / "export-config.json", [{"manifest": manifest, "artifact_path": "source.bin"}])
    save(directory / "admission.json", {"enabled": True})
    provider = ComputeServer(directory)
    provider.RequestHandlerClass = MemoryComputeHandler
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    config = directory / "node.toml"
    legacy_config = directory / "legacy-node.toml"
    legacy_config.write_text(node_config(directory, node, grpc, provider.origin))
    config.write_text(
        legacy_config.read_text().replace("node-config/v0.7", "node-config/v0.8")
        + memory_config(backend, cas, directory)
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN"] = "1"
    environment["PYTHONPATH"] = str(ROOT / "integrations/memory-provider")
    logs: list[BinaryIO] = []
    processes: list[subprocess.Popen[bytes]] = []

    def start(command: list[str], name: str) -> subprocess.Popen[bytes]:
        """Track only owned actual processes and retain their original stdout/stderr evidence."""
        log = (directory / (name + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log, stderr=log, start_new_session=True
        )
        processes.append(process)
        return process

    controller_command = [
        str(ROOT / "target/debug/integration-server"),
        grpc,
        str(directory / "controller.sqlite3"),
        http,
        artifact,
        str(directory / "cas"),
    ]
    try:
        start(
            [
                sys.executable,
                "-m",
                "roboguide_memory_provider",
                "--root",
                str(directory / "backend"),
                "--provider-id",
                "files",
                "--input-root",
                str(directory),
                "--export-config",
                str(directory / "export-config.json"),
                "--export-directory",
                str(handoff),
                "--listen-port",
                memory.rsplit(":", 1)[1],
            ],
            "memory-backend",
        )
        identity = wait_for(lambda: request(backend, "/v1/instance")[1], "actual Memory backend")
        with sqlite3.connect(directory / "backend/memory.sqlite3") as db:
            db.execute(
                "CREATE TRIGGER fail_final_import BEFORE UPDATE OF state ON imports "
                "BEGIN SELECT RAISE(ABORT,'conformance final import SQL failure'); END"
            )
        controller = start(controller_command, "controller")
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller startup")
        install_policies(origin, node)
        start([str(ROOT / "target/debug/roboguide-node"), str(config)], "node")
        selector = manifest["selector"]
        memory_path = f"/v1/memories/{selector['memory_id']}/revisions/{selector['revision_id']}"
        published = wait_for(
            lambda: request(cas, memory_path)[1].get("manifest"),
            "Node-authored actual file publication",
        )
        assert published == manifest
        accepted = plan(mission)
        accepted["tasks"] = accepted["tasks"][:1]
        accepted["mission"]["objective"] = "independently verify actual imported file content"
        assert request(origin, "/v1/missions", accepted)[0] == 202
        attempts = wait_for(
            lambda: [
                a
                for a in request(origin, "/v1/execution-attempts")[1]["attempts"]
                if a["status"] in {"Accepted", "Running"}
            ],
            "actual file consumer waiting for authorized input",
        )
        assert len(attempts) == 1
        attempt = attempts[0]
        process, output, _ = provider.jobs[attempt["execution_id"]]
        assert process.poll() is None
        identifier = uuid.uuid4().hex
        command = {
            "import_id": identifier,
            "group_id": attempt["group_id"],
            "task_ref": {"mission_id": mission, "task_id": attempt["task_id"]},
            "role_id": attempt["role_id"],
            "execution_id": attempt["execution_id"],
            "node_id": node,
            "consumer_provider_id": "files",
            "manifest": manifest,
            "deadline_ms": time.time_ns() // 1_000_000 + 30_000,
        }
        status, body = request(origin, "/v1/memory/imports", command)
        assert status == 202, body
        imported = wait_for(
            lambda: (
                value
                if (value := request(origin, f"/v1/memory/imports/{identifier}")[1])["phase"]
                == "unknown"
                else None
            ),
            "actual SQL failure leaves the original import Unknown",
        )
        assert process.poll() is None
        assert (directory / "backend/content" / digest[7:]).read_bytes() == payload
        with sqlite3.connect(directory / "backend/memory.sqlite3") as db:
            assert db.execute("SELECT COUNT(*),state FROM imports").fetchone() == (1, "pending")
        replacement = dict(command, import_id=uuid.uuid4().hex)
        assert request(origin, "/v1/memory/imports", replacement)[0] == 409
        query = {
            "import_id": identifier,
            "expected_generation": 0,
            "deadline_ms": time.time_ns() // 1_000_000 + 90_000,
            "query_timeout_ms": 10_000,
        }
        status, body = request(origin, "/v1/memory/import-queries", query)
        assert status == 202, body
        reconciled = wait_for(
            lambda: (
                value
                if (value := request(origin, f"/v1/memory/imports/{identifier}")[1])["phase"]
                == "completed"
                else None
            ),
            "readonly original-provider query resolves the exact still-authorized import",
        )
        assert reconciled["request"] == imported["request"]
        assert reconciled["deliveries"] == imported["deliveries"]
        receipt = reconciled["reconciliation"]["queries"]["1"]
        assert receipt["receipt"]["provider_instance_id"] == identity["instance_id"]
        assert receipt["current_authority_at_receipt"] is True
        assert process.poll() is None, "Memory query must not complete the actual consumer"
        with sqlite3.connect(directory / "backend/memory.sqlite3") as db:
            assert db.execute("SELECT COUNT(*),state FROM imports").fetchone() == (1, "pending")
        old = subprocess.run(
            [str(old_node_binary), str(legacy_config)],
            env=environment,
            cwd=ROOT,
            capture_output=True,
            timeout=8,
        )
        (directory / "old-node.stdout").write_bytes(old.stdout)
        (directory / "old-node.stderr").write_bytes(old.stderr)
        assert old.returncode != 0 and b"view" in old.stderr, old.stderr
        stop(controller)
        start(controller_command, "controller-restored")
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller restore")
        restored = request(origin, f"/v1/memory/imports/{identifier}")[1]
        assert restored == reconciled
        assert request(origin, "/v1/memory/import-queries", query)[1] == reconciled
        wait_for(
            lambda: len(request(origin, "/v1/inventory")[1]["nodes"]) == 1, "fresh Node session"
        )
        assert len(provider.invocations) == 1
        save(directory / "consume-permit.json", {"content_digest": digest, "name": source.name})
        wait_for(
            lambda: process.poll() == 0 and output.is_file(), "actual imported-file computation"
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/quality/check_state_verifier_processes.py"),
                "--verify",
                str(directory),
                str(output),
            ],
            check=True,
            timeout=10,
        )
        wait_for(
            lambda: any(
                a["status"] == "Completed"
                for a in request(origin, "/v1/execution-attempts")[1]["attempts"]
            ),
            "original terminal execution report",
        )
        raw = output.read_bytes()
        proof_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        upload = uuid.uuid4().hex
        assert request(cas, "/v1/artifact-uploads", {"upload_id": upload})[0] == 201
        assert request(cas, f"/v1/artifact-uploads/{upload}/content", raw, method="PUT")[0] == 202
        assert (
            request(
                cas,
                f"/v1/artifact-uploads/{upload}/finalize",
                {"content_digest": proof_digest, "byte_size": len(raw)},
            )[0]
            == 201
        )
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": "file-reader",
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": command["task_ref"],
            "plan_digest": request(origin, f"/v1/missions/{mission}/tenant")[1][
                "source_plan_digest"
            ],
            "attempts": [{"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}],
            "predicate": "all file digests match",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 10000,
            "sequence": 1,
            "artifact_digest": proof_digest,
            "artifact_size_bytes": len(raw),
        }
        status, evidence_receipt = request(origin, "/v1/verifier-evidence", evidence)
        assert status == 200 and evidence_receipt["receipt"]["applied"], evidence_receipt
        wait_for(
            lambda: request(origin, f"/v1/missions/{mission}")[1]["status"] == "Completed",
            "independently verified file Mission",
        )
        result = {
            "passed": True,
            "devices": 0,
            "software_nodes": 1,
            "actual_file_consumer_processes": len(provider.invocations),
            "independent_verifier_processes": 1,
            "actual_backend_import_rows": 1,
            "duplicate_imports": 0,
            "readonly_query_preserved_backend_pending_row": True,
            "old_node_exit": old.returncode,
            "old_node_sha256": hashlib.sha256(old_node_binary.read_bytes()).hexdigest(),
            "controller_sha256": hashlib.sha256(
                (ROOT / "target/debug/integration-server").read_bytes()
            ).hexdigest(),
            "node_sha256": hashlib.sha256(
                (ROOT / "target/debug/roboguide-node").read_bytes()
            ).hexdigest(),
            "configuration_digest": "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest(),
            "reconciliation": reconciled,
            "hardware_evidence": "HardwareEvidencePending",
        }
        save(directory / "result.json", result)
        return result
    finally:
        for owned in reversed(processes):
            stop(owned)
        provider.close()
        thread.join(timeout=5)
        for log in logs:
            log.close()


def main() -> None:
    """Select actual child computation or the complete zero-device Memory process profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consume", nargs=2, type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--old-node-binary", type=Path)
    args = parser.parse_args()
    if args.consume:
        consume(*args.consume)
    elif args.output_directory and args.old_node_binary:
        result = run(args.output_directory.resolve(), args.old_node_binary.resolve(strict=True))
        print(
            json.dumps({k: v for k, v in result.items() if k != "reconciliation"}, sort_keys=True)
        )
    else:
        parser.error("provide --output-directory and --old-node-binary, or --consume")


if __name__ == "__main__":
    main()
