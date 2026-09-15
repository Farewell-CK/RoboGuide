"""C0-C3 post-fix per-run criteria verifier.

Checks one snapshot evidence directory (postfix/run-N) against the frozen
C0-C3 acceptance criteria and the old C0C-B1 failure signature, then prints
one verdict JSON document.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

MISSION_ID = "mission-c0c3-parallel-demo"
TASK_A = "task-infer-analysis"
TASK_B = "task-move-gate"
CHAIN = [
    "CandidatesMatched",
    "TaskSchedulingSelected",
    "ProposalCreated",
    "PlanCommitted",
    "ExecutionGroupBound",
    "MissionActorBound",
    "TaskExecutionActivated",
    "NodeObservation",
    "TaskExecutionCompleted",
    "TaskSatisfied",
]


def task_seq(payload: dict, task: str) -> bool:
    """Reports whether one event payload references the given task."""
    return json.dumps(payload, ensure_ascii=False).find(task) >= 0


def load_events(path: Path) -> list[dict]:
    """Loads the events snapshot as an ordered list."""
    doc = json.loads(path.read_text())
    return doc.get("events", doc) if isinstance(doc, dict) else doc


def check_chain(events: list[dict], task: str) -> tuple[bool, str]:
    """Verifies the full per-task lifecycle chain preserves its order."""
    positions: list[int] = []
    for expected in CHAIN:
        found = next(
            (
                i
                for i, event in enumerate(events)
                if list(event["payload"].keys())[0] == expected
                and task_seq(event["payload"][expected], task)
            ),
            None,
        )
        if found is None:
            return False, f"{task}: chain event {expected} missing"
        positions.append(found)
    if positions != sorted(positions):
        return False, f"{task}: chain events out of order"
    return True, f"{task}: full chain {len(CHAIN)} events in order"


def journal_counts(journal: Path) -> tuple[int, str, int]:
    """Returns execution row count, terminal status, and authorization count."""
    con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        rows = con.execute("select execution_id, status from executions").fetchall()
        auths = con.execute("select count(*) from local_dispatch_authorizations").fetchone()[0]
    finally:
        con.close()
    status = rows[0][1] if len(rows) == 1 else f"unexpected rows: {rows}"
    return len(rows), status, int(auths)


def eaios_dispatch_count(log: Path) -> int:
    """Counts physical dispatch calls (POST /v1/executions) in one EAIOS log."""
    count = 0
    for line in log.read_text().splitlines():
        entry = json.loads(line)
        if entry["method"] == "POST" and entry["path"] == "/v1/executions":
            count += 1
    return count


def verify(run_dir: Path) -> dict:
    """Evaluates every frozen C0-C3 criterion for one run directory."""
    checks: dict[str, object] = {}

    post_status = (run_dir / "responses/c0c3-post-status.txt").read_text().strip()
    checks["post_202"] = post_status == "202"

    mission = json.loads((run_dir / "responses/c0c3-mission.json").read_text())
    checks["mission_completed"] = mission["status"] == "Completed"
    tasks = {t["task_id"]: t["status"] for t in mission["tasks"]}
    checks["task_a_completed"] = tasks.get(TASK_A) == "Completed"
    checks["task_b_completed"] = tasks.get(TASK_B) == "Completed"
    checks["relations_empty"] = mission["relations"] == []
    checks["peer_channels_empty"] = mission["peer_channels"] == []

    events = load_events(run_dir / "events/c0c3-events.json")
    kinds = [list(e["payload"].keys())[0] for e in events]
    checks["zero_recovery_required"] = "RuntimeExecutionRecoveryRequired" not in kinds
    checks["zero_group_blocked"] = "ExecutionGroupBlocked" not in kinds
    chain_a, note_a = check_chain(events, TASK_A)
    chain_b, note_b = check_chain(events, TASK_B)
    checks["chain_task_a"] = chain_a
    checks["chain_task_b"] = chain_b
    checks["chain_notes"] = [note_a, note_b]
    checks["group_completed"] = "ExecutionGroupCompleted" in kinds

    attempts = json.loads((run_dir / "responses/c0c3-execution-attempts.json").read_text())[
        "attempts"
    ]
    checks["attempts_terminal"] = all(a["status"] != "Unknown" for a in attempts)

    for node in ("a", "b"):
        rows, status, auths = journal_counts(
            run_dir / f"node-journals/{node}/execution-journal.sqlite3"
        )
        checks[f"node_{node}_single_execution"] = rows == 1 and status == "completed"
        checks[f"node_{node}_single_authorization"] = auths == 1
        eaios = run_dir / f"run/eaios-node-{node}.jsonl"
        dispatches = eaios_dispatch_count(eaios)
        checks[f"node_{node}_single_physical_execute"] = dispatches == 1

    server_log = (run_dir / "logs/c0c3-server.log").read_text()
    checks["server_no_fatal"] = "Error:" not in server_log
    checks["server_no_invalid_lifecycle"] = "invalid lifecycle" not in server_log

    node_loops = {
        node: (run_dir / f"logs/c0c3-node-{node}.log").read_text().count("session ended")
        for node in ("a", "b")
    }
    checks["nodes_no_session_loss"] = all(count == 0 for count in node_loops.values())
    checks["node_session_end_counts"] = node_loops

    passed = all(
        value if isinstance(value, bool) else True
        for key, value in checks.items()
        if key != "chain_notes"
    )
    return {
        "schema": "roboguide.c0c-postfix-verdict/v0.1",
        "run": run_dir.name,
        "mission_id": MISSION_ID,
        "verdict": "PASS" if passed else "FAIL",
        "checks": checks,
    }


def main() -> None:
    """Prints the verdict JSON for one run directory argument."""
    print(json.dumps(verify(Path(sys.argv[1])), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
