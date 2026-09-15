"""Verify one C1-S0 production smoke evidence directory."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_ID = "mission-c1-s0-habitat-navigation"
TASK_ID = "navigate-to-target"


def _events(path: Path) -> list[dict[str, Any]]:
    """Load the ordered Controller event response."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document["events"])


def _event_kinds(events: list[dict[str, Any]]) -> list[str]:
    """Return the externally visible variant name of each domain event."""
    return [next(iter(event["payload"])) for event in events]


def _bridge_row(database: Path) -> dict[str, Any]:
    """Read the sole local execution and decode its retained invocation/outcome."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM executions").fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise AssertionError(f"expected one local execution, found {len(rows)}")
    row = dict(rows[0])
    row["invocation"] = json.loads(row.pop("invocation_json"))
    row["local_outcome"] = json.loads(row.pop("local_outcome_json"))
    return row


def _node_journal_counts(database: Path) -> tuple[int, int, str]:
    """Return local execution rows, dispatch authorizations, and the final Node status."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        executions = connection.execute("SELECT status FROM executions").fetchall()
        authorizations = connection.execute(
            "SELECT COUNT(*) FROM local_dispatch_authorizations"
        ).fetchone()
    finally:
        connection.close()
    if authorizations is None:
        raise AssertionError("Node journal authorization count is missing")
    status = str(executions[0][0]) if len(executions) == 1 else "invalid"
    return len(executions), int(authorizations[0]), status


def verify(run: Path) -> dict[str, object]:
    """Evaluate the exact C1-S0 happy-path criteria from persisted production evidence."""
    mission = json.loads((run / "mission.json").read_text(encoding="utf-8"))
    events = _events(run / "events.json")
    kinds = _event_kinds(events)
    bridge = _bridge_row(run / "habitat-bridge.sqlite3")
    invocation = bridge["invocation"]
    outcome = bridge["local_outcome"]
    node_rows, node_authorizations, node_status = _node_journal_counts(
        run / "node-state/execution-journal.sqlite3"
    )
    server_log = (run / "integration-server.log").read_text(encoding="utf-8")
    node_log = (run / "roboguide-node.log").read_text(encoding="utf-8")
    bridge_log = (run / "habitat-bridge.log").read_text(encoding="utf-8")
    initial = outcome["initial_position"]
    final = outcome["final_position"]
    moved = any(
        abs(float(before) - float(after)) > 0.05
        for before, after in zip(initial, final, strict=True)
    )
    checks = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_completed": mission["status"] == "Completed",
        "task_completed": mission["tasks"]
        == [
            {
                "context_id": "habitat-navigation-context",
                "status": "Completed",
                "task_id": TASK_ID,
            }
        ],
        "exact_operation": invocation["operation"] == "mobility.navigate@v1",
        "objective_preserved": invocation["objective"]
        == "Navigate the Habitat robot to the configured semantic target.",
        "parameters_preserved": invocation["parameters"] == {"destination": "any_targets|0"},
        "committed_resource_preserved": invocation["resource_ids"] == ["habitat-navigation-slot"],
        "local_handle_created": str(bridge["execution_id"]).startswith("habitat-"),
        "local_completed": bridge["state"] == "COMPLETED" and outcome["state"] == "COMPLETED",
        "single_local_execution": node_rows == 1
        and node_authorizations == 1
        and node_status == "completed",
        "simulator_stepped": int(outcome["simulator_steps"]) > 0,
        "physical_position_changed": moved,
        "running_observed": "status observed for" in bridge_log and ": RUNNING" in bridge_log,
        "task_execution_completed": "TaskExecutionCompleted" in kinds,
        "task_satisfied": "TaskSatisfied" in kinds,
        "group_completed": "ExecutionGroupCompleted" in kinds,
        "no_recovery": "RuntimeExecutionRecoveryRequired" not in kinds,
        "controller_alive": "Error:" not in server_log
        and "application timer stopped" not in server_log,
        "node_connected": "session ended" not in node_log,
        "bridge_responsive": "BrokenPipeError" not in bridge_log and "Traceback" not in bridge_log,
    }
    return {
        "checks": checks,
        "local_execution": {
            "execution_id": bridge["execution_id"],
            "outcome": outcome,
        },
        "mission_id": MISSION_ID,
        "schema": "roboguide.c1-s0-habitat-smoke-verdict/v0.1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }


def main() -> None:
    """Print one machine-readable verdict for the supplied smoke directory."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-smoke.py RUN_DIRECTORY")
    verdict = verify(Path(sys.argv[1]))
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if verdict["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
