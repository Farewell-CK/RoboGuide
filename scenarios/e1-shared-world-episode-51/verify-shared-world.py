"""Verify one E1-I shared-world smoke evidence directory."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_TASKS = {
    "task-navigate-goal-a": "any_targets|0",
    "task-navigate-goal-b": "TARGET_any_targets|0",
}
NEGATIVE_TASKS = {
    "task-navigate-goal-a": "any_targets|0",
    "task-navigate-goal-b": "any_targets|0",
}


def _events(path: Path) -> list[dict[str, Any]]:
    """Load the ordered Controller event response."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document["events"])


def _kinds(events: list[dict[str, Any]]) -> list[str]:
    """Return the externally visible variant name of each domain event."""
    return [next(iter(event["payload"])) for event in events]


def _bridge_rows(database: Path) -> list[dict[str, Any]]:
    """Read every local execution row from one endpoint's durable store."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM executions").fetchall()
    finally:
        connection.close()
    decoded = []
    for row in rows:
        item = dict(row)
        item["invocation"] = json.loads(item.pop("invocation_json"))
        outcome_raw = item.pop("local_outcome_json")
        item["local_outcome"] = (
            json.loads(outcome_raw) if outcome_raw is not None else None
        )
        decoded.append(item)
    return decoded


def _intents_match_mission(
    rows: list[dict[str, Any]], mission: dict[str, Any]
) -> bool:
    """Check each endpoint invocation preserves its mission task's canonical intent."""
    plan_tasks = {
        task["id"]: task["roles"][0]["execution_intent"]
        for task in _mission_plan_tasks(mission)
    }
    intents = {}
    for row in rows:
        invocation = row["invocation"]
        intents[invocation["task_id"]] = invocation
    if set(intents) != set(plan_tasks):
        return False
    for task_id, intent in plan_tasks.items():
        invocation = intents[task_id]
        if invocation["operation"] != "mobility.navigate@v1":
            return False
        if invocation["parameters"]["destination"] != intent["parameters"]["destination"]:
            return False
        if invocation["objective"] != intent["objective"]:
            return False
    return True


def _mission_plan_tasks(mission: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the mission plan tasks from the scenario fixture."""
    plan = json.loads(
        (
            Path(__file__).parent
            / ("mission-plan-negative.json" if "negative" in mission["mission_id"] else "mission-plan.json")
        ).read_text(encoding="utf-8")
    )
    return list(plan["tasks"])


def _node_journal(database: Path) -> tuple[int, int, list[str]]:
    """Return execution rows, dispatch authorizations, and statuses."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT status FROM executions").fetchall()
        authorizations = connection.execute(
            "SELECT COUNT(*) FROM local_dispatch_authorizations"
        ).fetchone()[0]
    finally:
        connection.close()
    return len(rows), int(authorizations), [str(row[0]) for row in rows]


def verify(run: Path, mode: str) -> dict[str, Any]:
    """Evaluate the E1-I shared-world criteria from persisted evidence."""
    mission = json.loads((run / "mission.json").read_text(encoding="utf-8"))
    events = _events(run / "events.json")
    kinds = _kinds(events)
    expected_tasks = NEGATIVE_TASKS if mode == "negative" else MISSION_TASKS
    summary_path = run / "evidence/shared-world-summary.json"
    shared = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {}
    )
    identity = shared.get("identity", {})
    outcomes = {
        int(key): value for key, value in shared.get("outcomes", {}).items()
    }
    arrivals = [
        json.loads(line)
        for line in (run / "evidence/assignment-arrival.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    bridge_a = _bridge_rows(run / "bridge-a.sqlite3")
    bridge_b = _bridge_rows(run / "bridge-b.sqlite3")
    journal_a = _node_journal(run / "node-state-a/execution-journal.sqlite3")
    journal_b = _node_journal(run / "node-state-b/execution-journal.sqlite3") if (
        run / "node-state-b/execution-journal.sqlite3"
    ).exists() else (0, 0, [])
    inventory = (run / "inventory.json").read_text(encoding="utf-8")
    bridge_log = (run / "shared-bridge.log").read_text(encoding="utf-8")
    server_log = (run / "integration-server.log").read_text(encoding="utf-8")

    bound_tasks = set()
    for event in events:
        payload = event["payload"]
        if "ExecutionGroupBound" in payload:
            bound_tasks.add(payload["ExecutionGroupBound"]["task_ref"]["task_id"])

    def _moved(outcome: dict[str, Any]) -> bool:
        """Check the agent's base actually moved in the shared world."""
        return any(
            abs(float(before) - float(after)) > 0.05
            for before, after in zip(
                outcome["initial_position"], outcome["final_position"], strict=True
            )
        )

    if mode == "single":
        checks = {
            "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
            "single_node_registered": '"e1-shared-node-a"' in inventory,
            "second_node_absent": '"e1-shared-node-b"' not in inventory,
            "pair_never_assembled_fail_closed": bridge_a
            and bridge_a[0]["state"] == "FAILED"
            and "pair never assembled" in bridge_a[0]["detail"],
            "no_fake_pddl_success": not outcomes,
            "no_shared_episode": not (run / "evidence/shared-world-summary.json").exists()
            or identity.get("episode_reset_count", 0) == 0,
            "mission_failed_not_success": mission["status"] == "Failed",
        }
        return _verdict(mode, checks, {
            "mission_status": mission["status"],
            "lone_local_state": bridge_a[0]["state"] if bridge_a else None,
            "lone_detail": bridge_a[0]["detail"] if bridge_a else None,
        })

    checks: dict[str, Any] = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "two_nodes_registered": '"e1-shared-node-a"' in inventory
        and '"e1-shared-node-b"' in inventory,
        "two_tasks_bound": bound_tasks == set(expected_tasks),
        "two_distinct_local_handles": (
            len(bridge_a) == 1
            and len(bridge_b) == 1
            and bridge_a[0]["execution_id"] != bridge_b[0]["execution_id"]
        ),
        "both_running_observed": "RUNNING" in bridge_log,
        "one_simulator_world": identity.get("simulator_worlds") == 1,
        "one_episode_reset": identity.get("episode_reset_count") == 1,
        # Movement is a hard paired-mode criterion; cancel and negative runs
        # may stop agents before either base moves.
        "both_agents_moved": mode != "paired"
        or (len(outcomes) == 2 and all(_moved(o) for o in outcomes.values())),
        "canonical_intents_preserved": _intents_match_mission(
            bridge_a + bridge_b, mission
        ),
        "original_stage2_active": all(
            outcome["local_llm_calls"] >= 1 for outcome in outcomes.values()
        ),
        "no_stage1_reassignment": len(arrivals) == 2
        and {a["destination"] for a in arrivals} == set(expected_tasks.values()),
        "node_a_exactly_once": journal_a[0] == 1 and journal_a[1] == 1,
        "node_b_exactly_once": journal_b[0] == 1 and journal_b[1] == 1,
        "assign_admitted_once_each": len(arrivals) == 2,
        "controller_alive": "Error:" not in server_log,
        "no_recovery_storm": kinds.count("RuntimeExecutionRecoveryRequired") == 0,
    }
    context = {
        "mission_status": mission["status"],
        "task_statuses": {t["task_id"]: t["status"] for t in mission["tasks"]},
        "identity": identity,
        "outcomes": {str(k): v for k, v in outcomes.items()},
        "peer_communication": {
            agent: {
                "send_request_count": v["send_request_count"],
                "message_pipe_activity_count": v["message_pipe_activity_count"],
            }
            for agent, v in outcomes.items()
        },
    }
    if mode == "paired":
        checks["official_pddl_success"] = shared.get("official_pddl_success") is True
        checks["both_goals_satisfied"] = all(
            v["benchmark_task_achieved"] for v in outcomes.values()
        )
        checks["mission_completed"] = mission["status"] == "Completed"
    elif mode == "negative":
        checks["pddl_success_false"] = shared.get("official_pddl_success") is False
        checks["no_benchmark_success_despite_task_completion"] = not shared.get(
            "official_pddl_success", False
        )
    elif mode == "cancel":
        checks["cancel_accepted"] = (run / "cancel-status.txt").read_text(
            encoding="utf-8"
        ).strip() in {"200", "202"}
        checks["mission_cancelled"] = mission["status"] == "Cancelled"
        checks["local_outcomes_terminal"] = all(
            row[0]["state"] in {"CANCELLED", "FAILED", "COMPLETED"}
            for row in (bridge_a, bridge_b)
        )
    return _verdict(mode, checks, context)


def _verdict(mode: str, checks: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Assemble the machine verdict for one mode."""
    hard = [value for key, value in checks.items() if isinstance(value, bool)]
    return {
        "checks": checks,
        "context": context,
        "mode": mode,
        "schema": "roboguide.e1-shared-world-verdict/v0.1",
        "verdict": "PASS" if all(hard) else "FAIL",
    }


def main() -> None:
    """Print one machine-readable verdict for the supplied run and mode."""
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-shared-world.py RUN_DIRECTORY MODE")
    verdict = verify(Path(sys.argv[1]), sys.argv[2])
    print(json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False))
    if verdict["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
