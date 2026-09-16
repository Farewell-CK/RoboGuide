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
    local_outcome = row.pop("local_outcome_json")
    row["local_outcome"] = json.loads(local_outcome) if isinstance(local_outcome, str) else None
    return row


def verify(run: Path, mode: str) -> dict[str, object]:
    """Evaluate the C1-S0B controlled criteria from persisted production evidence."""
    mission = json.loads((run / "mission.json").read_text(encoding="utf-8"))
    events = _events(run / "events.json")
    kinds = [next(iter(event["payload"])) for event in events]
    bridge = _bridge_row(run / "habitat-bridge.sqlite3")
    raw_outcome = bridge["local_outcome"]
    outcome = raw_outcome if isinstance(raw_outcome, dict) else {}

    trace_path = run / "evidence/action_trace.jsonl"
    trace = (
        [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        if trace_path.is_file()
        else []
    )
    controlled_path = run / "evidence/controlled-outcome.json"
    controlled = (
        json.loads(controlled_path.read_text(encoding="utf-8")) if controlled_path.is_file() else {}
    )
    raw_skill_sequence = controlled.get("skill_sequence", outcome.get("skill_sequence", []))
    skill_sequence = list(raw_skill_sequence) if isinstance(raw_skill_sequence, list) else []

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

    initial = outcome.get("initial_position")
    final = outcome.get("final_position")
    moved = (
        any(
            abs(float(before) - float(after)) > 0.05
            for before, after in zip(initial, final, strict=True)
        )
        if isinstance(initial, list) and isinstance(final, list) and len(initial) == len(final)
        else False
    )
    local_state = outcome.get("state", bridge.get("state"))
    local_outcome_present = bool(outcome)
    checks = {
        "post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_terminal": mission["status"] in {"Completed", "Failed", "Cancelled"},
        "original_stage2_called": controlled.get("policy", {}).get("stage2_policy")
        == "original EMOS MultiLLMPolicy/HierarchicalPolicy",
        "nav_to_obj_selected": any("nav_to_obj" in item for item in skill_sequence),
        "running_observed": "status observed for"
        in (run / "habitat-bridge.log").read_text(encoding="utf-8")
        and ": RUNNING" in (run / "habitat-bridge.log").read_text(encoding="utf-8"),
        "simulator_stepped": int(outcome.get("simulator_steps", 0)) > 0,
        "physical_position_changed": moved,
        "local_terminal": local_state in {"COMPLETED", "FAILED", "CANCELLED"},
        "exactly_once": len(node_rows) == 1 and int(authorizations) == 1,
        "no_recovery": "RuntimeExecutionRecoveryRequired" not in kinds,
        "controller_alive": "Error:"
        not in (run / "integration-server.log").read_text(encoding="utf-8"),
        "node_connected": "session ended"
        not in (run / "roboguide-node.log").read_text(encoding="utf-8"),
        "bridge_responsive": "Traceback"
        not in (run / "habitat-bridge.log").read_text(encoding="utf-8"),
        "llm_calls_recorded": int(outcome.get("local_llm_calls", 0)) > 0,
        "local_skill_completed": outcome.get("local_skill_completed") is True,
        "mission_matches_local": (mission["status"] == "Completed") == (local_state == "COMPLETED"),
        "subtask_mode": mode,
        "skill_sequence": skill_sequence,
        "simulator_trace_count": len(trace),
        "llm_calls": int(outcome.get("local_llm_calls", 0)),
        "local_replans": int(outcome.get("local_replans", 0)),
        "local_model_tokens": int(outcome.get("local_tokens", 0)),
        "invalid_outputs": int(outcome.get("invalid_outputs", 0)),
        "send_request_count": int(outcome.get("send_request_count", 0)),
        "message_pipe_activity_count": int(outcome.get("message_pipe_activity_count", 0)),
        "physical_execution_record_count": len(node_rows),
        "physical_dispatch_count": int(authorizations),
        "benchmark_task_achieved": outcome.get("benchmark_task_achieved"),
        "episode_terminated": outcome.get("episode_terminated"),
        "local_outcome_present": local_outcome_present,
        "local_outcome_state": local_state,
        "local_terminal_basis": outcome.get("terminal_basis"),
        "local_execution_completed": local_state == "COMPLETED",
        "mission_status": mission["status"],
    }
    hard_keys = [
        "post_202",
        "mission_terminal",
        "original_stage2_called",
        "nav_to_obj_selected",
        "running_observed",
        "simulator_stepped",
        "physical_position_changed",
        "local_terminal",
        "exactly_once",
        "no_recovery",
        "controller_alive",
        "node_connected",
        "bridge_responsive",
        "llm_calls_recorded",
        "local_execution_completed",
        "mission_matches_local",
    ]
    passed = all(checks[key] for key in hard_keys)
    return {
        "checks": checks,
        "local_execution": {
            "execution_id": bridge["execution_id"],
            "outcome": outcome if local_outcome_present else None,
        },
        "mission_id": MISSION_ID,
        "schema": "roboguide.c1-s0b-controlled-verdict/v0.2",
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
