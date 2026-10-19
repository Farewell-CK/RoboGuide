"""Verify independent Task release with two actual software Nodes, CAS files and CPU workers."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import ssl
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, stop, wait_for
from memory_preparation_profile import mission
from memory_use_profile import request


@dataclass
class GroupScenario:
    """Retain only actual enrolled processes and immutable source bytes from the owning checker."""

    directory: Path
    nodes: list[str]
    backends: list[str]
    manifest: dict[str, Any]
    exchange: Callable[..., tuple[int, dict[str, Any]]]
    verifier_context: ssl.SSLContext
    controller: subprocess.Popen[bytes]
    controller_command: list[str]
    start: Callable[..., subprocess.Popen[bytes]]

    def get(self, path: str) -> dict[str, Any]:
        """Read committed state without treating transport receipt as a successful write."""
        status, value = self.exchange(path)
        assert status == 200, value
        return value


def verify(directory: Path, index: int) -> dict[str, Any]:
    """Independently hash actual source/import files and reconstruct the CPU output commitment."""
    evidence = json.loads((directory / f"group-computation-{index}.json").read_bytes())
    original = directory / "source/node-state/memory/files/source.bin"
    imported = directory / evidence["content_path"]
    digests = []
    for path in (original, imported):
        with path.open("rb") as stream:
            digests.append("sha256:" + hashlib.file_digest(stream, "sha256").hexdigest())
    assert digests[0] == digests[1] == evidence["source_digest"]
    result = evidence["computation"]["result"]
    assert result["input_digests"] == [digests[0]]
    assert result["rounds"] == evidence["rounds"]
    encoded = json.dumps(
        {"input_digests": [digests[0]], "rounds": evidence["rounds"]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert result["output_digest"] == "sha256:" + hashlib.sha256(encoded).hexdigest()
    assert result["execution_id"] == evidence["attempt"]["execution_id"]
    return {"passed": True, "source_digest": digests[0], "execution_id": result["execution_id"]}


def run(scenario: GroupScenario) -> dict[str, Any]:
    """Run real parallel file computations and require one Task to release while its peer runs."""
    identifier = uuid.uuid4().hex
    accepted = mission(identifier, scenario.manifest)
    template = copy.deepcopy(accepted["tasks"][1]["roles"][0])
    for index, task in enumerate(accepted["tasks"]):
        context_role = task["roles"][0]["context_role"]
        task["roles"][0] = copy.deepcopy(template)
        task["roles"][0]["context_role"] = context_role
        task["roles"][0]["execution_intent"]["parameters"]["rounds"] = 1 if index == 0 else 50000
        task["depends_on"] = []
    save(scenario.directory / "group-mission.json", accepted)
    status, response = scenario.exchange("/v1/missions", accepted)
    assert status == 202, response
    path = f"/v1/missions/{identifier}"
    waiting = wait_for(
        lambda: (
            records
            if len(records := scenario.get("/v1/memory/preparations")["records"]) == 2
            and all(row["phase"] == "waiting_source" for row in records.values())
            else None
        ),
        "two actual receivers wait for their Group content authorization",
    )
    for name in ("source", "consumer"):
        database = scenario.directory / name / "backend/file-jobs.sqlite3"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    save(scenario.directory / "group-before-authorization.json", waiting)
    group = scenario.get(path)["group_id"]
    registry = scenario.get("/v1/memory-authorizations")["registry"]
    previous = registry["revision"]
    registry["revision"] += 1
    for policy in registry["policies"].values():
        policy["groups"] = [
            {
                "group_id": group,
                "operations": ["read", "import"],
                "providers": [
                    {
                        "provider_id": None,
                        "local_system_id": None,
                        "kind": "artifact",
                        "payload_schema": scenario.manifest["payload_schema"],
                        "media_type": scenario.manifest["media_type"],
                    }
                ],
            }
        ]
    status, response = scenario.exchange(
        "/v1/memory-authorizations",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": previous,
            "registry": registry,
        },
    )
    assert status == 200, response
    verifier_receipts = []
    partial = None
    for index, task in enumerate(accepted["tasks"]):
        task_id = task["id"]

        def completed(task_id: str = task_id) -> dict[str, Any] | None:
            """Retain actual failure or ambiguity immediately, without relabeling the outcome."""
            rows = [
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["task_id"] == task_id
            ]
            assert all(row["status"] not in {"Unknown", "Cancelled", "Failed"} for row in rows), (
                rows
            )
            return next((row for row in rows if row["status"] == "Completed"), None)

        attempt = wait_for(completed, "original CPU computation finishes", 240)
        receiver = scenario.nodes.index(attempt["node_id"])
        status, computation = request(
            scenario.backends[receiver],
            "/v1/file-jobs/status",
            {"execution_id": attempt["execution_id"]},
            method="GET",
        )
        assert status == 200 and computation["state"] == "COMPLETED", computation
        content = (
            Path("source" if receiver == 0 else "consumer")
            / "backend/content"
            / scenario.manifest["artifact"]["content_digest"][7:]
        )
        save(
            scenario.directory / f"group-computation-{index}.json",
            {
                "attempt": attempt,
                "computation": computation,
                "content_path": str(content),
                "rounds": task["roles"][0]["execution_intent"]["parameters"]["rounds"],
                "source_digest": scenario.manifest["artifact"]["content_digest"],
            },
        )
        checked = subprocess.run(
            [sys.executable, str(Path(__file__)), str(scenario.directory), str(index)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        independent = json.loads(checked.stdout)
        assert independent["passed"] is True
        save(scenario.directory / f"group-independent-verifier-{index}.json", independent)
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": "independent-file-reader",
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": identifier, "task_id": task_id},
            "plan_digest": scenario.get(path + "/tenant")["source_plan_digest"],
            "attempts": [{"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}],
            "predicate": "all file digests match",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 10000,
            "sequence": 1,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }
        status, response = scenario.exchange(
            "/v1/verifier-evidence", evidence, context=scenario.verifier_context
        )
        assert status == 200, response
        verifier_receipts.append(response)
        if index == 0:

            def task_released(task_id: str = task_id) -> dict[str, Any] | None:
                """Observe the first Task's committed release while its peer keeps running."""
                view = scenario.get(path)
                return (
                    view
                    if any(
                        row["task_id"] == task_id and row["status"] == "Completed"
                        for row in view["tasks"]
                    )
                    else None
                )

            partial = wait_for(
                task_released,
                "first satisfied Task releases while the other Node is still computing",
            )
            peers = [
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["task_id"] != task_id
            ]
            assert len(peers) == 1 and peers[0]["status"] in {"Accepted", "Running"}, peers
            assert peers[0]["node_id"] != attempt["node_id"]
            peer_use = scenario.get("/v1/memory/use-leases")["records"][peers[0]["execution_id"]]
            assert peer_use["revoked"] is False
            save(
                scenario.directory / "group-partial-progress.json",
                {"mission": partial, "peer": peers[0], "peer_use": peer_use},
            )
    final = wait_for(
        lambda: view if (view := scenario.get(path))["status"] == "Completed" else None,
        "both independently verified Tasks complete their Mission",
    )
    contexts = scenario.get("/v1/memory/preparations")["records"]
    uses = scenario.get("/v1/memory/use-leases")["records"]
    assert len(contexts) == len(uses) == 2
    for execution, record in contexts.items():
        assert record["phase"] == "revoked", record
        original_providers = {
            item["consumer"]["provider_id"] for item in record["preparation"]["inputs"]
        }
        ack = next(
            value["receipt"]
            for value in uses[execution]["controls"].values()
            if value["command"]["operation"] == "revoke"
        )
        assert ack["status"] == "revoked"
        assert {item["provider_id"] for item in ack["providers"]} == original_providers
    save(
        scenario.directory / "group-final.json",
        {"mission": final, "preparations": contexts, "uses": uses},
    )
    original = {
        execution: (row["admitted_at_ms"], row["deadline_ms"])
        for execution, row in contexts.items()
    }
    stop(scenario.controller)
    scenario.start(scenario.controller_command, "controller-group-restored")
    wait_for(
        lambda: scenario.get(path)["status"] == "Completed",
        "completed Group restores without another CPU job",
    )
    restored = scenario.get("/v1/memory/preparations")["records"]
    assert original == {
        execution: (row["admitted_at_ms"], row["deadline_ms"])
        for execution, row in restored.items()
    }
    for receiver, name in enumerate(("source", "consumer")):
        with sqlite3.connect(
            f"file:{scenario.directory / name / 'backend/file-jobs.sqlite3'}?mode=ro", uri=True
        ) as connection:
            assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert (
            request(scenario.backends[receiver], "/v1/file-jobs/health")[1]["active_executions"]
            == []
        )
    return {
        "passed": True,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "software_nodes": 2,
        "actual_hash_jobs": 2,
        "independent_verifiers": 2,
        "task_release_while_peer_running": partial is not None,
        "all_original_provider_revoke_acks": True,
        "context_revoke_acks": 2,
        "controller_restarts": 1,
        "budgets_unchanged": True,
        "verifier_receipts": verifier_receipts,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]), int(sys.argv[2])), sort_keys=True))
