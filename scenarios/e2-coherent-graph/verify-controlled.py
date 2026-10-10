#!/usr/bin/env python3
"""Verify E2-S1 from controller, node, adapter, and original graph evidence."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_ID = "mission-e2-s1-coherent-env4-task17"
TASKS = ["arm-load-milk", "drone-lower-basket", "dog-load-book", "drone-deliver"]
EXPECTED_NODES = {
    "arm-load-milk": "coherent-arm-e2-s1",
    "drone-lower-basket": "coherent-drone-e2-s1",
    "dog-load-book": "coherent-dog-e2-s1",
    "drone-deliver": "coherent-drone-e2-s1",
}
EXPECTED_OPERATIONS = {
    "arm-load-milk": "coherent.arm-phase@v1",
    "drone-lower-basket": "coherent.drone-phase@v1",
    "dog-load-book": "coherent.dog-phase@v1",
    "drone-deliver": "coherent.drone-phase@v1",
}


def read_json(path: Path) -> dict[str, Any]:
    """Read one required JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def payload_name(event: dict[str, Any]) -> str:
    """Return the sole domain payload variant name for one event."""
    payload = event.get("payload", {})
    if not isinstance(payload, dict) or len(payload) != 1:
        return ""
    return str(next(iter(payload)))


def payload_body(event: dict[str, Any]) -> dict[str, Any]:
    """Return the inner object for one single-variant event payload."""
    payload = event.get("payload", {})
    if not isinstance(payload, dict) or len(payload) != 1:
        return {}
    body = next(iter(payload.values()))
    return body if isinstance(body, dict) else {}


def task_ref_id(event: dict[str, Any]) -> str:
    """Extract the task identity from a task-scoped event when present."""
    reference = payload_body(event).get("task_ref", {})
    return str(reference.get("task_id", "")) if isinstance(reference, dict) else ""


def has_edge(graph: dict[str, Any], source: int, target: int, relation: str) -> bool:
    """Test one exact final graph relation."""
    edges = graph.get("edges", [])
    return any(
        isinstance(edge, dict)
        and edge.get("from_id") == source
        and edge.get("to_id") == target
        and edge.get("relation_type") == relation
        for edge in edges
    )


def main() -> int:
    """Build an automatic verdict and return nonzero unless every check passes."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-controlled.py RUN_DIR")
    run = Path(sys.argv[1])
    mission = read_json(run / "mission.json")
    inventory = read_json(run / "inventory.json")
    events_doc = read_json(run / "events.json")
    attempts_doc = read_json(run / "execution-attempts.json")
    events = [item for item in events_doc.get("events", []) if isinstance(item, dict)]
    attempts = [item for item in attempts_doc.get("attempts", []) if isinstance(item, dict)]

    connection = sqlite3.connect(run / "coherent-graph.sqlite3")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT invocation_json, state, local_outcome_json FROM executions ORDER BY created_at_unix_ms, execution_id"
    ).fetchall()
    state = connection.execute(
        "SELECT graph_json, step_count, next_phase FROM graph_state WHERE singleton = 1"
    ).fetchone()
    connection.close()
    if state is None:
        raise RuntimeError("graph state row is missing")
    graph = json.loads(str(state["graph_json"]))
    executions = []
    for row in rows:
        executions.append(
            {
                "invocation": json.loads(str(row["invocation_json"])),
                "state": str(row["state"]),
                "outcome": json.loads(str(row["local_outcome_json"])),
            }
        )
    (run / "graph-final.json").write_text(
        json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8"
    )
    (run / "graph-executions.json").write_text(
        json.dumps(executions, indent=2, sort_keys=True), encoding="utf-8"
    )

    attempt_map = {str(item.get("task_id")): str(item.get("node_id")) for item in attempts}
    execution_map = {
        str(item["invocation"]["task_id"]): item
        for item in executions
        if isinstance(item.get("invocation"), dict)
    }
    node_ids = {
        str(node.get("node_id"))
        for node in inventory.get("nodes", [])
        if isinstance(node, dict)
    }
    satisfied = {task_ref_id(event) for event in events if payload_name(event) == "TaskSatisfied"}
    completed_sequences = {
        task_ref_id(event): int(event.get("sequence", 0))
        for event in events
        if payload_name(event) == "TaskExecutionCompleted"
    }
    activated_sequences = {
        task_ref_id(event): int(event.get("sequence", 0))
        for event in events
        if payload_name(event) == "TaskExecutionActivated"
    }
    dependency_order = all(
        completed_sequences.get(TASKS[index], 10**9)
        < activated_sequences.get(TASKS[index + 1], -1)
        for index in range(len(TASKS) - 1)
    )
    operations = {
        task: str(item["invocation"].get("operation", ""))
        for task, item in execution_map.items()
    }
    phase_steps = {
        task: int(item["outcome"].get("steps_after", -1))
        for task, item in execution_map.items()
        if isinstance(item.get("outcome"), dict)
    }
    evidence_files = sorted((run / "graph-evidence").glob("*.json"))
    checks = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_completed": mission.get("mission_id") == MISSION_ID
        and mission.get("status") == "Completed",
        "all_tasks_completed": {
            str(task.get("task_id"))
            for task in mission.get("tasks", [])
            if isinstance(task, dict) and task.get("status") == "Completed"
        }
        == set(TASKS),
        "three_nodes_registered": {
            "coherent-arm-e2-s1",
            "coherent-drone-e2-s1",
            "coherent-dog-e2-s1",
        }.issubset(node_ids),
        "four_formal_attempts": len(attempts) == 4 and attempt_map == EXPECTED_NODES,
        "exact_operation_mapping": operations == EXPECTED_OPERATIONS,
        "all_local_phases_completed": len(executions) == 4
        and all(item.get("state") == "COMPLETED" for item in executions),
        "dependency_release_order": dependency_order,
        "all_tasks_satisfied": satisfied == set(TASKS),
        "original_gt_steps": int(state["step_count"]) == 13
        and phase_steps == {
            "arm-load-milk": 2,
            "drone-lower-basket": 5,
            "dog-load-book": 9,
            "drone-deliver": 13,
        },
        "all_phases_committed": int(state["next_phase"]) == 4,
        "phase_evidence_complete": len(evidence_files) == 4,
        "book_inside_basket": has_edge(graph, 34, 29, "INSIDE"),
        "milk_inside_basket": has_edge(graph, 35, 29, "INSIDE"),
        "drone_on_swing_table": has_edge(graph, 25, 16, "ON"),
        "no_failed_or_recovery_event": not any(
            "Failed" in payload_name(event) or "Recovery" in payload_name(event) for event in events
        ),
    }
    verdict = {
        "schema": "roboguide.e2-s1-coherent-graph-verdict/v0.1",
        "mission_id": MISSION_ID,
        "public_task": "env4/task17",
        "checks": checks,
        "phase_steps": phase_steps,
        "task_to_node": attempt_map,
        "final_goal_edges": {
            "inside_book_basket": checks["book_inside_basket"],
            "inside_milk_basket": checks["milk_inside_basket"],
            "on_drone_swing_table": checks["drone_on_swing_table"],
        },
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }
    (run / "verdict.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
