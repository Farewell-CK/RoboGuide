"""Verify actual original CPU preemption, coupled recovery, hazards and cold authority preservation.

All Nodes are actual software service processes; there are zero physical entities, devices,
models or simulators. The co-located Local EAIOS owns each complete original CPU cohort.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from check_identity_processes import request as https
from check_plan_revision_processes import checkpoint
from check_state_verifier_processes import ROOT, save, stop, wait_for
from coordination_processes import Case, semantic_plan, successor
from recovery_profile_selection import declare_controller_storage


def ready_child(case: Case, job: dict[str, Any]) -> bool:
    """Observe the original CPU PID before admitting a stop or replacement."""
    group = case.backend.groups[job["invocation"]["group_id"]]
    return any(
        row["identity"] == job["identity"] and row["native_wait"] is None
        for row in case.backend.rows(group)
    )


def complete(case: Case, job: dict[str, Any]) -> None:
    """Permit actual computation and independently verify its original result."""
    case.backend.release(job["identity"])
    wait_for(lambda: Path(job["output"]).is_file(), "actual original CPU file result")
    case.verify(job["identity"])


def preemption(case: Case) -> dict[str, Any]:
    """Require original native exit before higher-priority Control admission."""
    mission = "preempt-source-" + uuid.uuid4().hex[:8]
    original = semantic_plan(mission)
    case.exchange("/v1/missions", original)
    group = case.view(mission)["current_group_id"]
    wait_for(lambda: len(case.group_jobs(group)) == 1, "original preemption CPU invocation")
    job = case.group_jobs(group)[0]
    wait_for(lambda: ready_child(case, job), "actual original preemption CPU PID")
    case.exchange(f"/v1/missions/{mission}/scheduling-policy", {"priority": 10})
    beneficiary = "higher-priority-" + uuid.uuid4().hex[:8]
    case.exchange("/v1/missions", semantic_plan(beneficiary))
    case.exchange(f"/v1/missions/{beneficiary}/scheduling-policy", {"priority": 200})
    case.policy(mission)
    case.command(mission, "request-candidate", {"ticket_id": "preserve-original-context"}, "viewer")
    before = case.snapshot("original-cohort-running", mission)
    permission = case.authorize(
        mission, "running-preemption", "preserve-original-context", beneficiary
    )
    command = case.commands[-1]
    assert case.exchange(f"/v1/missions/{mission}/plan-revisions", command["input"]) == permission
    assert len(case.backend.jobs) == 1
    case.reviewed(mission, "preserve-original-context", successor(original))
    target = "resumed-source-" + uuid.uuid4().hex
    switch = case.command(
        mission,
        "request-switch",
        {"ticket_id": "preserve-original-context", "target_group_id": target},
        "viewer",
    )
    wait_for(lambda: bool(case.backend.cancel_receipts), "original preemption Cancel receipt")
    held = case.snapshot("cancel-accepted-cohort-still-live", mission)
    assert held["backend"]["groups"][group]["native_wait"] is None
    assert ready_child(case, job) and len(case.backend.jobs) == 1
    assert held["checkpoint"]["integration"]["control"]["reservations"]
    assert all(
        value["record"]["phase"] != "stop_confirmed"
        for value in held["checkpoint"]["safety_stops"].values()
    )
    premature = {
        "schema_version": "roboguide.runtime-plan-revision/v0.1",
        "command_id": uuid.uuid4().hex,
        "expected_revision": case.view(mission)["authority"]["revision"],
        "action": {"kind": "commit-switch", "switch_command_id": switch["switch_command_id"]},
    }
    status, value = https(
        case.origin, case.contexts["viewer"], f"/v1/missions/{mission}/plan-revisions", premature
    )
    save(case.root / "premature-preemption-refused.json", {"status": status, "value": value})
    assert status == 409 and len(case.backend.jobs) == 1
    case.backend.stop_group(group)
    wait_for(
        lambda: any(row["phase"] == "ready" for row in case.view(mission)["authority"]["switches"]),
        "complete original native stop confirmation",
    )
    case.command(
        mission, "commit-switch", {"switch_command_id": switch["switch_command_id"]}, "viewer"
    )
    beneficiary_group = case.view(beneficiary)["current_group_id"]
    wait_for(
        lambda: len(case.group_jobs(beneficiary_group)) == 1,
        "higher-priority normal Control winner",
    )
    assert not case.group_jobs(target)
    winner = case.group_jobs(beneficiary_group)[0]
    complete(case, winner)
    wait_for(
        lambda: case.exchange(f"/v1/missions/{beneficiary}")["status"] == "Completed",
        "actual beneficiary completes",
    )
    wait_for(
        lambda: len(case.group_jobs(target)) == 1, "original Mission resumes through normal Control"
    )
    complete(case, case.group_jobs(target)[0])
    wait_for(
        lambda: "hash-0-resumed" in case.view(mission)["completed_tasks"],
        "resumed original Task completion",
    )
    case.finish_goal(mission)
    final = case.snapshot("preemption-original-goal-completed", mission)
    assert (
        final["checkpoint"]["runtime_revisions"][mission]["budget"]
        == before["checkpoint"]["runtime_revisions"][mission]["budget"]
    )
    assert case.backend.groups[group]["process"].wait(timeout=5) == -15
    assert all(
        row["native_wait"] is not None for row in case.backend.rows(case.backend.groups[group])
    )
    return {
        "mission": mission,
        "beneficiary": beneficiary,
        "source_group": group,
        "target_group": target,
        "permission_digest": permission["coordination_digest"],
        "actual_original_parent_pid": case.backend.groups[group]["process"].pid,
        "actual_original_parent_wait": -15,
        "higher_priority_won_before_resume": True,
        "original_goal_independently_completed": True,
    }


def coupled(case: Case) -> dict[str, Any]:
    """Replace the complete cohort without repeating the completed prefix."""
    mission = "coupled-source-" + uuid.uuid4().hex[:8]
    original = semantic_plan(mission, coupled=True)
    case.exchange("/v1/missions", original)
    group = case.view(mission)["current_group_id"]
    wait_for(lambda: len(case.group_jobs(group)) == 1, "actual completed-prefix source child")
    seed = case.group_jobs(group)[0]
    complete(case, seed)
    wait_for(lambda: len(case.group_jobs(group)) == 3, "both actual coupled Role invocations")
    wait_for(
        lambda: all(
            ready_child(case, job)
            for job in case.group_jobs(group)
            if job["identity"] != seed["identity"]
        ),
        "two actual coupled CPU child PIDs",
    )
    case.policy(mission)
    case.command(mission, "request-candidate", {"ticket_id": "complete-coupled-cohort"}, "viewer")
    permission = case.authorize(mission, "coupled-recovery", "complete-coupled-cohort")
    before = case.snapshot("original-coupled-cohort", mission)
    assert before["revision"]["completed_tasks"] == ["seed"]
    assert len(before["revision"]["authority"]["candidates"][0]["coordination"]["attempts"]) == 3
    # Real software support cannot authorize a physical transition, even when requested by the
    # administrator.
    rejected = {
        "schema_version": "roboguide.runtime-plan-revision/v0.6",
        "command_id": uuid.uuid4().hex,
        "expected_revision": case.view(mission)["authority"]["revision"],
        "action": {
            "kind": "authorize-coordination",
            "ticket_id": "complete-coupled-cohort",
            "authorization": {
                "schema": "roboguide.execution-coordination/v0.1",
                "kind": "regrasp",
                "beneficiary_mission_id": None,
                "stop_assurance": "software-process",
                "continuation_operations": [
                    {"namespace": "compute", "name": "hash-files", "version": "v1"}
                ],
                "reason": "software assurance cannot become device evidence",
            },
        },
    }
    status, refusal = https(
        case.origin, case.contexts["admin"], f"/v1/missions/{mission}/plan-revisions", rejected
    )
    save(case.root / "physical-assurance-refused.json", {"status": status, "response": refusal})
    assert status == 409
    case.reviewed(mission, "complete-coupled-cohort", successor(original))
    target = "coupled-target-" + uuid.uuid4().hex
    case.backend.group_sizes[target] = 2
    switch = case.command(
        mission,
        "request-switch",
        {"ticket_id": "complete-coupled-cohort", "target_group_id": target},
        "viewer",
    )
    wait_for(
        lambda: len(case.backend.cancel_receipts) >= 2, "complete original cohort Cancel receipts"
    )
    assert case.backend.groups[group]["process"].poll() is None and len(case.backend.jobs) == 3
    case.snapshot("coupled-cancel-receipts-are-not-exits", mission)
    case.backend.stop_group(group)
    wait_for(
        lambda: any(row["phase"] == "ready" for row in case.view(mission)["authority"]["switches"]),
        "all original cohort waits and source barriers",
    )
    case.command(
        mission, "commit-switch", {"switch_command_id": switch["switch_command_id"]}, "viewer"
    )
    wait_for(lambda: len(case.group_jobs(target)) == 2, "both fresh coupled Role workers")
    current = case.view(mission)
    assert current["completed_tasks"] == ["seed"]
    assert not any(job["invocation"]["task_id"] == "seed" for job in case.group_jobs(target))
    # The guarded target completes while its actual source is still alive; no relation success
    # is invented.
    for role in ["beta", "alpha"]:
        job = next(job for job in case.group_jobs(target) if job["invocation"]["role_id"] == role)
        complete(case, job)
        wait_for(
            lambda identity=job["identity"]: any(
                row["execution_id"] == identity and row["status"] == "Completed"
                for row in case.exchange("/v1/execution-attempts")["attempts"]
            ),
            "actual coupled Role completion",
        )
    wait_for(
        lambda: "cohort-resumed" in case.view(mission)["completed_tasks"],
        "fresh coupled Task completion",
    )
    case.finish_goal(mission)
    final = case.snapshot("coupled-original-goal-completed", mission)
    assert len(case.backend.jobs) == 5
    assert final["revision"]["history"][0]["completed_tasks"] == ["seed"]
    assert (
        final["checkpoint"]["runtime_revisions"][mission]["budget"]
        == before["checkpoint"]["runtime_revisions"][mission]["budget"]
    )
    return {
        "mission": mission,
        "source_group": group,
        "target_group": target,
        "permission_digest": permission["coordination_digest"],
        "actual_nodes": len(case.nodes),
        "original_cpu_children": 3,
        "replacement_cpu_children": 2,
        "completed_prefix_repeated": False,
        "original_goal_independently_completed": True,
        "device_regrasp_refused": True,
    }


def hazard(case: Case) -> dict[str, Any]:
    """Observe actual corruption and retain the original stop and Unknown fences."""
    mission = "file-hazard-" + uuid.uuid4().hex[:8]
    case.exchange("/v1/missions", semantic_plan(mission))
    group = case.view(mission)["current_group_id"]
    wait_for(lambda: len(case.group_jobs(group)) == 1, "actual hazard-source CPU invocation")
    job = case.group_jobs(group)[0]
    wait_for(lambda: ready_child(case, job), "actual hazard-source CPU PID")
    node = case.nodes[0]
    policy = {
        "schema": "roboguide.decision-evidence-policy/v0.1",
        "policy_id": "original-file-integrity",
        "revision": 1,
        "object": {"class": "node", "object_type": "software-file-integrity", "object_id": node},
        "channel_id": "file-integrity",
        "allowed_semantics": ["observed"],
        "allowed_sources": [{"owner": "node", "node_id": node, "local_system_id": "posix-compute"}],
        "max_age_ms": 2000,
        "min_confidence_millionths": None,
        "allowed_decisions": ["readiness"],
        "reducer": "require_equivalent",
        "payload_schema": "roboguide.software-file-integrity/v0.1",
    }
    case.exchange(
        "/v1/state-policies",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.decision-policy-registry/v0.3",
                "registry_id": uuid.uuid4().hex,
                "revision": 1,
                "gates": {
                    "original-file-integrity": {
                        "active_stop_timeout_ms": 30000,
                        "tenant_id": "local",
                        "enabled": True,
                        "policy": policy,
                        "target": {
                            "operation": {
                                "namespace": "compute",
                                "name": "hash-files",
                                "version": "v1",
                            }
                        },
                        "candidate_source_only": True,
                        "require_current_session": True,
                        "condition": {"kind": "boolean", "pointer": "/intact", "value": True},
                    }
                },
            },
        },
    )
    wait_for(
        lambda: bool(case.view(mission)["active_hazards"]["watches"]),
        "original actual file-integrity watch",
    )
    before = case.snapshot("actual-integrity-safe-source", mission)
    assert not before["checkpoint"]["safety_stops"]
    path = next((case.local / "inputs").iterdir())
    path.write_bytes(path.read_bytes() + b"\nactual retained fault injection\n")
    wait_for(
        lambda: bool(case.backend.cancel_receipts),
        "actual corruption triggers original whole-cohort Cancel",
    )
    held = case.snapshot("actual-file-corruption-original-process-still-live", mission)
    assert case.backend.groups[group]["process"].poll() is None and ready_child(case, job)
    assert held["mission"]["isolation"] is not None
    assert held["checkpoint"]["integration"]["control"]["reservations"]
    purposes = copy.deepcopy(held["checkpoint"]["safety_stops"])
    assert all(value["record"]["phase"] != "stop_confirmed" for value in purposes.values())
    stop(case.controller)
    case.controller = case.start(case.arguments, "controller-unknown-restarted")
    wait_for(lambda: case.exchange("/healthz"), "actual hazard Unknown restart")
    restored = case.snapshot("original-hazard-authority-restored", mission)
    assert restored["checkpoint"]["hazards"] == held["checkpoint"]["hazards"]
    assert {
        key: value["record"]["deadline"]
        for key, value in restored["checkpoint"]["safety_stops"].items()
    } == {key: value["record"]["deadline"] for key, value in purposes.items()}
    assert len(case.backend.jobs) == 1
    return {
        "mission": mission,
        "source_group": group,
        "actual_corrupted_file": path.name,
        "original_parent_pid": case.backend.groups[group]["process"].pid,
        "cancel_receipt_did_not_prove_exit": True,
        "original_hazard_and_deadline_preserved": True,
        "replacement_executions": 0,
        "physical_stop_proven": False,
    }


def rows(database: Path) -> list[str]:
    """Read original SQL without changing its generation, clocks or authority."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return list(connection.iterdump())


def cold(case: Case, old_controller: Path | None) -> dict[str, Any]:
    """Verify real cold recovery and genuine old-writer refusal after drains."""
    case.close()
    output = case.root.with_name(case.root.name + "-cold")
    output.mkdir()
    components: list[dict[str, Any]] = [
        {
            "component_id": "controller",
            "kind": "controller",
            "root": str(case.root),
            "database": "controller.sqlite3",
            "trees": [],
            "depends_on": ["cas"],
        },
        {
            "component_id": "cas",
            "kind": "artifact",
            "root": str(case.root / "artifacts"),
            "database": None,
            "trees": [],
            "depends_on": [],
            "storage_schema": "roboguide.artifact-storage/v3",
        },
    ]
    for index, root in enumerate(case.node_roots):
        components.append(
            {
                "component_id": f"node-{index}",
                "kind": "node",
                "root": str(root),
                "database": "execution-journal.sqlite3",
                "trees": [],
                "depends_on": ["controller"],
            }
        )
    components.append(
        {
            "component_id": "original-cpu-files",
            "kind": "files",
            "root": str(case.root),
            "database": None,
            "trees": ["local-eaios"],
            "depends_on": [
                "controller",
                *[f"node-{index}" for index in range(len(case.node_roots))],
            ],
        }
    )
    profile = {
        "schema": "roboguide.recovery-profile/v0.28",
        "profile_id": uuid.uuid4().hex,
        "revision": 1,
        "max_bytes": 512 * 1024 * 1024,
        "max_files": 10000,
        "budget_seconds": 120,
        "artifact_replica_sets": [],
        "components": components,
    }
    declare_controller_storage(profile)
    profile_path = output / "profile.json"
    save(profile_path, profile)
    prior = {
        component["component_id"]: rows(Path(component["root"]) / component["database"])
        for component in components
        if component["database"]
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def execute(*arguments: str) -> dict[str, Any]:
        """Invoke the actual standard-library recovery CLI once and retain its native result."""
        child = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=140,
        )
        commands.append(
            {
                "arguments": arguments,
                "exit_code": child.returncode,
                "stdout": child.stdout.decode(),
                "stderr": child.stderr.decode(),
            }
        )
        save(output / "commands.json", commands)
        if child.returncode != 0:
            raise ValueError(
                "actual coordination recovery failed: " + child.stderr.decode()[-4000:]
            )
        return dict(json.loads(child.stdout))

    bundle = output / "bundle"
    arguments = [
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(bundle),
        "--command-id",
        "original-coordination-backup",
    ]
    backed_up = execute(*arguments)
    pin = backed_up["manifest_digest"]
    execute("verify", "--bundle", str(bundle), "--manifest-digest", pin)
    restored = output / "restored"
    restore_arguments = [
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        pin,
        "--destination",
        str(restored),
        "--command-id",
        "original-coordination-restore",
    ]
    receipt = execute(*restore_arguments)
    assert receipt["schema"] == "roboguide.restore-receipt/v0.28"
    assert execute(*arguments) == backed_up and execute(*restore_arguments) == receipt
    targets = {
        component["component_id"]: Path(component["root"]) for component in receipt["components"]
    }
    for component in components:
        if component["database"]:
            assert (
                rows(targets[component["component_id"]] / component["database"])
                == prior[component["component_id"]]
            )
            assert (
                rows(Path(component["root"]) / component["database"])
                == prior[component["component_id"]]
            )
    current = checkpoint(case.root)
    restored_controller = targets["controller"] / "controller.sqlite3"
    with closing(
        sqlite3.connect(restored_controller.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        restored_value = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
    assert restored_value.get("hazards") == current["hazards"]
    assert restored_value.get("runtime_revisions", {}) == current.get("runtime_revisions", {})
    old_exit = None
    if old_controller is not None:
        rejected = subprocess.run(
            [str(old_controller), *case.arguments[1:]],
            cwd=ROOT,
            env=case.environment,
            capture_output=True,
            timeout=15,
        )
        save(
            output / "genuine-old-writer-refused.json",
            {
                "binary": str(old_controller),
                "sha256": hashlib.sha256(old_controller.read_bytes()).hexdigest(),
                "exit_code": rejected.returncode,
                "stderr": rejected.stderr.decode(),
            },
        )
        assert (
            rejected.returncode != 0
            and rows(case.root / "controller.sqlite3") == prior["controller"]
        )
        assert b"v59" in rejected.stdout + rejected.stderr
        old_exit = rejected.returncode
    result = {
        "passed": True,
        "manifest_digest": pin,
        "services_started_by_restore": 0,
        "original_sql_authority_preserved": True,
        "actual_original_cpu_files_copied": True,
        "hardware_or_cross_host_continuity_proven": False,
        "genuine_old_writer_exit": old_exit,
    }
    save(output / "result.json", result)
    return result


def run(output: Path, old_controller: Path | None, selections: list[str]) -> dict[str, Any]:
    """Freeze binaries and run complete actual CPU and cold-recovery cases."""
    output.mkdir(parents=True, exist_ok=False)
    paths = [
        ROOT / "target/debug/integration-server",
        ROOT / "target/debug/roboguide-node",
        Path(__file__),
        Path(__file__).with_name("coordination_processes.py"),
        Path(__file__).with_name("cohort_process_profile.py"),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
    ]
    frozen = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result: dict[str, Any] = {
        "passed": False,
        "devices": 0,
        "physical_entities": 0,
        "models": 0,
        "simulators": 0,
        "cases": {},
        "source_identity": frozen,
    }
    try:
        for selection in selections:
            case = Case(
                output / selection,
                2 if selection == "coupled" else 1,
                3 if selection == "coupled" else 1,
            )
            try:
                case.boot()
                evidence = {"preemption": preemption, "coupled": coupled, "hazard": hazard}[
                    selection
                ](case)
                if selection != "hazard":
                    original = checkpoint(case.root)
                    original_jobs = copy.deepcopy(case.backend.jobs)
                    stop(case.controller)
                    case.controller = case.start(
                        case.arguments, "controller-completed-history-restored"
                    )
                    wait_for(
                        lambda current_case=case: current_case.exchange("/healthz"),
                        "actual completed coordination restart",
                    )
                    restored = case.snapshot(
                        "original-coordination-owner-restored", evidence["mission"]
                    )
                    assert restored["checkpoint"].get("runtime_revisions", {}) == original.get(
                        "runtime_revisions", {}
                    )
                    assert case.backend.jobs == original_jobs
                    evidence["completed_original_owner_restart"] = True
                evidence["recovery"] = cold(case, old_controller)
                evidence["passed"] = True
                result["cases"][selection] = evidence
                print(json.dumps({"case": selection, "passed": True}), flush=True)
            except Exception as error:
                result["cases"][selection] = {
                    "passed": False,
                    "error": str(error),
                    "evidence_root": str(case.root),
                }
                print(
                    json.dumps({"case": selection, "passed": False, "error": str(error)}),
                    flush=True,
                )
            finally:
                case.close()
        if any(
            hashlib.sha256(path.read_bytes()).hexdigest() != frozen[str(path)] for path in paths
        ):
            raise ValueError("original coordination binary/source changed during native acceptance")
        if any(not evidence["passed"] for evidence in result["cases"].values()):
            raise ValueError(
                "actual coordination cases failed; original results retained in result.json"
            )
        result["passed"] = True
        return result
    finally:
        save(output / "result.json", result)


def main() -> None:
    """Run bounded owned-process cases without hardware or model claims."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-controller", type=Path)
    parser.add_argument("--case", action="append", choices=["preemption", "coupled", "hazard"])
    args = parser.parse_args()
    run(
        args.output.absolute(),
        args.previous_controller.resolve(strict=True)
        if args.previous_controller is not None
        else None,
        args.case or ["preemption", "coupled", "hazard"],
    )


if __name__ == "__main__":
    main()
