"""Exercise Core scheduling observations across the real B1 collection boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from b1_helpers import ROOT, build_provenance, make_run, write_json
from roboguide_eval.b1_admission import FailureOwner
from roboguide_eval.b1_artifacts import collect_b1_artifacts
from roboguide_eval.results import summarize_results
from test_b1_artifact_chain import collect


def observation_boundary(run: Path, *, dead_controller: bool = False) -> dict[str, str]:
    """Run the script's actual polling/EXIT code with offline reads and no SUT launch."""
    script = (ROOT / "scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh").read_text()
    functions = script[script.index("finish_run() {") : script.index('\nmkdir -p "$RUN"')]
    accepted = script[script.index('if [[ "$LIFECYCLE" == "Accepted" ]]; then') :]
    # Only the existing functions and post-submission wait are executed. Stubs bound
    # the polling loop to one read and capture collection arguments before cleanup.
    offline = r"""
set -euo pipefail
RUN="$1" REPO="$2" REQUEST_ID=request MISSION_ID=mission LIFECYCLE=Accepted
FAILURE_OWNER=SUT_SYSTEM FAILURE_COMPONENT=mission_service FAILURE_REASON=mission_ingress_failed
PIDS=(123) COMPONENTS=(controller)
DEAD_CONTROLLER="$3"
curl() { return 0; }
sleep() { return 0; }
seq() { printf '1\n'; }
kill() { [[ "$DEAD_CONTROLLER" == false ]]; }
uv() { printf '%s\n' "$@"; }
"""
    result = subprocess.run(
        ["bash", "-s", "--", str(run), str(ROOT), str(dead_controller).lower()],
        input=offline + functions + "\ntrap finish_run EXIT\n" + accepted,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    arguments = result.stdout.splitlines()
    return {
        name: arguments[arguments.index("--" + name) + 1]
        for name in ("failure-owner", "component", "reason")
    }


def deferred_run(root: Path, reason: str, prior_attempt: bool) -> Path:
    """Archive the Core's Ready plus TaskSchedulingDeferred shape, without a failure."""
    run = make_run(root, case="G")
    mission = json.loads((run / "mission.json").read_text())
    request = json.loads((run / "b1-request-record.json").read_text())
    tasks = request["plan"]["tasks"]
    mission["status"] = "Running"
    mission["tasks"] = [{"task_id": task["id"], "status": "Ready"} for task in tasks]
    events = json.loads((run / "events.json").read_text())
    events["events"].append(
        {
            "payload": {
                "TaskSchedulingDeferred": {
                    "task_ref": {"mission_id": mission["mission_id"], "task_id": tasks[-1]["id"]},
                    "reason": reason,
                }
            }
        }
    )
    attempts = json.loads((run / "execution-attempts.json").read_text())
    attempts["attempts"] = attempts["attempts"][:1] if prior_attempt else []
    write_json(run / "mission.json", mission)
    write_json(run / "events.json", events)
    write_json(run / "execution-attempts.json", attempts)
    build_provenance(run)
    return run


def archive_boundary(
    run: Path, boundary: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Feed the shell's actual attribution into the canonical archive/verdict path."""
    monkeypatch.setattr("roboguide_eval.b1_artifacts._fetch", Mock(return_value=None))
    # These cases consume pre-existing offline evidence without recollecting it.
    # Live acquisition (including an unconfirmed terminal view) has separate tests.
    monkeypatch.setattr("roboguide_eval.b1_artifacts.collect_controller_events", Mock())
    request = json.loads((run / "b1-request-record.json").read_text())
    return collect_b1_artifacts(
        run,
        request_id=request["request_id"],
        mission_endpoint="http://unused",
        controller_endpoint="http://unused",
        owner=FailureOwner(boundary["failure-owner"]),
        component=boundary["component"],
        reason=boundary["reason"],
    )


@pytest.mark.parametrize(
    "reason", ["distinct-entities-unavailable", "activation-revalidation", "window-missed"]
)
@pytest.mark.parametrize("prior_attempt", [False, True])
def test_scheduling_wait_budget_is_not_system_failure(
    tmp_path: Path, reason: str, prior_attempt: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F02/F03/F05 Ready deferrals stay nonfailure through script, verdict and Harness."""
    run = deferred_run(tmp_path, reason, prior_attempt)
    boundary = observation_boundary(run)
    assert boundary["failure-owner"] == "NONE"
    verdict = archive_boundary(run, boundary, monkeypatch)
    assert not (run / "run-failure.json").exists()
    assert verdict["system_outcome"] == "UNKNOWN"
    metrics = collect(run, exit_code=1)
    assert metrics["details"]["failure_owner"] == "BENCHMARK_AUTHORITY_UNAVAILABLE"
    assert metrics["values"]["system_failure"] is False
    assert metrics["values"]["infrastructure_failure"] is False
    assert "success" not in metrics["values"]
    # Waiting never fabricates an execution attempt to bypass F07's provenance gate.
    assert metrics["values"]["valid_for_formal_population"] is prior_attempt
    assert metrics["values"]["valid_for_benchmark_population"] is False
    aggregate = summarize_results(tmp_path)["aggregate"]
    assert isinstance(aggregate, dict)
    assert aggregate["system_failure_observations"] == 0
    assert aggregate["invalid_infra_runs"] == 0


@pytest.mark.parametrize("failure", ["process", "mission"])
def test_real_controller_failure_after_deferral_remains_attributed(
    tmp_path: Path, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier scheduling deferral cannot hide a subsequent real Control failure."""
    run = deferred_run(tmp_path, "distinct-entities-unavailable", False)
    boundary = observation_boundary(run, dead_controller=failure == "process")
    if failure == "mission":
        mission = json.loads((run / "mission.json").read_text())
        mission["status"] = "Failed"
        write_json(run / "mission.json", mission)
    verdict = archive_boundary(run, boundary, monkeypatch)
    assert verdict["system_outcome"] == "FAILURE"
    assert verdict["admission"]["failure_owner"] == "SUT_SYSTEM"
    metrics = collect(run, exit_code=1)
    assert metrics["values"]["system_failure"] is True
    assert metrics["values"]["valid_for_formal_population"] is True
    assert metrics["values"]["valid_for_benchmark_population"] is False
    assert "success" not in metrics["values"]
