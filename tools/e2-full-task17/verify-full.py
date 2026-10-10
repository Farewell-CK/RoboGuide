#!/usr/bin/env python3
"""Verify an unedited RoboGuide-generated task17 plan against runtime evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_ID = "mission-e2-s1-coherent-env4-task17"
PHASE_NODE = {
    "arm-load-milk": "coherent-arm-e2-s1",
    "drone-lower-basket": "coherent-drone-e2-s1",
    "dog-load-book": "coherent-dog-e2-s1",
    "drone-deliver": "coherent-drone-e2-s1",
}
PHASE_OPERATION = {
    "arm-load-milk": "coherent.arm-phase@v1",
    "drone-lower-basket": "coherent.drone-phase@v1",
    "dog-load-book": "coherent.dog-phase@v1",
    "drone-deliver": "coherent.drone-phase@v1",
}


def read(path: Path) -> dict[str, Any]:
    """Read one required JSON object or fail closed on a non-object value."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def payload_name(event: dict[str, Any]) -> str:
    """Return the single typed event payload name, or an empty string if malformed."""
    payload = event.get("payload", {})
    return str(next(iter(payload))) if isinstance(payload, dict) and len(payload) == 1 else ""


def payload_body(event: dict[str, Any]) -> dict[str, Any]:
    """Return the single typed event payload body, or an empty object if malformed."""
    payload = event.get("payload", {})
    if not isinstance(payload, dict) or len(payload) != 1:
        return {}
    body = next(iter(payload.values()))
    return body if isinstance(body, dict) else {}


def task_ref(event: dict[str, Any]) -> str:
    """Extract the referenced Mission task identifier from an event."""
    ref = payload_body(event).get("task_ref", {})
    return str(ref.get("task_id", "")) if isinstance(ref, dict) else ""


def edge(graph: dict[str, Any], source: int, target: int, relation: str) -> bool:
    """Check whether the final COHERENT graph contains one exact relation edge."""
    return any(
        item.get("from_id") == source
        and item.get("to_id") == target
        and item.get("relation_type") == relation
        for item in graph.get("edges", [])
        if isinstance(item, dict)
    )


def main() -> int:
    """Verify the generated plan, Controller evidence, and official task17 goals."""
    run = Path(sys.argv[1])
    plan_path = run / "generated-mission-plan.json"
    plan = read(plan_path)
    mission = read(run / "mission.json")
    inventory = read(run / "inventory.json")
    events = [
        item for item in read(run / "events.json").get("events", []) if isinstance(item, dict)
    ]
    attempts = [
        item
        for item in read(run / "execution-attempts.json").get("attempts", [])
        if isinstance(item, dict)
    ]

    planned: dict[str, dict[str, Any]] = {}
    dependency_chain = True
    previous: str | None = None
    for task in plan.get("tasks", []):
        if not isinstance(task, dict) or len(task.get("roles", [])) != 1:
            dependency_chain = False
            continue
        role = task["roles"][0]
        intent = role.get("execution_intent", {})
        parameters = intent.get("parameters", {})
        phase = str(parameters.get("phase", ""))
        task_id = str(task.get("id", ""))
        dependencies = task.get("depends_on", [])
        dependency_chain = dependency_chain and dependencies == (
            [] if previous is None else [previous]
        )
        planned[task_id] = {
            "phase": phase,
            "role_id": str(role.get("id", "")),
            "operation": ".".join(
                [
                    str(intent.get("operation", {}).get("namespace", "")),
                    str(intent.get("operation", {}).get("name", "")),
                ]
            )
            + "@"
            + str(intent.get("operation", {}).get("version", "")),
            "parameters": parameters,
            "objective": str(intent.get("objective", "")),
        }
        previous = task_id

    attempts_by_task = {str(item.get("task_id")): item for item in attempts}
    expected_nodes = {task: PHASE_NODE.get(info["phase"], "") for task, info in planned.items()}
    actual_nodes = {task: str(item.get("node_id", "")) for task, item in attempts_by_task.items()}

    connection = sqlite3.connect(run / "coherent-graph.sqlite3")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT invocation_json, state, local_outcome_json FROM executions "
        "ORDER BY created_at_unix_ms, execution_id"
    ).fetchall()
    state = connection.execute(
        "SELECT graph_json, step_count, next_phase FROM graph_state WHERE singleton=1"
    ).fetchone()
    connection.close()
    if state is None:
        raise RuntimeError("graph state is missing")
    graph = json.loads(str(state["graph_json"]))
    executions = [
        {
            "invocation": json.loads(str(row["invocation_json"])),
            "state": str(row["state"]),
            "outcome": json.loads(str(row["local_outcome_json"])),
        }
        for row in rows
    ]
    execution_by_task = {str(item["invocation"].get("task_id")): item for item in executions}
    invocation_exact = len(execution_by_task) == 4
    for task, info in planned.items():
        actual = execution_by_task.get(task, {}).get("invocation", {})
        invocation_exact = invocation_exact and all(
            [
                actual.get("role_id") == info["role_id"],
                actual.get("operation") == info["operation"],
                actual.get("parameters") == info["parameters"],
                actual.get("objective") == info["objective"],
            ]
        )

    satisfied = {task_ref(event) for event in events if payload_name(event) == "TaskSatisfied"}
    completed = {
        task_ref(event): int(event.get("sequence", 0))
        for event in events
        if payload_name(event) == "TaskExecutionCompleted"
    }
    activated = {
        task_ref(event): int(event.get("sequence", 0))
        for event in events
        if payload_name(event) == "TaskExecutionActivated"
    }
    order = [task for task in planned]
    runtime_dependency_order = all(
        completed.get(order[index], 10**9) < activated.get(order[index + 1], -1)
        for index in range(len(order) - 1)
    )
    plan_phases = [info["phase"] for info in planned.values()]
    checks = {
        "submitted_plan_byte_identical": hashlib.sha256(plan_path.read_bytes()).hexdigest()
        == read(run / "provenance.json").get("generated_plan_sha256"),
        "post_202": (run / "post-status.txt").read_text().strip() == "202",
        "mission_completed": mission.get("mission_id") == MISSION_ID
        and mission.get("status") == "Completed",
        "four_model_planned_tasks": len(planned) == 4,
        "exact_four_phases": plan_phases == list(PHASE_NODE),
        "model_dependency_chain": dependency_chain,
        "canonical_operations": all(
            info["operation"] == PHASE_OPERATION.get(info["phase"]) for info in planned.values()
        ),
        "task17_parameters": all(
            info["parameters"].get("env") == "env4" and info["parameters"].get("task") == 17
            for info in planned.values()
        ),
        "three_nodes_registered": {
            "coherent-arm-e2-s1",
            "coherent-drone-e2-s1",
            "coherent-dog-e2-s1",
        }.issubset(
            {
                str(node.get("node_id"))
                for node in inventory.get("nodes", [])
                if isinstance(node, dict)
            }
        ),
        "controller_assignments_match_embodiments": len(attempts) == 4
        and actual_nodes == expected_nodes,
        "generated_invocations_executed_exactly": invocation_exact,
        "all_local_executions_completed": len(executions) == 4
        and all(item["state"] == "COMPLETED" for item in executions),
        "runtime_dependency_order": runtime_dependency_order,
        "all_generated_tasks_satisfied": satisfied == set(planned),
        "original_gt_steps": int(state["step_count"]) == 13,
        "all_phases_committed": int(state["next_phase"]) == 4,
        "book_inside_basket": edge(graph, 34, 29, "INSIDE"),
        "milk_inside_basket": edge(graph, 35, 29, "INSIDE"),
        "drone_on_swing_table": edge(graph, 25, 16, "ON"),
        "no_failed_or_recovery_event": not any(
            "Failed" in payload_name(event) or "Recovery" in payload_name(event) for event in events
        ),
    }
    verdict = {
        "schema": "roboguide.e2-full-coherent-graph-verdict/v0.1",
        "public_task": "env4/task17",
        "model_generated_tasks": planned,
        "task_to_node": actual_nodes,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }
    (run / "graph-final.json").write_text(
        json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8"
    )
    (run / "graph-executions.json").write_text(
        json.dumps(executions, indent=2, sort_keys=True), encoding="utf-8"
    )
    (run / "verdict.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
