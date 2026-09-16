"""Verify one C1-S0B controlled production smoke evidence directory."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

MISSION_ID = "mission-c1-s0-habitat-navigation"


def _events(path: Path) -> list[dict[str, Any]]:
    """Load the ordered Controller event response."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document["events"])


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


def verify(run: Path, mode: str) -> dict[str, object]:
    """Evaluate the C1-S0B controlled criteria from persisted production evidence."""
    mission = json.loads((run / "mission.json").read_text(encoding="utf-8"))
    events = _events(run / "events.json")
    kinds = [next(iter(event["payload"])) for event in events]
    bridge = _bridge_row(run / "habitat-bridge.sqlite3")
    outcome = bridge["local_outcome"]

    trace = [
        json.loads(line)
        for line in (run / "evidence/action_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    decisions = [record["decision"] for record in trace if record.get("decision")]
    nav_calls = [
        decision
        for decision in decisions
        if isinstance(decision, dict) and decision.get("name") == "nav_to_obj"
    ]
    usage_lines = (
        (run / "evidence/token_usage_details.jsonl").read_text(encoding="utf-8").splitlines()
    )
    usage = [json.loads(line) for line in usage_lines if line.strip()]
    pipe_hits = [
        record for record in trace if record.get("message_pipe_entries")
    ]

    connection = sqlite3.connect(
        f"file:{run / 'node-state/execution-journal.sqlite3'}?mode=ro", uri=True
    )
    try:
        node_rows = connection.execute("SELECT status FROM executions").fetchall()
        authorizations = connection.execute(
            "SELECT COUNT(*) FROM local_dispatch_authorizations"
        ).fetchone()[0]
    finally:
        connection.close()

    initial = outcome["initial_position"]
    final = outcome["final_position"]
    moved = any(
        abs(float(before) - float(after)) > 0.05
        for before, after in zip(initial, final, strict=True)
    )
    nav_targets = [
        arguments.get("target_obj")
        for decision in nav_calls
        if isinstance((arguments := decision.get("arguments")), dict)
    ]
    checks = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_terminal": mission["status"] in {"Completed", "Failed", "Cancelled"},
        "real_crabagent_called": bool(trace),
        "nav_to_obj_selected": bool(nav_calls),
        "nav_target_recorded": bool(nav_targets),
        "running_observed": "status observed for" in (run / "habitat-bridge.log").read_text(
            encoding="utf-8"
        )
        and ": RUNNING" in (run / "habitat-bridge.log").read_text(encoding="utf-8"),
        "simulator_stepped": int(outcome["simulator_steps"]) > 0,
        "physical_position_changed": moved,
        "local_terminal": outcome["state"] in {"COMPLETED", "FAILED", "CANCELLED"},
        "exactly_once": len(node_rows) == 1 and int(authorizations) == 1,
        "no_recovery": "RuntimeExecutionRecoveryRequired" not in kinds,
        "controller_alive": "Error:" not in (run / "integration-server.log").read_text(
            encoding="utf-8"
        ),
        "node_connected": "session ended" not in (run / "roboguide-node.log").read_text(
            encoding="utf-8"
        ),
        "bridge_responsive": "Traceback" not in (run / "habitat-bridge.log").read_text(
            encoding="utf-8"
        ),
        "llm_calls_recorded": len(usage) > 0,
        "mission_matches_local": (mission["status"] == "Completed")
        == (outcome["state"] == "COMPLETED"),
        "send_request_absent": all(not record.get("message_pipe_entries") for record in trace),
        "subtask_mode": mode,
        "nav_targets": nav_targets,
        "decision_count": len(trace),
        "llm_calls": len(usage),
        "local_model_tokens": sum(int(entry["usage"]["total_tokens"]) for entry in usage),
        "local_outcome_state": outcome["state"],
        "mission_status": mission["status"],
    }
    hard_keys = [
        key
        for key, value in checks.items()
        if isinstance(value, bool)
    ]
    passed = all(checks[key] for key in hard_keys)
    return {
        "checks": checks,
        "local_execution": {
            "execution_id": bridge["execution_id"],
            "outcome": outcome,
        },
        "mission_id": MISSION_ID,
        "schema": "roboguide.c1-s0b-controlled-verdict/v0.1",
        "verdict": "PASS" if passed else "FAIL",
    }


def main() -> None:
    """Print one machine-readable verdict for the supplied run and mode."""
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-controlled.py RUN_DIRECTORY SUBTASK_MODE")
    verdict = verify(Path(sys.argv[1]), sys.argv[2])
    print(json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False))
    if verdict["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
