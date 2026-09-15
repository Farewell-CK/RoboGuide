"""Verify one C1-S0 production cancellation sanity evidence directory."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _bridge_row(database: Path) -> dict[str, Any]:
    """Read the sole bridge execution and decode its terminal local outcome."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM executions").fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise AssertionError(f"expected one local execution, found {len(rows)}")
    row = dict(rows[0])
    row["local_outcome"] = json.loads(row.pop("local_outcome_json"))
    return row


def verify(run: Path) -> dict[str, object]:
    """Prove cancellation acceptance preceded true terminal local cancellation evidence."""
    mission = json.loads((run / "mission.json").read_text(encoding="utf-8"))
    bridge = _bridge_row(run / "habitat-bridge.sqlite3")
    bridge_log = (run / "habitat-bridge.log").read_text(encoding="utf-8")
    accepted_marker = (
        f"cancellation request accepted for {bridge['execution_id']} while local state is RUNNING"
    )
    terminal_marker = f"local execution {bridge['execution_id']} terminated: CANCELLED"
    accepted_at = bridge_log.find(accepted_marker)
    terminal_at = bridge_log.find(terminal_marker)
    checks = {
        "mission_post_202": (run / "post-status.txt").read_text(encoding="utf-8").strip() == "202",
        "cancel_post_202": (run / "cancel-status.txt").read_text(encoding="utf-8").strip() == "202",
        "mission_cancelled": mission["status"] == "Cancelled",
        "cancel_accepted_while_running": accepted_at >= 0,
        "terminal_cancelled_later": terminal_at > accepted_at,
        "local_cancelled": bridge["state"] == "CANCELLED"
        and bridge["local_outcome"]["state"] == "CANCELLED",
        "controller_alive": "Error:"
        not in (run / "integration-server.log").read_text(encoding="utf-8"),
        "node_connected": "session ended"
        not in (run / "roboguide-node.log").read_text(encoding="utf-8"),
        "bridge_responsive": "BrokenPipeError" not in bridge_log and "Traceback" not in bridge_log,
    }
    return {
        "checks": checks,
        "local_execution": {
            "execution_id": bridge["execution_id"],
            "outcome": bridge["local_outcome"],
        },
        "schema": "roboguide.c1-s0-habitat-cancel-verdict/v0.1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }


def main() -> None:
    """Print one machine-readable cancellation verdict for the supplied run directory."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-cancel.py RUN_DIRECTORY")
    verdict = verify(Path(sys.argv[1]))
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if verdict["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
