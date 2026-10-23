"""Observe real file-worker interruption under current Memory permission without device fixtures."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, wait_for
from memory_use_profile import request

Exchange = Callable[[str, dict[str, Any] | None], tuple[int, dict[str, Any]]]
Start = Callable[[list[str], str], subprocess.Popen[bytes]]


@dataclass
class ActualUseScenario:
    """Carry only already-owned actual processes and their frozen deployment inputs."""

    directory: Path
    backend: str
    controller: subprocess.Popen[bytes]
    controller_command: list[str]
    backend_process: subprocess.Popen[bytes]
    backend_command: list[str]
    exchange: Exchange
    start: Start

    def get(self, path: str) -> dict[str, Any]:
        """Read the actual authenticated Controller and reject failed observations."""
        status, value = self.exchange(path, None)
        assert status == 200, value
        return value


def rows(database: Path, query: str) -> list[tuple[Any, ...]]:
    """Inspect only the owned actual SQLite file through a readonly connection."""
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection, connection:
        return connection.execute(query).fetchall()


def run(scenario: ActualUseScenario, case: str) -> dict[str, Any]:
    """Interrupt granted actual computation and preserve original budgets and ownership."""
    assert case in {"revoke", "expire", "backend-restart", "controller-restart"}
    directory = scenario.directory

    def running_use() -> dict[str, Any] | None:
        """Require a committed actual grant and an independently observed running backend job."""
        values = scenario.get("/v1/memory/use-leases")["records"]
        preparations = scenario.get("/v1/memory/preparations")["records"]
        if any(
            value["phase"] in {"revoking", "revoked", "unknown"} for value in preparations.values()
        ):
            raise RuntimeError("actual preparation failed before use interruption admission")
        if len(values) != 1:
            return None
        value = next(iter(values.values()))
        positive = [
            delivery
            for delivery in value["controls"].values()
            if delivery["receipt"] and delivery["receipt"]["status"] == "granted"
        ]
        if not positive or value["revoked"]:
            return None
        observed = request(
            scenario.backend,
            "/v1/file-jobs/status",
            {"execution_id": value["execution_id"]},
            method="GET",
        )[1]
        return value if observed["state"] == "RUNNING" else None

    original = wait_for(running_use, "actual granted and running Memory consumer", 150)
    execution = original["execution_id"]
    preparation = scenario.get("/v1/memory/preparations")["records"][execution]
    save(directory / "lifecycle-original-use.json", original)
    save(directory / "lifecycle-original-preparation.json", preparation)
    node_database = directory / "consumer/node-state/execution-journal.sqlite3"
    jobs_database = directory / "consumer/backend/file-jobs.sqlite3"
    lease_database = directory / "consumer/backend/memory-use.sqlite3"
    original_context = rows(
        node_database,
        "SELECT preparation_json,budget_json,configuration_digest FROM memory_preparations",
    )
    original_budget = rows(jobs_database, "SELECT * FROM job_budgets")
    original_invocation = rows(jobs_database, "SELECT invocation_json FROM jobs")[0][0]
    original_receipts = rows(lease_database, "SELECT * FROM commands ORDER BY command_id")
    assert len(original_budget) == 1 and original_context

    if case == "revoke":
        registry = scenario.get("/v1/memory-authorizations")["registry"]
        previous = registry["revision"]
        registry["revision"] += 1
        for policy in registry["policies"].values():
            for group in policy["groups"]:
                group["operations"] = [value for value in group["operations"] if value != "read"]
        status, receipt = scenario.exchange(
            "/v1/memory-authorizations",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": previous,
                "registry": registry,
            },
        )
        assert status == 200, receipt
        save(directory / "lifecycle-revocation.json", receipt)
    elif case == "backend-restart":
        scenario.backend_process.kill()
        scenario.backend_process.wait(timeout=10)
        scenario.start(scenario.backend_command, "consumer-backend-use-restored")
        wait_for(
            lambda: request(scenario.backend, "/v1/file-jobs/health")[1].get("ready"),
            "original actual Memory backend reopened",
        )
    else:
        scenario.controller.kill()
        scenario.controller.wait(timeout=10)
        if case == "controller-restart":
            scenario.start(scenario.controller_command, "controller-use-restored")
            wait_for(lambda: scenario.get("/healthz"), "actual Controller use restore")

    def stopped_job() -> dict[str, Any] | None:
        """Observe the actual worker outcome and closed active-computation set."""
        status, value = request(
            scenario.backend, "/v1/file-jobs/status", {"execution_id": execution}, method="GET"
        )
        assert status == 200, value
        if value["state"] == "COMPLETED":
            raise AssertionError("actual computation completed before the requested interruption")
        if value["state"] not in {"CANCELLED", "UNKNOWN"}:
            return None
        health = request(scenario.backend, "/v1/file-jobs/health")[1]
        return value if health["safe"] is True and health["active_executions"] == [] else None

    outcome = wait_for(stopped_job, "actual computation exit after use ended", 50)
    assert outcome["result"] is None
    if case == "expire":
        assert outcome["reason"] == "current Memory-use permission ended", outcome
        assert rows(lease_database, "SELECT generation FROM leases") == [(original["generation"],)]
    else:
        wait_for(
            lambda: scenario.get("/v1/memory/use-leases")["records"][execution]["revoked"],
            "committed Controller current-use fence",
            35,
        )
        wait_for(
            lambda: any(
                value["receipt"] and value["receipt"]["status"] == "revoked"
                for value in scenario.get("/v1/memory/use-leases")["records"][execution][
                    "controls"
                ].values()
            ),
            "actual all-provider use-revocation acknowledgement",
            35,
        )
        assert rows(lease_database, "SELECT revoked FROM leases") == [(1,)]
        assert scenario.get("/v1/recovery")["state"] != "Ready"
    assert (
        rows(
            node_database,
            "SELECT preparation_json,budget_json,configuration_digest FROM memory_preparations",
        )
        == original_context
    )
    assert rows(jobs_database, "SELECT * FROM job_budgets") == original_budget
    retained_receipts = rows(lease_database, "SELECT * FROM commands ORDER BY command_id")
    assert all(value in retained_receipts for value in original_receipts)
    before = rows(jobs_database, "SELECT * FROM jobs")
    status, replay = request(
        scenario.backend, "/v1/file-jobs/compute", json.loads(original_invocation)
    )
    assert status == 200 and replay == outcome
    assert rows(jobs_database, "SELECT * FROM jobs") == before and len(before) == 1
    result = {
        "passed": True,
        "case": case,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "software_nodes": 2,
        "execution_id": execution,
        "actual_worker_outcome": outcome,
        "original_compute_budget_unchanged": True,
        "original_preparation_budget_unchanged": True,
        "original_provider_receipts_unchanged": True,
        "replayed_job_started_another_worker": False,
        "semantic_satisfaction_invented": False,
        "physical_stop": "HardwareEvidencePending",
    }
    save(directory / "lifecycle-result.json", result)
    return result
