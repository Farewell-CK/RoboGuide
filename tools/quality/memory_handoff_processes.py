"""Check stopped-Group exchange through actual software Nodes, TLS, files, SQLite and CPU work."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, stop, wait_for
from memory_group_lifecycle import GroupScenario
from memory_preparation_profile import mission
from memory_use_profile import request
from spatial_content_policy import digest


def single_mission(
    identifier: str,
    manifest: dict[str, Any],
    consumer: bool,
    bootstrap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one actual file operation from the existing semantic two-operation profile."""
    accepted = mission(identifier, manifest)
    index = 1 if consumer else 0
    accepted["tasks"] = [accepted["tasks"][index]]
    accepted["tasks"][0]["depends_on"] = []
    accepted["mission"]["actors"] = [accepted["mission"]["actors"][index]]
    accepted["contexts"][0]["roles"] = [accepted["contexts"][0]["roles"][index]]
    if bootstrap is not None:
        accepted["tasks"][0]["roles"][0]["requirements"]["memories"] = [
            {
                "selector": bootstrap["selector"],
                "kind": bootstrap["kind"],
                "payload_schema": bootstrap["payload_schema"],
                "media_type": bootstrap["media_type"],
            }
        ]
    accepted["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["rounds"] = (
        1 if consumer else 2_000_000
    )
    return accepted


def source_manifest(manifest: dict[str, Any], identifier: str) -> None:
    """Freeze the predeclared software source Group without adding any physical identity."""
    manifest["scope"] = {"kind": "execution_group", "execution_group_id": "group-" + identifier}
    manifest["source_mission_id"] = identifier


def bootstrap_source(directory: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Declare distinct actual input so source stop also requires actual provider revocation."""
    from check_state_verifier_processes import ROOT

    bootstrap = copy.deepcopy(manifest)
    bootstrap["selector"] = {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex}
    bootstrap["scope"] = {"kind": "global"}
    bootstrap["source_mission_id"] = None
    original = ROOT / "core/domain/src/memory_use.rs"
    source = directory / "source/node-state/memory/files/bootstrap.bin"
    source.write_bytes(original.read_bytes())
    bootstrap["artifact"] = {
        "content_digest": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
        "byte_size": source.stat().st_size,
    }
    assert bootstrap["artifact"]["content_digest"] != manifest["artifact"]["content_digest"]
    save(directory / "handoff-bootstrap.json", bootstrap)
    save(
        directory / "source/exports.json",
        [
            {"manifest": manifest, "artifact_path": "source.bin"},
            {"manifest": bootstrap, "artifact_path": "bootstrap.bin"},
        ],
    )
    profile_path = directory / "artifact-authorization.json"
    profile = json.loads(profile_path.read_bytes())
    for grant in profile["grants"].values():
        grant["memory_ids"].append(bootstrap["selector"]["memory_id"])
    save(profile_path, profile)
    return bootstrap


def source_configuration(text: str, node: str) -> str:
    """Declare real file-worker exit observation with explicit software-only assurance."""
    support = {
        "schema": "roboguide.execution-safety/v0.1",
        "operations": [
            {
                "operation": {
                    "namespace": "compute",
                    "name": "hash-exported-file",
                    "version": "v1",
                },
                "safe_state_export_id": "file-worker-exit",
                "max_evidence_age_ms": 3000,
                "stop_deadline_ms": 10000,
                "min_confidence_millionths": None,
                "assurance": "software-process",
            }
        ],
    }
    text = text.replace(
        '[local_systems.metadata]\nassurance = "software-process"',
        '[local_systems.metadata]\nassurance = "software-process"\n'
        + '"roboguide.execution-safety" = '
        + json.dumps(json.dumps(support, separators=(",", ":"))),
    )
    text += f'''
[[state_exports]]
id = "file-worker-exit"
owner = "posix-compute"
object_class = "node"
object_type = "software-file-worker"
object_id = "{node}"
semantic = "observed"
payload_schema = "roboguide.safe-state-observation/v0.2"
valid_for_ms = 3000
interval_ms = 100
value_pointer = ""
[state_exports.step]
id = "observe-file-worker-exit"
connection = "compute-http"
[state_exports.step.operation]
kind = "http"
method = "GET"
path = "/v1/file-jobs/safe-state"
'''
    return text


def checkpoint(directory: Path) -> dict[str, Any]:
    """Read only the actual committed authority wrapper, without editing execution evidence."""
    with sqlite3.connect(
        f"file:{directory / 'controller.sqlite3'}?mode=ro", uri=True
    ) as connection:
        row = connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()
    assert row is not None
    value: dict[str, Any] = json.loads(row[0])
    return value


def verify(directory: Path, index: int = 0) -> dict[str, Any]:
    """Independently hash actual original/imported bytes and reconstruct the target CPU result."""
    evidence = json.loads((directory / f"handoff-computation-{index}.json").read_bytes())
    parent = json.loads((directory / "manifest.json").read_bytes())
    digests = []
    for path in (
        directory / "source/node-state/memory/files/source.bin",
        directory / "consumer/backend/content" / parent["artifact"]["content_digest"][7:],
    ):
        with path.open("rb") as stream:
            digests.append("sha256:" + hashlib.file_digest(stream, "sha256").hexdigest())
        assert path.stat().st_size == parent["artifact"]["byte_size"]
    assert digests == [parent["artifact"]["content_digest"]] * 2
    result = evidence["computation"]["result"]
    assert result["input_digests"] == [digests[0]] and result["rounds"] == 1
    expected = json.dumps(
        {"input_digests": [digests[0]], "rounds": 1}, sort_keys=True, separators=(",", ":")
    ).encode()
    assert result["output_digest"] == "sha256:" + hashlib.sha256(expected).hexdigest()
    assert result["execution_id"] == evidence["attempt"]["execution_id"]
    return {"passed": True, "source_digest": digests[0], "execution_id": result["execution_id"]}


def run(scenario: GroupScenario, source_id: str, case: str) -> dict[str, Any]:
    """Require actual stop, immutable scope, receiver preparation and explicit commit."""
    source_path = "/v1/missions/" + source_id
    source_attempt = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == source_id and row["status"] == "Running"
            ),
            None,
        ),
        "actual original file-hashing worker running",
    )
    identifier = uuid.uuid4().hex
    child = copy.deepcopy(scenario.manifest)
    child["selector"]["revision_id"] = uuid.uuid4().hex
    child["scope"]["execution_group_id"] = "group-" + identifier
    child["created_at"] = time.time_ns() // 1_000_000
    accepted = single_mission(identifier, child, True)
    next_task = copy.deepcopy(accepted["tasks"][0])
    next_task["id"] = "follow-up"
    next_task["depends_on"] = [accepted["tasks"][0]["id"]]
    accepted["tasks"].append(next_task)
    save(scenario.directory / "handoff-target-mission.json", accepted)
    status, response = scenario.exchange("/v1/missions", accepted)
    assert status == 202, response
    wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == identifier
            ),
            None,
        ),
        "current Control-bound receiving attempt",
    )
    registry = scenario.get("/v1/memory-authorizations")["registry"]
    previous = registry["revision"]
    registry["revision"] += 1
    source_key = "/".join(scenario.manifest["selector"].values())
    registry["policies"][source_key]["principals"]["file-administrator"] = ["handoff"]
    policy = {
        "policy_id": uuid.uuid4().hex,
        "selector": child["selector"],
        "manifest_digest": digest(child),
        "tenant_id": "local",
        "principals": {"independent-file-reader": ["discover"]},
        "groups": [
            {
                "group_id": "group-" + identifier,
                "operations": ["read", "import"],
                "providers": [
                    {
                        "provider_id": "files",
                        "local_system_id": "posix-compute",
                        "kind": "artifact",
                        "payload_schema": child["payload_schema"],
                        "media_type": child["media_type"],
                    }
                ],
            }
        ],
        "revoked": False,
    }
    registry["policies"]["/".join(child["selector"].values())] = policy
    status, response = scenario.exchange(
        "/v1/memory-authorizations",
        {"command_id": uuid.uuid4().hex, "expected_revision": previous, "registry": registry},
    )
    assert status == 200, response
    frozen = checkpoint(scenario.directory)
    groups = json.loads(frozen["integration_json"])["control"]["groups"]
    source_group = next(group for group in groups if group["group_id"] == "group-" + source_id)
    target_group = next(group for group in groups if group["group_id"] == "group-" + identifier)
    command = {
        "schema_version": "roboguide.memory-handoff/v0.1",
        "handoff_id": uuid.uuid4().hex,
        "parent_selector": scenario.manifest["selector"],
        "child": child,
        "source_group_id": source_group["group_id"],
        "source_membership": source_group["membership_version"],
        "source_execution_ids": [source_attempt["execution_id"]],
        "target_membership": target_group["membership_version"],
        "registry_id": registry["registry_id"],
        "registry_revision": registry["revision"],
        "deadline_ms": time.time_ns() // 1_000_000 + (1500 if case == "expiry" else 90000),
    }
    save(scenario.directory / "handoff-command.json", command)
    # Reject partial/stale/changed authority before it can stop an otherwise valid source.
    for field, changed in (
        ("source_execution_ids", []),
        ("registry_revision", previous),
        ("deadline_ms", time.time_ns() // 1_000_000),
    ):
        invalid = {**command, "handoff_id": uuid.uuid4().hex, field: changed}
        status, response = scenario.exchange("/v1/memory/handoffs", invalid)
        assert status == 409, response
    before = checkpoint(scenario.directory)
    if case == "complete":
        with sqlite3.connect(scenario.directory / "controller.sqlite3") as db:
            db.execute("""CREATE TRIGGER fail_handoff_admission BEFORE INSERT ON events
                WHEN json_extract(NEW.payload_json,'$.MemoryHandoffTransition.phase')='stopping'
                BEGIN SELECT RAISE(ABORT,'actual handoff admission rollback window'); END""")
        status, failed = scenario.exchange("/v1/memory/handoffs", command)
        assert status == 409, failed
        assert scenario.get(source_path)["isolation"] is None
        rolled_back = checkpoint(scenario.directory)
        assert rolled_back["memory_handoffs"] == before["memory_handoffs"]
        assert rolled_back["safety_stops"] == before["safety_stops"]
        assert (
            json.loads(rolled_back["integration_json"])["control"]["reservations"]
            == json.loads(before["integration_json"])["control"]["reservations"]
        )
        with sqlite3.connect(scenario.directory / "controller.sqlite3") as db:
            db.execute("DROP TRIGGER fail_handoff_admission")
        save(
            scenario.directory / "handoff-sql-admission-rollback.json",
            {"status": status, "error": failed, "source_held": False, "ownership_retained": True},
        )
    status, response = scenario.exchange("/v1/memory/handoffs", command)
    assert status == 202, response
    original_admitted = response["record"]["admitted_at_ms"]
    status, repeated = scenario.exchange("/v1/memory/handoffs", command)
    assert status == 202 and repeated["record"]["admitted_at_ms"] == original_admitted
    changed = copy.deepcopy(command)
    changed["deadline_ms"] += 1
    assert scenario.exchange("/v1/memory/handoffs", changed)[0] == 409
    commit_path = "/v1/memory/handoffs/" + command["handoff_id"] + "/commit"
    assert scenario.exchange(commit_path, {})[0] == 409

    def progress() -> dict[str, Any]:
        """Observe the exact original handoff rather than treating an HTTP ACK as progress."""
        rows = scenario.get("/v1/memory/handoffs")["records"]
        return next(row["record"] for row in rows if row["handoff_id"] == command["handoff_id"])

    if case in {"expiry", "policy-change", "controller-restart"}:
        if case == "controller-restart":
            wait_for(
                lambda: progress()["phase"] == "ready",
                "receiver ready before actual Controller crash window",
                60,
            )
            original = checkpoint(scenario.directory)
            stop(scenario.controller)
            scenario.controller = scenario.start(
                scenario.controller_command, "handoff-pending-controller-restored"
            )
            wait_for(
                lambda: scenario.get("/healthz").get("status") == "ok",
                "pending handoff Controller restores",
            )
        if case == "policy-change":
            next_registry = copy.deepcopy(registry)
            next_registry["revision"] += 1
            next_registry["policies"][source_key]["principals"]["file-administrator"] = ["discover"]
            status, response = scenario.exchange(
                "/v1/memory-authorizations",
                {
                    "command_id": uuid.uuid4().hex,
                    "expected_revision": registry["revision"],
                    "registry": next_registry,
                },
            )
            assert status == 200, response
        failed = wait_for(
            lambda: row if (row := progress())["phase"] == "manual" else None,
            "original exchange enters manual hold without target Execute",
        )
        assert scenario.exchange(commit_path, {})[0] == 409
        with sqlite3.connect(scenario.directory / "consumer/backend/file-jobs.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        after = checkpoint(scenario.directory)
        assert (
            json.loads(before["integration_json"])["control"]["reservations"]
            == json.loads(after["integration_json"])["control"]["reservations"]
        )
        if case == "controller-restart":
            for execution, record in original["memory_preparation"]["records"].items():
                restored = after["memory_preparation"]["records"][execution]
                assert (record["admitted_at_ms"], record["deadline_ms"]) == (
                    restored["admitted_at_ms"],
                    restored["deadline_ms"],
                )
            assert failed["admitted_at_ms"] == original_admitted
            assert failed["request"]["deadline_ms"] == command["deadline_ms"]
        save(scenario.directory / "handoff-manual.json", failed)
        return {
            "passed": True,
            "case": case,
            "devices": 0,
            "software_nodes": 2,
            "ownership_retained": True,
            "target_execute_count": 0,
        }
    ready = wait_for(
        lambda: row if (row := progress())["phase"] == "ready" else None,
        "actual stop, scoped publication, import, probe and use grant",
        60,
    )
    save(scenario.directory / "handoff-ready.json", ready)
    assert ready["parent"] == scenario.manifest and ready["request"]["child"] == child
    stopped = checkpoint(scenario.directory)["safety_stops"][source_attempt["execution_id"]]
    assert stopped["record"]["phase"] == "stop_confirmed"
    assert stopped["support"]["assurance"] == "software-process"
    assert stopped["safe_proof"]["observation"]["safe_state"] is True
    save(scenario.directory / "handoff-actual-stop.json", stopped)
    source_context = checkpoint(scenario.directory)["memory_preparation"]["records"][
        source_attempt["execution_id"]
    ]
    source_use = checkpoint(scenario.directory)["memory_use"]["records"][
        source_attempt["execution_id"]
    ]
    assert source_context["phase"] == "revoked" and source_use["revoked"] is True
    revoke = next(
        delivery
        for delivery in source_use["controls"].values()
        if delivery["command"]["operation"] == "revoke"
    )
    assert revoke["receipt"]["status"] == "revoked"
    assert [proof["provider_id"] for proof in revoke["receipt"]["providers"]] == ["files"]
    bootstrap = json.loads((scenario.directory / "handoff-bootstrap.json").read_bytes())
    assert source_context["preparation"]["inputs"][0]["manifest"] == bootstrap
    imported = (
        scenario.directory / "source/backend/content" / bootstrap["artifact"]["content_digest"][7:]
    )
    assert (
        "sha256:" + hashlib.sha256(imported.read_bytes()).hexdigest()
        == bootstrap["artifact"]["content_digest"]
    )
    save(
        scenario.directory / "handoff-original-source-revocation.json",
        {
            "preparation": source_context,
            "use": source_use,
            "independent_bootstrap_digest": bootstrap["artifact"]["content_digest"],
        },
    )
    with sqlite3.connect(scenario.directory / "consumer/backend/file-jobs.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    with sqlite3.connect(scenario.directory / "controller.sqlite3") as db:
        db.execute("""CREATE TRIGGER fail_handoff_commit BEFORE INSERT ON events
            WHEN json_extract(NEW.payload_json,'$.MemoryHandoffTransition.phase')='committed'
            BEGIN SELECT RAISE(ABORT,'actual handoff commit rollback window'); END""")
    status, failed = scenario.exchange(commit_path, {})
    assert status == 409 and progress()["phase"] == "ready", failed
    with sqlite3.connect(scenario.directory / "consumer/backend/file-jobs.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    with sqlite3.connect(scenario.directory / "controller.sqlite3") as db:
        db.execute("DROP TRIGGER fail_handoff_commit")
    save(
        scenario.directory / "handoff-sql-commit-rollback.json",
        {"status": status, "error": failed, "target_execute_count": 0},
    )
    status, committed = scenario.exchange(commit_path, {})
    assert status == 202 and committed["record"]["phase"] == "committed", committed
    save(scenario.directory / "handoff-committed.json", committed)
    status, retried = scenario.exchange(commit_path, {})
    assert (
        status == 202
        and retried["record"]["committed_at_ms"] == committed["record"]["committed_at_ms"]
    )
    for index, task in enumerate(accepted["tasks"]):
        task_id = task["id"]
        completed = wait_for(
            lambda task_id=task_id: next(
                (
                    row
                    for row in scenario.get("/v1/execution-attempts")["attempts"]
                    if row["mission_id"] == identifier
                    and row["task_id"] == task_id
                    and row["status"] == "Completed"
                ),
                None,
            ),
            "actual target CPU computation after committed exchange",
        )
        status, computation = request(
            scenario.backends[1],
            "/v1/file-jobs/status",
            {"execution_id": completed["execution_id"]},
            method="GET",
        )
        assert status == 200 and computation["state"] == "COMPLETED", computation
        save(
            scenario.directory / f"handoff-computation-{index}.json",
            {"attempt": completed, "computation": computation},
        )
        independent = subprocess.run(
            [sys.executable, str(Path(__file__)), str(scenario.directory), str(index)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        verified = json.loads(independent.stdout)
        assert verified["passed"] is True
        save(scenario.directory / f"handoff-independent-verifier-{index}.json", verified)
        verdict = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": "independent-file-reader",
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": identifier, "task_id": completed["task_id"]},
            "plan_digest": scenario.get("/v1/missions/" + identifier + "/tenant")[
                "source_plan_digest"
            ],
            "attempts": [
                {"role_id": completed["role_id"], "attempt_id": completed["execution_id"]}
            ],
            "predicate": "all file digests match",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 10000,
            "sequence": 1,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }
        status, response = scenario.exchange(
            "/v1/verifier-evidence", verdict, context=scenario.verifier_context
        )
        assert status == 200, response
    wait_for(
        lambda: scenario.get("/v1/missions/" + identifier)["status"] == "Completed",
        "target Mission independent semantic satisfaction",
    )
    final = checkpoint(scenario.directory)
    save(scenario.directory / "handoff-follow-up-authority.json", final["memory_preparation"])
    source_view = scenario.get(source_path)
    assert source_view["isolation"] is not None
    stop(scenario.controller)
    scenario.controller = scenario.start(scenario.controller_command, "handoff-controller-restored")
    wait_for(lambda: scenario.get("/healthz").get("status") == "ok", "restored Controller")
    restored = checkpoint(scenario.directory)
    assert restored["memory_handoffs"] == final["memory_handoffs"]
    assert scenario.get("/v1/missions/" + identifier)["status"] == "Completed"
    for name, count in (("source", 1), ("consumer", 2)):
        with sqlite3.connect(scenario.directory / name / "backend/file-jobs.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == count
    return {
        "passed": True,
        "case": case,
        "devices": 0,
        "simulators": 0,
        "models": 0,
        "software_nodes": 2,
        "actual_hash_jobs": 3,
        "source_software_stop_confirmed": True,
        "original_source_context_and_provider_revocation": True,
        "actual_sql_rollback_windows": 2,
        "target_execute_before_commit": False,
        "independent_verifiers": 2,
        "original_budget_unchanged_after_restart": True,
        "source_automatically_resumed": False,
        "target_mission": "Completed",
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 0)))
