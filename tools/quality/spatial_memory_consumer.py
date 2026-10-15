"""Consume a real published map bundle through a separate enrolled software Memory Node."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import grant
from check_spatial_upgrade_gates import logical_digest
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for
from memory_preparation_profile import configuration, node_environment

Exchange = Callable[[str, str, Any], tuple[int, Any]]
Start = Callable[[list[str], str, dict[str, str] | None], subprocess.Popen[bytes]]


def verify(output: Path) -> dict[str, Any]:
    """Independently hash the actual receiver bytes and check full raw source/attempt binding."""
    source = json.loads((output / "catalog-manifest.json").read_bytes())["manifest"]
    record = json.loads((output / "consumer/preparation.json").read_bytes())
    attempt = json.loads((output / "consumer/attempt.json").read_bytes())
    job = json.loads((output / "consumer/computation.json").read_bytes())
    assert len(record["preparation"]["inputs"]) == 1
    original = record["preparation"]["inputs"][0]
    assert original["typed_map"] == source
    assert original["manifest"]["kind"] == "spatial"
    assert original["manifest"]["provider_id"] == source["producer_provider_id"]
    assert original["manifest"]["source_execution_id"] == source["source_execution_id"]
    assert original["manifest"]["source_task_ref"] == source["source_task_ref"]
    assert record["execution_id"] == attempt["execution_id"]
    content = output / "consumer/backend/content" / source["content_digest"][7:]
    with content.open("rb") as stream:
        checksum = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    assert checksum == source["content_digest"] and content.stat().st_size == source["byte_size"]
    assert job["state"] == "COMPLETED" and job["result"]["execution_id"] == attempt["execution_id"]
    assert job["result"]["input_digests"] == [checksum] and job["result"]["rounds"] == 2
    result = {"input_digests": [checksum], "rounds": 2}
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert job["result"]["output_digest"] == expected
    return {
        "passed": True,
        "actual_content_digest": checksum,
        "actual_bytes": source["byte_size"],
        "complete_raw_map_retained": True,
        "execution_id": attempt["execution_id"],
    }


class SpatialMemoryConsumer:
    """Own only the configured actual receiver process profile; Control makes every assignment."""

    def __init__(
        self,
        output: Path,
        python: Path,
        grpc: str,
        cas: str,
        environment: dict[str, str],
        profile: dict[str, Any],
    ) -> None:
        """Freeze real receiver credentials and configuration before Controller startup."""
        self.output, self.python, self.environment = output, python, environment
        self.node = "memory-" + uuid.uuid4().hex
        self.backend = "http://" + address()
        self.root = output / "consumer"
        self.root.mkdir(mode=0o700)
        self.profile = profile
        self.config = self.root / "node.toml"
        text = configuration(self.root, self.node, grpc, self.backend, cas, True)
        text = text.replace('kind = "artifact"', 'kind = "spatial"')
        text = text.replace(
            'payload_schema = "roboguide.software-source-file/v0.1"',
            'payload_schema = "roboguide.spatial-file-map/v0.1"',
        )
        text = text.replace(
            'media_type = "application/octet-stream"',
            'media_type = "application/vnd.roboguide.spatial-map"',
        )
        text = text.replace("max_artifact_bytes = 1048576", "max_artifact_bytes = 536870912")
        self.config.write_text(text)
        certificate = output / "pki/enrolment-next.crt"
        self.leaf = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
        path = output / "artifact-authorization.json"
        registry = json.loads(path.read_bytes())
        receiver = grant("configured-spatial-consumer", "writer", "local")
        receiver.update(
            node_id=self.node,
            provider_ids=["files"],
            memory_ids=[profile["map_id"]],
            map_ids=[profile["map_id"]],
        )
        registry["grants"]["sha256:" + self.leaf] = receiver
        save(path, registry)

    def plan(self, original: dict[str, Any]) -> dict[str, Any]:
        """Append semantic map requirements; Control selects the actual receiver."""
        result = copy.deepcopy(original)
        result["schema_version"] = "roboguide.mission-plan/v0.9"
        result["mission"]["actors"].append({"id": "map-consumer"})
        result["contexts"][0]["roles"].append({"id": "consumer", "actor": "map-consumer"})
        for task in result["tasks"]:
            for role in task["roles"]:
                role["requirements"]["memories"] = []
        operation = {"namespace": "compute", "name": "hash-imported-file", "version": "v1"}
        task = {
            "id": "consume",
            "description": "hash actual remote map bytes after prepared import",
            "context_id": "files",
            "depends_on": ["publish"],
            "timing": {
                "earliest_start_offset_ms": 0,
                "latest_start_offset_ms": None,
                "completion_deadline_offset_ms": None,
            },
            "satisfaction": copy.deepcopy(result["tasks"][-1]["satisfaction"]),
            "roles": [
                {
                    "id": "reader",
                    "context_role": "consumer",
                    "requirements": {
                        "capabilities": [{"contract": operation, "constraints": []}],
                        "resources": [{"kind": "compute", "units": 1}],
                        "memories": [
                            {
                                "selector": {
                                    "memory_id": self.profile["map_id"],
                                    "revision_id": self.profile["revision_id"],
                                },
                                "kind": "spatial",
                                "payload_schema": "roboguide.spatial-file-map/v0.1",
                                "media_type": "application/vnd.roboguide.spatial-map",
                            }
                        ],
                    },
                    "execution_intent": {
                        "operation": operation,
                        "objective": "hash imported immutable map bundle",
                        "parameters": {"rounds": 2},
                    },
                    "resource_scope": "task",
                }
            ],
        }
        result["tasks"].append(task)
        return result

    def start(self, origin: str, exchange: Exchange, start: Start) -> None:
        """Enroll the receiver and start its actual backend and formal Node Service."""
        status, response = exchange(
            origin,
            "/v1/node-identities/commands",
            {
                "command_id": uuid.uuid4().hex,
                "node_id": self.node,
                "expected_revision": 0,
                "action": {
                    "kind": "register",
                    "tenant_id": "local",
                    "domain_id": "software-file-domain",
                    "digest": self.leaf,
                    "expires_at_ms": 0,
                },
            },
        )
        assert status == 200, response
        environment = dict(self.environment)
        environment["PYTHONPATH"] = (
            str(ROOT / "integrations/memory-provider")
            + os.pathsep
            + environment.get("PYTHONPATH", "")
        )
        self.backend_environment = environment
        self.backend_command = [
            str(self.python),
            "-m",
            "roboguide_memory_provider",
            "--root",
            str(self.root / "backend"),
            "--provider-id",
            "files",
            "--input-root",
            str(self.root),
            "--max-bytes",
            "536870912",
            "--enable-file-jobs",
            "--listen-port",
            self.backend.rsplit(":", 1)[1],
        ]
        self.backend_process = start(
            self.backend_command,
            "consumer-backend",
            environment,
        )
        wait_for(
            lambda: request(self.backend, "/v1/file-jobs/health")[1].get("ready") is True,
            "actual map consumer backend",
        )
        self.node_process = start(
            [str(ROOT / "target/debug/roboguide-node"), str(self.config)],
            "consumer-node",
            node_environment(environment, self.output, "enrolment-next"),
        )

    def restart(self, start: Start) -> dict[str, Any]:
        """Reopen receiver processes without changing original imports, budgets or jobs."""
        health = request(self.backend, "/v1/file-jobs/health")[1]
        assert health["safe"] is True and health["active_executions"] == []
        stop(self.node_process)
        stop(self.backend_process)
        config = tomllib.loads(self.config.read_text())
        node_database = Path(config["state_directory"]) / "execution-journal.sqlite3"
        databases = (
            (self.root / "backend/memory.sqlite3", ("identity", "imports")),
            (self.root / "backend/file-jobs.sqlite3", ("jobs",)),
            (node_database, ("memory_preparations", "memory_import_authority_v7")),
        )
        before = {str(path): logical_digest(path, tables) for path, tables in databases}
        self.backend_process = start(
            self.backend_command, "consumer-backend-restored", self.backend_environment
        )
        wait_for(
            lambda: request(self.backend, "/v1/file-jobs/health")[1].get("ready") is True,
            "original receiver backend restored",
        )
        self.node_process = start(
            [str(ROOT / "target/debug/roboguide-node"), str(self.config)],
            "consumer-node-restored",
            node_environment(self.backend_environment, self.output, "enrolment-next"),
        )
        after = {str(path): logical_digest(path, tables) for path, tables in databases}
        assert before == after
        assert request(self.backend, "/v1/file-jobs/health")[1] == health
        result = {"passed": True, "original_authority_digests": before}
        save(self.root / "restart.json", result)
        return result

    def authorize(self, origin: str, exchange: Exchange, group: str) -> None:
        """Grant current Group read/import while retaining the immutable source digest."""
        status, response = exchange(origin, "/v1/memory-authorizations", None)
        assert status == 200, response
        registry = response["registry"]
        previous = registry["revision"]
        registry["revision"] += 1
        key = self.profile["map_id"] + "/" + self.profile["revision_id"]
        registry["policies"][key]["groups"] = [
            {
                "group_id": group,
                "operations": ["read", "import"],
                "providers": [
                    {
                        "provider_id": None,
                        "local_system_id": None,
                        "kind": "spatial",
                        "payload_schema": "roboguide.spatial-file-map/v0.1",
                        "media_type": "application/vnd.roboguide.spatial-map",
                    }
                ],
            }
        ]
        status, response = exchange(
            origin,
            "/v1/memory-authorizations",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": previous,
                "registry": registry,
            },
        )
        assert status == 200, response
        save(self.root / "content-authorization.json", registry)

    def finish(
        self, origin: str, exchange: Exchange, mission: str, verifier: str
    ) -> dict[str, Any]:
        """Require actual computation and independent verification before completion."""
        attempts = wait_for(
            lambda: [
                item
                for item in exchange(origin, "/v1/execution-attempts", None)[1]["attempts"]
                if item["task_id"] == "consume" and item["status"] == "Completed"
            ],
            "actual prepared map consumption",
            35,
        )
        assert len(attempts) == 1 and attempts[0]["node_id"] == self.node
        attempt = attempts[0]
        save(self.root / "attempt.json", attempt)
        _, job = request(
            self.backend,
            "/v1/file-jobs/status",
            {"execution_id": attempt["execution_id"]},
            method="GET",
        )
        save(self.root / "computation.json", job)
        records = exchange(origin, "/v1/memory/preparations", None)[1]["records"]
        record = records[attempt["execution_id"]]
        save(self.root / "preparation.json", record)
        checked = subprocess.run(
            [sys.executable, str(Path(__file__)), "--verify", str(self.output)],
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=10,
        )
        verified = json.loads(checked.stdout)
        assert verified["passed"] is True
        save(self.root / "independent-verifier.json", verified)
        map_manifest = json.loads((self.output / "catalog-manifest.json").read_bytes())["manifest"]
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": verifier,
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": mission, "task_id": "consume"},
            "plan_digest": exchange(origin, f"/v1/missions/{mission}/tenant", None)[1][
                "source_plan_digest"
            ],
            "attempts": [{"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}],
            "predicate": "source and CAS bundle integrity",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 30000,
            "sequence": 1,
            "artifact_digest": map_manifest["content_digest"],
            "artifact_size_bytes": map_manifest["byte_size"],
        }
        status, response = exchange(origin, "/v1/verifier-evidence", evidence)
        assert status == 200 and response["receipt"]["applied"] is True, response
        return {
            "passed": True,
            "actual_software_receiver": self.node,
            "independent": verified,
            "verifier_receipt": response,
            "configuration_digest": "sha256:"
            + hashlib.sha256(self.config.read_bytes()).hexdigest(),
            "physical_localization": "HardwareEvidencePending",
        }


def main() -> None:
    """Run only independent byte verification over a supplied owned process profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.verify), sort_keys=True))


if __name__ == "__main__":
    main()
