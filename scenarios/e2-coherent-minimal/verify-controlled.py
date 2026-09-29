"""Verify the E2-S0 RoboGuide-to-COHERENT controlled smoke."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_ID = "mission-e2-s0-coherent-merom"
TASK_ID = "execute-merom-trio-plan"
REQUIRED_AGENTS = {"aliengo_0", "quadrotor_0", "franka_0"}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} is not a JSON object")
    return value


def _events(path: Path) -> list[dict[str, Any]]:
    document = _read(path)
    return list(document["events"])


def _bridge_row(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM executions").fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise AssertionError(f"expected one bridge execution, found {len(rows)}")
    row = dict(rows[0])
    row["invocation"] = json.loads(row.pop("invocation_json"))
    raw_outcome = row.pop("local_outcome_json")
    row["local_outcome"] = json.loads(raw_outcome) if raw_outcome else None
    return row


def _node_counts(database: Path) -> tuple[int, int, str]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        executions = connection.execute("SELECT status FROM executions").fetchall()
        authorizations = connection.execute(
            "SELECT COUNT(*) FROM local_dispatch_authorizations"
        ).fetchone()
    finally:
        connection.close()
    if authorizations is None:
        raise AssertionError("Node authorization count is missing")
    status = str(executions[0][0]) if len(executions) == 1 else "invalid"
    return len(executions), int(authorizations[0]), status


def verify(run: Path) -> dict[str, object]:
    """Evaluate Controller, Node, bridge, physical execution, and goal evidence."""
    mission = _read(run / "mission.json")
    events = _events(run / "events.json")
    kinds = [next(iter(event["payload"])) for event in events]
    bridge = _bridge_row(run / "coherent-bridge.sqlite3")
    outcome = bridge["local_outcome"] or {}
    summary = outcome.get("summary") or {}
    goal = outcome.get("goal_check") or {}
    invocation = bridge["invocation"]
    node_rows, authorizations, node_status = _node_counts(
        run / "node-state/execution-journal.sqlite3"
    )
    executed_agents = set(goal.get("executed_agents") or [])
    checks = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_completed": mission.get("status") == "Completed",
        "task_completed": mission.get("tasks")
        == [
            {
                "context_id": "coherent-merom-context",
                "status": "Completed",
                "task_id": TASK_ID,
            }
        ],
        "exact_operation": invocation.get("operation") == "coherent.execute-plan@v1",
        "task_parameter_preserved": invocation.get("parameters") == {"task": "Merom_1_int_Task1"},
        "resource_preserved": invocation.get("resource_ids") == ["coherent-simulator-slot"],
        "bridge_completed": bridge.get("state") == "COMPLETED",
        "coherent_process_ok": outcome.get("process_returncode") == 0,
        "coherent_summary_success": summary.get("success") is True,
        "physical_goal_passed": goal.get("passed") is True,
        "all_three_agents_executed": REQUIRED_AGENTS.issubset(executed_agents),
        "all_text_actions_executed": summary.get("text_actions_issued") == 9,
        "all_physical_skills_finished": summary.get("sim_actions_finished") == 21,
        "no_failed_physical_skill": summary.get("sim_actions_failed") == [],
        "no_manual_correction": summary.get("manual_correction") is False,
        "single_node_dispatch": node_rows == 1
        and authorizations == 1
        and node_status == "completed",
        "task_execution_completed": "TaskExecutionCompleted" in kinds,
        "task_satisfied": "TaskSatisfied" in kinds,
        "task_bindings_released": "TaskExecutionBindingsReleased" in kinds,
        "context_bindings_released": "ContextBindingsReleased" in kinds,
        "no_recovery": "RuntimeExecutionRecoveryRequired" not in kinds,
    }
    result = {
        "schema": "roboguide.e2-s0-coherent-controlled-verdict/v0.1",
        "mission_id": MISSION_ID,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "coherent_result_dir": outcome.get("result_dir"),
        "goal_evidence": goal.get("goal_evidence"),
    }
    (run / "coherent-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run / "coherent-goal-check.json").write_text(
        json.dumps(goal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    """Print the machine-readable E2-S0 verdict for one evidence directory."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-controlled.py RUN_DIRECTORY")
    verdict = verify(Path(sys.argv[1]))
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if verdict["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
