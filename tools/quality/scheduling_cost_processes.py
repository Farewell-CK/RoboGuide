"""Compose actual host observations, Node State costs, durable audit and restart."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, wait_for

Exchange = Callable[..., tuple[int, dict[str, Any]]]
SCHEMA = "roboguide.scheduling-metrics/v0.1"


def configuration(text: str, node: str) -> str:
    """Append a declared real provider observation; it grants no new operation or resource."""
    return (
        text
        + f"""
[[state_exports]]
id = "scheduling-cost"
owner = "posix-compute"
object_class = "node"
object_type = "software-scheduling-cost"
object_id = "{node}"
semantic = "observed"
payload_schema = "{SCHEMA}"
valid_for_ms = 2000
interval_ms = 250
value_pointer = ""
[state_exports.step]
id = "observe-scheduling-cost"
connection = "compute-http"
[state_exports.step.operation]
kind = "http"
method = "GET"
path = "/v1/scheduling-cost"
"""
    )


class SchedulingCostCheck:
    """Retain original exact cost policies and compare durable actual evidence across restart."""

    def __init__(self, output: Path, nodes: list[str], exchange: Exchange) -> None:
        """Configure only caller-owned existing software processes; no inventory is manufactured."""
        self.output = output
        self.nodes = nodes
        self.exchange = exchange
        self.registry: dict[str, Any] = {
            "schema_version": "roboguide.decision-policy-registry/v0.2",
            "registry_id": "actual-source-costs-" + uuid.uuid4().hex,
            "revision": 1,
            "gates": {},
            "scheduling_costs": {},
        }
        for node in nodes:
            policy_id = "host-load-" + node
            self.registry["scheduling_costs"][policy_id] = {
                "tenant_id": "local",
                "enabled": True,
                "node_id": node,
                "policy": {
                    "schema": "roboguide.decision-evidence-policy/v0.1",
                    "policy_id": policy_id,
                    "revision": 1,
                    "object": {
                        "class": "node",
                        "object_type": "software-scheduling-cost",
                        "object_id": node,
                    },
                    "channel_id": "scheduling-cost",
                    "allowed_semantics": ["observed"],
                    "allowed_sources": [
                        {"owner": "node", "node_id": node, "local_system_id": "posix-compute"}
                    ],
                    "max_age_ms": 2000,
                    "min_confidence_millionths": None,
                    "allowed_decisions": ["schedule"],
                    "reducer": "require_equivalent",
                    "payload_schema": SCHEMA,
                },
                "terms": [
                    {
                        "metric": "load_millionths",
                        "weight": 1,
                        "units_per_cost": 1000,
                        "maximum": 1_024_000_000,
                        "prefer_higher": False,
                    }
                ],
            }
        command = {
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0,
            "registry": self.registry,
        }
        status, receipt = exchange("/v1/state-policies", command)
        assert status == 200, receipt
        assert exchange("/v1/state-policies", command) == (status, receipt)
        save(output / "original-cost-command.json", command)
        save(output / "original-cost-receipt.json", receipt)

    def observe(self, mission: str, *, available: bool) -> dict[str, Any]:
        """Check actual current-session availability without promoting null GPU or route values."""
        path = f"/v1/missions/{mission}/scheduling-costs"

        def current() -> dict[str, Any] | None:
            """Read the actual committed view until the existing bounded lifecycle settles."""
            status, view = self.exchange(path)
            if status != 200 or len(view.get("assessments", [])) != len(self.nodes):
                return None
            scores = [row["score"] for row in view["assessments"]]
            if (
                all(score is not None for score in scores)
                if available
                else all(score is None for score in scores)
            ):
                return view
            return None

        view: dict[str, Any] = wait_for(current, "actual cost source session availability")
        assert view["planning_only"] is True
        for row in view["assessments"]:
            if available:
                record = row["record"]
                assert record["source_epoch"]
                metrics = record["value"]
                assert metrics["schema_version"] == SCHEMA
                assert type(metrics["load_millionths"]) is int
                assert all(
                    metrics[key] is None
                    for key in (
                        "queue_delay_ms",
                        "travel_distance_um",
                        "traffic_delay_ms",
                        "gpu_memory_bytes",
                    )
                )
                assert row["score"] == metrics["load_millionths"] // 1000
        save(
            self.output / ("cost-available.json" if available else "cost-after-route-loss.json"),
            view,
        )
        return view

    def durable(self, database: Path) -> dict[str, Any]:
        """Independently read original source audits and retained policy history from SQL."""
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT sequence,payload_json FROM events ORDER BY sequence"
            ).fetchall()
            saved = connection.execute(
                "SELECT checkpoint_json FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
        audits = [
            (sequence, json.loads(payload)) for sequence, payload in rows if '"schedule"' in payload
        ]
        assert len(audits) >= 2, "actual Task selections did not retain cost source evidence"
        assert saved is not None
        checkpoint = json.loads(saved[0])
        assert self.registry["registry_id"] in json.dumps(checkpoint)
        assert all(
            policy_id in json.dumps(checkpoint) for policy_id in self.registry["scheduling_costs"]
        )
        save(self.output / "cost-original-audit.json", audits)
        status, policy = self.exchange("/v1/state-policies")
        assert status == 200 and policy["registry"] == self.registry
        return {
            "actual_audit_rows": len(audits),
            "original_policy_survives_restart": True,
            "unsupported_gpu_queue_travel_traffic": "unavailable",
            "devices": 0,
        }


def refuse_old(
    binary: Path,
    database: Path,
    cas: Path,
    environment: dict[str, str],
    output: Path,
) -> dict[str, Any]:
    """Require the actual older process to refuse retained v0.2 before serving."""
    import hashlib
    import subprocess

    from check_state_verifier_processes import ROOT, address, stop

    before = hashlib.sha256(binary.read_bytes()).hexdigest()
    command = [str(binary.resolve()), address(), str(database), address(), address(), str(cas)]
    with (output / "cost-old-controller.log").open("wb") as stream:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=stream, stderr=stream, start_new_session=True
        )
        try:
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                stop(process)
    reason = (output / "cost-old-controller.log").read_text()
    interpretation = (
        "schedule"
        if "unknown variant `schedule`" in reason
        else "scheduling_costs"
        if "unknown field `scheduling_costs`" in reason
        else None
    )
    assert process.returncode != 0 and interpretation is not None, reason[-2048:]
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == before
    receipt = {
        "passed": True,
        "binary_sha256": before,
        "actual_pid": process.pid,
        "actual_exit_code": process.returncode,
        "refused_interpretation": interpretation,
    }
    save(output / "cost-old-controller-receipt.json", receipt)
    return receipt
