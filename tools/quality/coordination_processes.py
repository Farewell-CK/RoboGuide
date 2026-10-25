"""Real mTLS/SQL/CPU composition for execution coordination with zero devices or external models."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from check_identity_processes import client, pki
from check_identity_processes import request as https
from check_plan_revision_processes import checkpoint
from check_state_verifier_processes import ROOT, address, plan, save, stop, wait_for
from cohort_process_profile import CohortBackend, cohort_configuration
from memory_preparation_profile import node_environment


class Case:
    """Own one explicitly declared software deployment and its original native process handles."""

    def __init__(self, root: Path, nodes: int, members: int) -> None:
        """Freeze real input identities and retain bounded original process ownership."""
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.binary = ROOT / "target/debug/integration-server"
        self.node_binary = ROOT / "target/debug/roboguide-node"
        self.certificates = root / "pki"
        self.leaves = pki(self.certificates)
        self.contexts = {
            name: client(self.certificates, name) for name in ("admin", "viewer", "enrolment-first")
        }
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        }
        self.authorization = root / "operator.json"
        save(
            self.authorization,
            {
                "schema_version": "roboguide.operator-authorization/v0.2",
                "registry_id": uuid.uuid4().hex,
                "revision": 1,
                "grants": {
                    "sha256:" + self.leaves[name]: {
                        "principal_id": principal,
                        "role": role,
                        "tenant_ids": tenants,
                    }
                    for name, principal, role, tenants in [
                        ("admin", "cohort-administrator", "recovery-admin", ["*"]),
                        ("viewer", "cohort-proposer", "operator", ["local"]),
                        ("enrolment-first", "independent-goal-producer", "operator", ["local"]),
                    ]
                },
            },
        )
        self.environment.update(
            {
                "TOKIO_WORKER_THREADS": "1",
                "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(self.authorization),
                "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
                "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(
                    {
                        "schema": "roboguide.node-identity-registry/v0.1",
                        "registry_id": uuid.uuid4().hex,
                        "records": {},
                    }
                ),
                **{
                    f"ROBOGUIDE_{plane}_MTLS_{key}": str(self.certificates / name)
                    for plane in ("NODE", "OPERATOR")
                    for key, name in [
                        ("SERVER_CERT", "server.crt"),
                        ("SERVER_KEY", "server.key"),
                        ("CLIENT_CA", "ca.crt"),
                    ]
                },
            }
        )
        self.grpc, self.http, self.artifact = (address() for _ in range(3))
        self.origin = "https://" + self.http
        self.arguments = [
            str(self.binary),
            self.grpc,
            str(root / "controller.sqlite3"),
            self.http,
            self.artifact,
            str(root / "artifacts"),
        ]
        self.processes: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
        self.logs: list[BinaryIO] = []
        self.commands: list[dict[str, Any]] = []
        self.nodes = ["cpu-" + uuid.uuid4().hex[:12] for _ in range(nodes)]
        self.node_roots: list[Path] = []
        self.local = root / "local-eaios"
        (self.local / "inputs").mkdir(parents=True)
        for source in [
            ROOT / "core/domain/src/mission_goal_verification.rs",
            ROOT / "core/domain/src/execution_coordination.rs",
        ]:
            (self.local / "inputs" / source.name).write_bytes(source.read_bytes())
        self.backend = CohortBackend(self.local, members)
        self.thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.thread.start()
        self.closed = False

    def start(
        self, arguments: list[str], name: str, environment: dict[str, str] | None = None
    ) -> subprocess.Popen[bytes]:
        """Retain the original process handle and exact native result."""
        stream = (self.root / (name + ".log")).open("xb")
        self.logs.append(stream)
        child = subprocess.Popen(
            arguments,
            cwd=ROOT,
            env=self.environment if environment is None else environment,
            stdout=stream,
            stderr=stream,
        )
        self.processes.append((child, {"name": name, "pid": child.pid, "arguments": arguments}))
        return child

    def exchange(
        self, path: str, body: dict[str, Any] | None = None, who: str = "admin"
    ) -> dict[str, Any]:
        """Send once over actual mTLS; a lost/error response is not retried as another effect."""
        status, value = https(self.origin, self.contexts[who], path, body)
        if not 200 <= status < 300:
            raise ValueError(f"actual {path}: HTTP {status}: {value}")
        return value

    def boot(self) -> None:
        """Enroll actual software Nodes with distinct actual TLS certificates."""
        self.controller = self.start(self.arguments, "controller")
        wait_for(lambda: self.exchange("/healthz"), "actual cohort Controller")
        for index, node in enumerate(self.nodes):
            certificate = ["enrolment-next", "reenrolment"][index]
            self.exchange(
                "/v1/node-identities/commands",
                {
                    "command_id": uuid.uuid4().hex,
                    "node_id": node,
                    "expected_revision": 0,
                    "action": {
                        "kind": "register",
                        "tenant_id": "local",
                        "domain_id": "owned-cpu-software",
                        "digest": self.leaves[certificate],
                        "expires_at_ms": 0,
                    },
                },
            )
            node_root = self.root / ("node-" + str(index))
            node_root.mkdir()
            self.node_roots.append(node_root / "node-state")
            configuration = node_root / "node.toml"
            configuration.write_text(
                cohort_configuration(node_root, node, self.grpc, self.backend.origin).replace(
                    'server_endpoint = "http://', 'server_endpoint = "https://'
                )
            )
            self.start(
                [str(self.node_binary), str(configuration)],
                "node-" + str(index),
                node_environment(self.environment, self.root, certificate),
            )
        wait_for(
            lambda: len(self.exchange("/v1/inventory")["nodes"]) == len(self.nodes),
            "actual registered CPU Node services",
        )

    def view(self, mission: str) -> dict[str, Any]:
        """Read original revision authority without renewing permission or budgets."""
        return self.exchange(f"/v1/missions/{mission}/plan-revisions")

    def command(
        self, mission: str, kind: str, values: dict[str, Any], who: str = "admin"
    ) -> dict[str, Any]:
        """Retain the actual versioned input and original authenticated result."""
        authority = self.view(mission)["authority"]
        body = {
            "schema_version": "roboguide.runtime-plan-revision/v0.6"
            if kind == "authorize-coordination"
            else "roboguide.runtime-plan-revision/v0.1",
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0 if authority is None else authority["revision"],
            "action": {"kind": kind, **values},
        }
        result = self.exchange(f"/v1/missions/{mission}/plan-revisions", body, who)
        self.commands.append({"mission": mission, "who": who, "input": body, "result": result})
        save(self.root / "commands.json", self.commands)
        return result

    def policy(self, mission: str) -> None:
        """Freeze an independent real-file goal source before any candidate, stop or replacement."""
        source: dict[str, Any] = {
            "source_id": "original-cpu-files",
            "revision": 1,
            "tenant_id": "local",
            "producer_principals": ["independent-goal-producer"],
            "verifier": {
                "namespace": "observation",
                "name": "verify-original-files",
                "version": "v1",
            },
            "predicates": ["all original file digests independently match"],
            "max_age_ms": 10000,
            "enabled": True,
            "continuous": False,
            "stop_timeout_ms": 30000,
            "require_artifact": False,
        }
        if not getattr(self, "source_installed", False):
            self.exchange(
                "/v1/verifier-sources",
                {
                    "command_id": uuid.uuid4().hex,
                    "expected_revision": 0,
                    "registry": {
                        "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                        "registry_id": uuid.uuid4().hex,
                        "revision": 1,
                        "sources": {source["source_id"]: source},
                    },
                },
            )
            self.source_installed = True
        self.command(
            mission,
            "install-policy",
            {
                "policy": {
                    "goal_verifier": {
                        "verifier": source["verifier"],
                        "predicate": source["predicates"][0],
                        "max_evidence_age_ms": 10000,
                    },
                    "goal_context": {"files": self.backend.original_inputs},
                    "policy_id": uuid.uuid4().hex,
                    "proposer": "cohort-proposer",
                    "lifetime_ms": 600000,
                    "candidate_limit": 8,
                    "candidate_timeout_ms": 120000,
                    "candidate_bytes": 131072,
                    "switch_limit": 4,
                    "switch_timeout_ms": 60000,
                    "automatic_triggers": [],
                }
            },
        )

    def authorize(
        self, mission: str, kind: str, ticket: str, beneficiary: str | None = None
    ) -> dict[str, Any]:
        """Freeze complete ownership and semantic purpose through the real API."""
        return self.command(
            mission,
            "authorize-coordination",
            {
                "ticket_id": ticket,
                "authorization": {
                    "schema": "roboguide.execution-coordination/v0.1",
                    "kind": kind,
                    "beneficiary_mission_id": beneficiary,
                    "stop_assurance": "software-process",
                    "continuation_operations": [
                        {"namespace": "compute", "name": "hash-files", "version": "v1"}
                    ],
                    "reason": "retain original inputs and complete CPU cohort",
                },
            },
        )

    def reviewed(self, mission: str, ticket: str, successor: dict[str, Any]) -> None:
        """Submit as the proposer and obtain independent administrative Review."""
        output = self.command(
            mission,
            "submit-candidate",
            {
                "ticket_id": ticket,
                "plan": successor,
                "provenance": {"kind": "operator", "principal": "cohort-proposer"},
            },
            "viewer",
        )
        assert output["status"] == "awaiting_review", output
        reviewed = self.command(
            mission,
            "review-candidate",
            {
                "ticket_id": ticket,
                "plan_digest": output["plan_digest"],
                "decision": "approve",
                "reason": "Review retains original goal, completed prefix and CPU context",
            },
        )
        assert reviewed["status"] in {
            "candidate_reviewed",
            "candidate_approved",
            "review_recorded",
        }, reviewed

    def snapshot(self, name: str, mission: str) -> dict[str, Any]:
        """Retain independent SQL, HTTP, invocation and native process evidence."""
        result = {
            "checkpoint": checkpoint(self.root),
            "mission": self.exchange(f"/v1/missions/{mission}"),
            "revision": self.view(mission),
            "backend": self.backend.snapshot(),
            "attempts": self.exchange("/v1/execution-attempts"),
        }
        save(self.root / (name + ".json"), result)
        return result

    def group_jobs(self, group: str) -> list[dict[str, Any]]:
        """Read exact original backend invocations without creating another execution or result."""
        with self.backend.lock:
            return [
                copy.deepcopy(job)
                for job in self.backend.jobs.values()
                if job["invocation"]["group_id"] == group
            ]

    def verify(self, identity: str) -> None:
        """Independently recompute actual output against original immutable files."""
        output = self.backend.jobs[identity]["output"]
        child = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/quality/cohort_process_profile.py"),
                "verify",
                str(self.local),
                output,
                self.backend.jobs[identity]["invocation"]["parameters"].get("file", "*"),
                json.dumps(self.backend.original_inputs),
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=15,
        )
        record = {
            "attempt": identity,
            "exit_code": child.returncode,
            "stdout": child.stdout.decode(),
            "stderr": child.stderr.decode(),
        }
        save(
            self.root
            / (hashlib.sha256(identity.encode()).hexdigest()[:16] + ".independent-verifier.json"),
            record,
        )
        assert child.returncode == 0, record

    def finish_goal(self, mission: str) -> None:
        """Submit original-goal evidence after independent actual recomputation."""
        view = self.view(mission)
        for job in self.backend.jobs.values():
            if (
                job["invocation"]["group_id"] == view["current_group_id"]
                or job["invocation"]["task_id"] == "seed"
            ):
                self.verify(job["identity"])
        attempts = [
            row
            for row in self.exchange("/v1/execution-attempts")["attempts"]
            if row["group_id"] == view["current_group_id"]
        ]
        evidence = {
            "schema_version": "roboguide.mission-goal-evidence/v0.1",
            "evidence_id": uuid.uuid4().hex,
            "mission_id": mission,
            "tenant_id": "local",
            "contract_digest": view["goal_contract"]["digest"],
            "source_id": "original-cpu-files",
            "source_revision": 1,
            "plan_revision": view["current_plan_revision"],
            "group_id": view["current_group_id"],
            "plan_digest": view["current_plan_digest"],
            "attempts": [
                {
                    "task_ref": {"mission_id": mission, "task_id": row["task_id"]},
                    "role_id": row["role_id"],
                    "attempt_id": row["execution_id"],
                }
                for row in attempts
            ],
            "predicate": "all original file digests independently match",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "sequence": 1,
            "valid_for_ms": 5000,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }
        self.exchange("/v1/mission-goal-evidence", evidence, "enrolment-first")
        wait_for(
            lambda: self.exchange(f"/v1/missions/{mission}")["status"] == "Completed",
            "independent original cohort goal",
        )

    def close(self) -> None:
        """Drain owned processes before closing software stores and listeners."""
        if self.closed:
            return
        self.closed = True
        for process, record in reversed(self.processes):
            stop(process)
            record["native_wait"] = process.wait(timeout=5)
        self.backend.close()
        self.thread.join(timeout=5)
        save(self.root / "owned-processes.json", [record for _, record in self.processes])
        save(self.root / "final-owned-cpu-waits.json", self.backend.snapshot())
        for stream in self.logs:
            stream.close()


def semantic_plan(mission: str, coupled: bool = False) -> dict[str, Any]:
    """Declare file work with an optional coupled cohort and preserved prefix."""
    value = plan(mission)
    task = value["tasks"][0]
    task["satisfaction"] = {
        "expected_effect": "actual original immutable file digests computed",
        "basis": "execution-report",
        "verifier": None,
    }
    value["tasks"] = [task]
    if not coupled:
        return value
    value["mission"]["actors"] = [{"id": "process-a"}, {"id": "process-b"}]
    context = value["contexts"][0]
    context["roles"] = [
        {"id": "compute-a", "actor": "process-a"},
        {"id": "compute-b", "actor": "process-b"},
    ]
    context["coupling_mode"] = "concurrent-cooperation"
    context["shared_view"] = {
        "bindings": [
            {"context_role_id": "compute-a", "field": "execution"},
            {"context_role_id": "compute-b", "field": "execution"},
        ],
        "include_freshness": True,
    }
    context["relations"] = [
        {
            "id": "original-cohort-active",
            "kind": "requires-active",
            "source": {"task_id": "cohort", "role_id": "alpha"},
            "target": {"task_id": "cohort", "role_id": "beta"},
        }
    ]
    task["id"] = "seed"
    task["coupling_mode"] = "independent"
    task["roles"][0]["context_role"] = "compute-a"
    task["roles"][0]["execution_intent"]["parameters"] = {"file": "mission_goal_verification.rs"}
    cohort = copy.deepcopy(task)
    cohort["id"] = "cohort"
    cohort["depends_on"] = ["seed"]
    cohort["coupling_mode"] = "concurrent-cooperation"
    cohort["roles"] = [copy.deepcopy(task["roles"][0]) for _ in range(2)]
    for role, identity in zip(cohort["roles"], ["alpha", "beta"], strict=True):
        role["id"] = identity
        role["context_role"] = "compute-a" if identity == "alpha" else "compute-b"
        role["execution_intent"]["parameters"] = {"file": "execution_coordination.rs"}
    value["tasks"].append(cohort)
    return value


def successor(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep the actual completed prefix byte-exact while replacing only unfinished semantic work."""
    result = copy.deepcopy(plan)
    source = result["tasks"][-1]["id"]
    replacement = source + "-resumed"
    result["tasks"][-1]["id"] = replacement
    for relation in result["contexts"][0]["relations"]:
        for endpoint in ["source", "target"]:
            if relation[endpoint]["task_id"] == source:
                relation[endpoint]["task_id"] = replacement
    return result
