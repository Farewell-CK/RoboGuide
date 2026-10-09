from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from render_live_status import discover_runs, render_markdown


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _recovery_fixture(run_dir: Path, *, injection_valid: bool = True) -> None:
    old_id = "attempt:r07-1"
    new_id = "attempt:r07-2"
    _write_json(
        run_dir / "fault-verdict.json",
        {
            "fault_profile": "f1-node-loss",
            "injection_valid": injection_valid,
            "infrastructure_ok": True,
            "task_success": False,
            "full_fault_run_success": False,
            "base_task_verdict": {
                "public_task": "env4/task17",
                "failure_reason": "planning failed; token=must-not-be-published",
                "official_goal_check": {
                    "satisfied": ["goal-a", "goal-b"],
                    "unsatisfied": {"goal-c": [1, []]},
                },
            },
            "controller_attempts": [
                {"execution_id": old_id, "status": "Cancelled"},
                {"execution_id": new_id, "status": "Completed"},
            ],
            "pre_fault_graph": {
                "graph": {
                    "nodes": [
                        {"id": 25, "class_name": "quadrotor"},
                    ]
                }
            },
        },
    )
    _write_json(
        run_dir / "fault-timeline.json",
        [
            {
                "event": "fault_triggered",
                "target_agent_id": 25,
                "accepted_invocation": {
                    "parameters": {
                        "agent_id": 25,
                        "agent_class": "quadrotor",
                        "action": "[land_on] <floor>(1)",
                    }
                },
            },
            {
                "event": "local_handle_confirmed",
                "agent_id": 25,
                "node_id": "coherent-agent-25-generic",
            },
            {"event": "recovery_required"},
            {"event": "recovery_authorized"},
            {"event": "same_owner_restarted"},
            {"event": "same_owner_registered"},
            {
                "event": "recovery_phase",
                "disposition": "AwaitingStop",
                "recovery_view": {"execution_status": "Unknown"},
            },
            {
                "event": "recovery_phase",
                "disposition": "Superseded",
                "recovery_view": {
                    "execution_status": "Cancelled",
                    "stop_intent": {"release_authorized": True},
                },
            },
            {
                "event": "rebind_completed",
                "original_execution_id": old_id,
                "replacement_execution_id": new_id,
            },
        ],
    )
    _write_json(
        run_dir / "provenance.json",
        {
            "roboguide_commit": "1d01ef75840484278b8abf2b56577af25f1c489a",
            "model": "gpt-6.1-sol",
        },
    )


def test_recovery_and_task_results_remain_separate(tmp_path: Path) -> None:
    _recovery_fixture(tmp_path / "run-1")
    runs = discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].recovery_protocol == "PASS"
    assert runs[0].task_success is False
    assert runs[0].agent_id == 25
    assert runs[0].agent_class == "quadrotor"

    rendered = render_markdown(
        runs,
        generated_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        generator_head="f44e84857867665d5daabb59598f9e407f1a4e12",
        active_state_path=None,
    )
    assert "Recovery Protocol：`PASS`" in rendered
    assert "Task Success：`FAIL`" in rendered
    assert "agent 25 (quadrotor)" in rendered
    assert "2/3" in rendered
    assert "planning/provider failure after Recovery completed" in rendered
    assert "must-not-be-published" not in rendered


def test_invalid_injection_is_not_evaluable(tmp_path: Path) -> None:
    _recovery_fixture(tmp_path / "run-invalid", injection_valid=False)
    runs = discover_runs(tmp_path)
    assert runs[0].recovery_protocol == "NOT_EVALUABLE"


def test_publisher_has_valid_bash_syntax() -> None:
    script = Path(__file__).with_name("publish-live-status.sh")
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_active_state_writer_requires_complete_running_identity(tmp_path: Path) -> None:
    script = Path(__file__).with_name("record_active_run.py")
    state_file = tmp_path / "active.json"
    incomplete = subprocess.run(
        [
            sys.executable,
            str(script),
            "--state-file",
            str(state_file),
            "--status",
            "running",
        ],
        capture_output=True,
        text=True,
    )
    assert incomplete.returncode != 0
    assert not state_file.exists()

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--state-file",
            str(state_file),
            "--status",
            "running",
            "--public-task",
            "env4/task17",
            "--run-id",
            "run-1",
            "--fault-profile",
            "f1-node-loss",
            "--roboguide-commit",
            "abc123",
            "--observed-at",
            "2026-10-09T18:25:36+08:00",
        ],
        check=True,
    )
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert state["run_id"] == "run-1"
