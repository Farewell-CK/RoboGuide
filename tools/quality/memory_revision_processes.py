"""Check original-owner Memory revision with actual TLS, SQL, CAS and owned CPU processes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from check_identity_processes import client
from check_state_verifier_processes import save, stop, wait_for
from memory_group_lifecycle import GroupScenario
from memory_handoff_processes import checkpoint, quiesce_node_publishers, recover_original_writer
from memory_runtime_mi_processes import generate
from memory_use_profile import request
from spatial_content_policy import digest


def verify(directory: Path, index: int) -> dict[str, Any]:
    """Independently hash actual original/imported bytes and recompute the complete CPU result."""
    parent = json.loads((directory / "manifest.json").read_bytes())
    evidence = json.loads((directory / f"revision-computation-{index}.json").read_bytes())
    typed = directory / "revision-typed-map.json"
    paths = (
        (
            directory / "downloaded.bundle",
            directory / "consumer/backend/content" / parent["artifact"]["content_digest"][7:],
        )
        if typed.is_file()
        else (
            directory / "source/node-state/memory/files/source.bin",
            directory / "source/backend/content" / parent["artifact"]["content_digest"][7:],
        )
    )
    if typed.is_file():
        original = json.loads(typed.read_bytes())
        assert parent["artifact"] == {
            "content_digest": original["content_digest"],
            "byte_size": original["byte_size"],
        }
    hashes = []
    for path in paths:
        with path.open("rb") as stream:
            hashes.append("sha256:" + hashlib.file_digest(stream, "sha256").hexdigest())
        assert path.stat().st_size == parent["artifact"]["byte_size"]
    assert hashes == [parent["artifact"]["content_digest"]] * 2
    expected = {"input_digests": [hashes[0]], "rounds": 1}
    result = evidence["computation"]["result"]
    assert result["input_digests"] == expected["input_digests"] and result["rounds"] == 1
    assert (
        result["output_digest"]
        == "sha256:"
        + hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert result["execution_id"] == evidence["attempt"]["execution_id"]
    return {"passed": True, "source_digest": hashes[0], "execution_id": result["execution_id"]}


def run(
    scenario: GroupScenario,
    mission: str,
    case: str,
    old_controller: Path | None = None,
) -> dict[str, Any]:
    """Exercise stopped-source revision with actual Node, worker and effect evidence."""
    path = "/v1/missions/" + mission
    revision_path = path + "/plan-revisions"
    original = json.loads((scenario.directory / "handoff-source-mission.json").read_bytes())
    source_attempt = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == mission and row["status"] == "Running"
            ),
            None,
        ),
        "actual original file CPU process running",
    )
    database = scenario.directory / scenario.worker_names[0] / "backend/file-jobs.sqlite3"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection, connection:
        process = connection.execute(
            "SELECT pid,instance_id FROM original_processes WHERE execution_id=?",
            (source_attempt["execution_id"],),
        ).fetchone()
    assert process is not None and process[0] is not None
    pid, instance = process
    assert Path(f"/proc/{pid}/stat").is_file()
    save(
        scenario.directory / "revision-original-process.json", {"pid": pid, "instance_id": instance}
    )

    def view() -> dict[str, Any]:
        """Read the exact committed original revision without renewing work."""
        return scenario.get(revision_path)

    def new_computation_count() -> int:
        """Count actual admitted file jobs independently of plan status or checker expectations."""
        count = 0
        for name in scenario.worker_names:
            database = scenario.directory / name / "backend/file-jobs.sqlite3"
            with (
                closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection,
                connection,
            ):
                count += sum(
                    row[0] not in original_jobs
                    for row in connection.execute("SELECT execution_id FROM jobs")
                )
        return count

    original_jobs: set[str] = set()
    for name in scenario.worker_names:
        with closing(
            sqlite3.connect(
                f"file:{scenario.directory / name / 'backend/file-jobs.sqlite3'}?mode=ro", uri=True
            )
        ) as connection:
            original_jobs.update(
                row[0] for row in connection.execute("SELECT execution_id FROM jobs")
            )

    def command(
        kind: str,
        values: dict[str, Any],
        *,
        proposer: bool = False,
        schema: str = "roboguide.runtime-plan-revision/v0.1",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Send one actual authenticated immutable command with its observed expected revision."""
        authority = view().get("authority")
        body: dict[str, Any] = {
            "schema_version": schema,
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0 if authority is None else authority["revision"],
            "action": {"kind": kind, **values},
        }
        status, response = scenario.exchange(
            revision_path, body, context=scenario.verifier_context if proposer else None
        )
        save(
            scenario.directory / (body["command_id"] + ".command.json"),
            {"input": body, "status": status, "response": response},
        )
        assert status == 200, response
        return body, response

    sources = scenario.get("/v1/verifier-sources")["registry"]
    previous = sources["revision"]
    sources["revision"] += 1
    goal_source: dict[str, Any] = {
        "source_id": "independent-revision-file-goal",
        "revision": 1,
        "tenant_id": "local",
        "producer_principals": ["independent-revision-goal-producer"],
        "verifier": {"namespace": "observation", "name": "original-file-goal", "version": "v1"},
        "predicates": ["all original revision file digests match"],
        "max_age_ms": 10000,
        "stop_timeout_ms": 10000,
        "enabled": True,
        "continuous": False,
        "require_artifact": False,
    }
    sources["sources"][goal_source["source_id"]] = goal_source
    status, response = scenario.exchange(
        "/v1/verifier-sources",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": previous,
            "registry": sources,
        },
    )
    assert status == 200, response
    command(
        "install-policy",
        {
            "policy": {
                "goal_verifier": {
                    "verifier": goal_source["verifier"],
                    "predicate": goal_source["predicates"][0],
                    "max_evidence_age_ms": 10000,
                },
                "goal_context": {"original_source": scenario.manifest["artifact"]},
                "policy_id": uuid.uuid4().hex,
                "proposer": "independent-file-reader",
                "lifetime_ms": 180000,
                "candidate_limit": 4,
                "candidate_timeout_ms": 30000,
                "candidate_bytes": 65536,
                "switch_limit": 2,
                "switch_timeout_ms": 30000,
                "automatic_triggers": ["local-execution-failed"] if case == "revision-mi" else [],
            }
        },
    )
    automatic_switch = None
    if case == "revision-mi":
        command("authorize-generation", {}, schema="roboguide.runtime-plan-revision/v0.2")
        # Terminate only the real child whose original PID was read from the actual owned ledger.
        # The backend's independent native wait, rather than this signal, supplies stop evidence.
        os.kill(pid, signal.SIGTERM)
        automatic_switch = wait_for(
            lambda: next(
                (row for row in view()["authority"]["switches"] if row["phase"] == "generating"),
                None,
            ),
            "original failed process reaches the actual automatic MI hold",
            30,
        )
        ticket, target = automatic_switch["ticket_id"], automatic_switch["target_group"]
    else:
        ticket, target = uuid.uuid4().hex, "revision-" + uuid.uuid4().hex
        command("request-candidate", {"ticket_id": ticket}, proposer=True)
    child = copy.deepcopy(scenario.manifest)
    child["selector"]["revision_id"] = uuid.uuid4().hex
    child["scope"] = {"kind": "execution_group", "execution_group_id": target}
    child["created_at"] = time.time_ns() // 1_000_000
    registry = scenario.get("/v1/memory-authorizations")["registry"]
    previous = registry["revision"]
    registry["revision"] += 1
    registry["policies"]["/".join(scenario.manifest["selector"].values())]["principals"][
        scenario.administrator
    ] = ["handoff"]
    registry["policies"]["/".join(child["selector"].values())] = {
        "policy_id": uuid.uuid4().hex,
        "selector": child["selector"],
        "manifest_digest": digest(child),
        "tenant_id": "local",
        "principals": {"independent-file-reader": ["discover"]},
        "groups": [
            {
                "group_id": target,
                "operations": ["read", "import"],
                "providers": [
                    {
                        "provider_id": "files",
                        "local_system_id": "posix-compute",
                        "kind": child["kind"],
                        "payload_schema": child["payload_schema"],
                        "media_type": child["media_type"],
                    }
                ],
            }
        ],
        "revoked": False,
    }
    status, response = scenario.exchange(
        "/v1/memory-authorizations",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": previous,
            "registry": registry,
        },
    )
    assert status == 200, response
    declaration = {
        "ticket_id": ticket,
        "authorization": {
            "parent_selector": scenario.manifest["selector"],
            "target_group_id": target,
            "child": child,
            "registry_id": registry["registry_id"],
            "registry_revision": registry["revision"],
        },
    }
    declaration_schema = "roboguide.runtime-plan-revision/v0.3"
    if scenario.typed_map is not None:
        declaration_schema = "roboguide.runtime-plan-revision/v0.4"
        declaration["authorization"]["typed_map"] = scenario.typed_map
    denied = {
        "schema_version": declaration_schema,
        "command_id": uuid.uuid4().hex,
        "expected_revision": view()["authority"]["revision"],
        "action": {"kind": "authorize-memory", **declaration},
    }
    status, denial = scenario.exchange(revision_path, denied, context=scenario.verifier_context)
    assert status == 409, denial
    older = copy.deepcopy(denied)
    older["schema_version"] = "roboguide.runtime-plan-revision/v0.2"
    older["command_id"] = uuid.uuid4().hex
    status, denial_v2 = scenario.exchange(revision_path, older)
    assert status == 409, denial_v2
    if scenario.typed_map is not None:
        malformed = copy.deepcopy(denied)
        malformed["command_id"] = uuid.uuid4().hex
        malformed["schema_version"] = "roboguide.runtime-plan-revision/v0.3"
        assert scenario.exchange(revision_path, malformed)[0] == 409
        malformed["command_id"] = uuid.uuid4().hex
        malformed["schema_version"] = declaration_schema
        malformed["action"]["authorization"]["typed_map"]["root_frame"] += "-changed"
        assert scenario.exchange(revision_path, malformed)[0] == 409
        malformed = copy.deepcopy(denied)
        malformed["command_id"] = uuid.uuid4().hex
        del malformed["action"]["authorization"]["typed_map"]
        assert scenario.exchange(revision_path, malformed)[0] == 409
    declaration_command, declared = command(
        "authorize-memory", declaration, schema=declaration_schema
    )
    assert scenario.exchange(revision_path, declaration_command) == (200, declared)
    staged = checkpoint(scenario.directory)
    assert new_computation_count() == 0
    authority = staged["runtime_revisions"][mission]
    assert authority["candidates"][ticket]["memory"]
    assert all(
        group["group_id"] != target
        for group in json.loads(staged["integration_json"])["control"]["groups"]
    )
    cas = "https://" + scenario.controller_command[4]
    selector = child["selector"]
    child_path = f"/v1/memories/{selector['memory_id']}/revisions/{selector['revision_id']}"
    assert scenario.exchange(child_path, context=scenario.verifier_context, endpoint=cas)[0] == 404
    save(
        scenario.directory / "revision-staged-only.json",
        {"authority": authority, "source_attempt": source_attempt},
    )

    candidate = copy.deepcopy(original)
    candidate["tasks"][0]["id"] = "revision-" + uuid.uuid4().hex
    role = candidate["tasks"][0]["roles"][0]
    role["requirements"]["memories"] = [
        {
            "selector": child["selector"],
            "kind": child["kind"],
            "payload_schema": child["payload_schema"],
            "media_type": child["media_type"],
        }
    ]
    role["execution_intent"]["parameters"]["rounds"] = 1
    follow_up = copy.deepcopy(candidate["tasks"][0])
    follow_up["id"] = "revision-follow-up-" + uuid.uuid4().hex
    follow_up["depends_on"] = [candidate["tasks"][0]["id"]]
    candidate["tasks"].append(follow_up)
    save(scenario.directory / "revision-candidate.json", candidate)
    mi_result = None
    if case == "revision-mi":
        mi_result = generate(scenario, view, ticket)
        candidate = view()["current_plan"]
        save(scenario.directory / "revision-candidate.json", candidate)
    else:
        _, output = command(
            "submit-candidate",
            {
                "ticket_id": ticket,
                "plan": candidate,
                "provenance": {"kind": "operator", "principal": "independent-file-reader"},
            },
            proposer=True,
        )
        assert output["status"] == "awaiting_review", output
        command(
            "review-candidate",
            {
                "ticket_id": ticket,
                "plan_digest": output["plan_digest"],
                "decision": "approve",
                "reason": "Exact original-owner child and independently checked file goal",
            },
        )
    mutation = copy.deepcopy(declaration_command)
    mutation.update(command_id=uuid.uuid4().hex, expected_revision=view()["authority"]["revision"])
    status, late = scenario.exchange(revision_path, mutation)
    assert status == 409, late
    if automatic_switch is None:
        switch_command, _ = command(
            "request-switch", {"ticket_id": ticket, "target_group_id": target}
        )
        switch_id = switch_command["command_id"]
    else:
        switch_id = automatic_switch["command_id"]
    ready = wait_for(
        lambda: (
            row
            if (row := view()["authority"]["switches"][-1])["phase"]
            == ("applied" if automatic_switch else "ready")
            else None
        ),
        "actual original process exit and complete original content revocation",
        30,
    )
    stopped = checkpoint(scenario.directory)
    purpose = stopped["safety_stops"][source_attempt["execution_id"]]
    assert purpose["record"]["phase"] == "stop_confirmed"
    assert purpose["safe_proof"]["observation"]["process_exit"]["pid"] == pid
    assert not Path(f"/proc/{pid}").exists()
    assert (
        stopped["memory_preparation"]["records"][source_attempt["execution_id"]]["phase"]
        == "revoked"
    )
    assert stopped["memory_use"]["records"][source_attempt["execution_id"]]["revoked"] is True
    save(
        scenario.directory / "revision-original-stop-revoke.json",
        {
            "stop": purpose,
            "switch": ready,
            "preparation": stopped["memory_preparation"]["records"][source_attempt["execution_id"]],
            "use": stopped["memory_use"]["records"][source_attempt["execution_id"]],
        },
    )

    if case == "revision-restart":
        stop(scenario.controller)
        scenario.controller = scenario.start(
            scenario.controller_command, "revision-controller-before-commit-restored"
        )
        wait_for(
            lambda: scenario.get("/healthz").get("status") == "ok",
            "original revision authority restored",
        )
        restored = checkpoint(scenario.directory)
        assert restored["runtime_revisions"][mission] == stopped["runtime_revisions"][mission]
        assert restored["safety_stops"][source_attempt["execution_id"]] == purpose
        save(
            scenario.directory / "revision-original-stop-restored.json",
            restored["runtime_revisions"][mission],
        )

    if case == "revision-policy":
        registry["revision"] += 1
        registry["policies"]["/".join(child["selector"].values())]["revoked"] = True
        status, response = scenario.exchange(
            "/v1/memory-authorizations",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": registry["revision"] - 1,
                "registry": registry,
            },
        )
        assert status == 200, response
        body = {
            "schema_version": "roboguide.runtime-plan-revision/v0.3",
            "command_id": uuid.uuid4().hex,
            "expected_revision": view()["authority"]["revision"],
            "action": {"kind": "commit-switch", "switch_command_id": switch_id},
        }
        status, response = scenario.exchange(revision_path, body)
        assert status == 409, response
        assert view()["current_plan_revision"] == 0
        assert (
            scenario.exchange(child_path, context=scenario.verifier_context, endpoint=cas)[0] == 404
        )
        save(
            scenario.directory / "revision-policy-fence.json",
            {"status": status, "response": response, "view": view()},
        )
        new_count = new_computation_count()
        assert new_count == 0
        return {
            "passed": True,
            "case": case,
            "ownership_retained": True,
            "new_execute_count": new_count,
            "software_nodes": 2,
            "devices": 0,
        }

    if case == "revision-expiry":
        wait_for(
            lambda: view()["authority"]["candidates"][0]["remaining_work_ms"] == 0,
            "original declaration/candidate clock expires without renewal",
            35,
        )
        body = {
            "schema_version": "roboguide.runtime-plan-revision/v0.3",
            "command_id": uuid.uuid4().hex,
            "expected_revision": view()["authority"]["revision"],
            "action": {"kind": "commit-switch", "switch_command_id": switch_id},
        }
        status, response = scenario.exchange(revision_path, body)
        assert status == 409, response
        assert view()["current_plan_revision"] == 0
        save(
            scenario.directory / "revision-original-expiry.json",
            {"status": status, "response": response, "view": view()},
        )
        new_count = new_computation_count()
        assert new_count == 0
        return {
            "passed": True,
            "case": case,
            "ownership_retained": True,
            "new_execute_count": new_count,
            "software_nodes": 2,
            "devices": 0,
        }

    if case == "revision-sql":
        with quiesce_node_publishers(scenario, "revision-publication"):
            before = checkpoint(scenario.directory)
            with (
                closing(sqlite3.connect(scenario.directory / "controller.sqlite3")) as connection,
                connection,
            ):
                connection.execute("""CREATE TRIGGER fail_revision_memory BEFORE INSERT ON events
                    WHEN json_type(NEW.payload_json,'$.MemoryManifestPublished') IS NOT NULL
                    BEGIN SELECT RAISE(ABORT,'actual revision child publication rollback'); END""")
            body = {
                "schema_version": "roboguide.runtime-plan-revision/v0.3",
                "command_id": uuid.uuid4().hex,
                "expected_revision": view()["authority"]["revision"],
                "action": {"kind": "commit-switch", "switch_command_id": switch_id},
            }
            status, failure = scenario.exchange(revision_path, body)
            assert status in (409, 503), failure
            after = checkpoint(scenario.directory)
            assert after["runtime_revisions"][mission] == before["runtime_revisions"][mission]
            assert new_computation_count() == 0
            assert (
                json.loads(after["integration_json"])["control"]["reservations"]
                == json.loads(before["integration_json"])["control"]["reservations"]
            )
            unavailable = scenario.exchange(
                child_path, context=scenario.verifier_context, endpoint=cas
            )
            assert unavailable[0] in (404, 503), unavailable
            with (
                closing(sqlite3.connect(scenario.directory / "controller.sqlite3")) as connection,
                connection,
            ):
                assert connection.execute(
                    "SELECT COUNT(*) FROM events WHERE "
                    "json_extract(payload_json,'$.MemoryManifestPublished.manifest.selector.revision_id')=?",
                    (child["selector"]["revision_id"],),
                ).fetchone() == (0,)
            with (
                closing(sqlite3.connect(scenario.directory / "controller.sqlite3")) as connection,
                connection,
            ):
                connection.execute("DROP TRIGGER fail_revision_memory")
            if scenario.get("/v1/authority-status")["write_enabled"] is False:
                recover_original_writer(scenario, "revision-publication")
            assert (
                scenario.exchange(child_path, context=scenario.verifier_context, endpoint=cas)[0]
                == 404
            )
            save(
                scenario.directory / "revision-publication-rollback.json",
                {"status": status, "response": failure},
            )

    if automatic_switch is None:
        commit_command, committed = command("commit-switch", {"switch_command_id": switch_id})
        assert committed["plan_revision"] == 1 and committed["target_group_id"] == target
        assert scenario.exchange(revision_path, commit_command) == (200, committed)
    else:
        assert view()["current_plan_revision"] == 1 and view()["current_group_id"] == target
    assert (
        scenario.exchange(child_path, context=scenario.verifier_context, endpoint=cas)[1][
            "manifest"
        ]
        == child
    )
    checks = []
    attempts = []
    for index, task in enumerate(candidate["tasks"]):
        attempt = wait_for(
            lambda task=task: next(
                (
                    row
                    for row in scenario.get("/v1/execution-attempts")["attempts"]
                    if row["group_id"] == target
                    and row["task_id"] == task["id"]
                    and row["status"] == "Completed"
                ),
                None,
            ),
            "actual new Task computes exact original child bytes",
            30,
        )
        status, computation = request(
            scenario.backends[0],
            "/v1/file-jobs/status",
            {"execution_id": attempt["execution_id"]},
            method="GET",
        )
        assert status == 200 and computation["state"] == "COMPLETED", computation
        save(
            scenario.directory / f"revision-computation-{index}.json",
            {"attempt": attempt, "computation": computation},
        )
        independent = subprocess.run(
            [sys.executable, str(Path(__file__)), "--verify", str(scenario.directory), str(index)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        verified = json.loads(independent.stdout)
        assert verified["passed"] is True
        checks.append(verified)
        attempts.append(attempt)
        save(scenario.directory / f"revision-independent-verifier-{index}.json", verified)
        verdict = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": "independent-file-reader",
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": mission, "task_id": task["id"]},
            "plan_digest": scenario.get(path + "/tenant")["source_plan_digest"],
            "attempts": [{"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}],
            "predicate": task["satisfaction"]["verifier"]["predicate"],
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 10000,
            "sequence": 1,
            "artifact_digest": scenario.manifest["artifact"]["content_digest"]
            if scenario.typed_map is not None
            else None,
            "artifact_size_bytes": scenario.manifest["artifact"]["byte_size"]
            if scenario.typed_map is not None
            else None,
        }
        wrong_identity = copy.deepcopy(verdict)
        wrong_identity["plan_digest"] = view()["current_plan_digest"]
        status, rejected_identity = scenario.exchange(
            "/v1/verifier-evidence", wrong_identity, context=scenario.verifier_context
        )
        assert status == 409, rejected_identity
        save(
            scenario.directory / f"revision-wrong-verifier-digest-{index}.json",
            {
                "status": status,
                "response": rejected_identity,
                "published_plan_digest": wrong_identity["plan_digest"],
                "normalized_verifier_plan_digest": verdict["plan_digest"],
            },
        )
        status, receipt = scenario.exchange(
            "/v1/verifier-evidence", verdict, context=scenario.verifier_context
        )
        assert status == 200, receipt
    wait_for(
        lambda: (
            view()["goal_contract"]
            and all(
                row["status"] == "Completed"
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["group_id"] == target
            )
        ),
        "all actual new local computations complete",
    )
    current = view()
    assert scenario.get(path)["status"] != "Completed"
    goal: dict[str, Any] = {
        "schema_version": "roboguide.mission-goal-evidence/v0.1",
        "evidence_id": uuid.uuid4().hex,
        "mission_id": mission,
        "tenant_id": "local",
        "contract_digest": current["goal_contract"]["digest"],
        "source_id": goal_source["source_id"],
        "source_revision": 1,
        "plan_revision": 1,
        "group_id": target,
        "plan_digest": current["current_plan_digest"],
        "attempts": [
            {
                "task_ref": {"mission_id": mission, "task_id": row["task_id"]},
                "role_id": row["role_id"],
                "attempt_id": row["execution_id"],
            }
            for row in attempts
        ],
        "predicate": goal_source["predicates"][0],
        "verdict": "satisfied",
        "finality": "final",
        "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
        "sequence": 1,
        "valid_for_ms": 10000,
        "artifact_digest": None,
        "artifact_size_bytes": None,
    }
    status, denied_goal = scenario.exchange(
        "/v1/mission-goal-evidence", goal, context=scenario.verifier_context
    )
    assert status == 409, denied_goal
    status, goal_receipt = scenario.exchange(
        "/v1/mission-goal-evidence", goal, context=client(scenario.directory / "pki", "admin-next")
    )
    assert status == 200, goal_receipt
    wait_for(
        lambda: scenario.get(path)["status"] == "Completed",
        "independently verified original Mission goal",
    )
    final = checkpoint(scenario.directory)
    with (
        closing(
            sqlite3.connect(
                f"file:{scenario.directory / scenario.worker_names[0] / 'backend/memory.sqlite3'}"
                "?mode=ro",
                uri=True,
            )
        ) as connection,
        connection,
    ):
        originals = [
            (row[0], json.loads(row[1])["original_invocation"])
            for row in connection.execute("SELECT import_id,original_json FROM imports")
        ]
    receivers = [
        {
            "import_id": identifier,
            "execution_id": invocation["execution_id"],
            "preparation_id": invocation["memory_preparation_id"],
        }
        for identifier, invocation in originals
        if invocation["group_id"] == target
    ]
    assert len(receivers) == 2 and len({row["import_id"] for row in receivers}) == 2
    assert len({row["preparation_id"] for row in receivers}) == 2
    assert {row["execution_id"] for row in receivers} == {row["execution_id"] for row in attempts}
    save(scenario.directory / "revision-original-receivers.json", receivers)
    save(scenario.directory / "revision-final-authority.json", final)
    stop(scenario.controller)
    final = checkpoint(scenario.directory)
    save(scenario.directory / "revision-final-sealed-authority.json", final)
    old_result = None
    if old_controller is not None:
        original_arguments = [str(old_controller.resolve()), *scenario.controller_command[1:]]
        old = scenario.start(original_arguments, "revision-old-controller-refused")
        wait_for(
            lambda: old.poll() is not None, "actual archived old writer refuses new authority", 15
        )
        assert old.returncode != 0
        unchanged = checkpoint(scenario.directory)
        assert unchanged == final
        old_result = {
            "sha256": hashlib.sha256(old_controller.read_bytes()).hexdigest(),
            "exit_code": old.returncode,
        }
    scenario.controller = scenario.start(
        scenario.controller_command, "revision-controller-final-restored"
    )
    wait_for(
        lambda: scenario.get("/healthz").get("status") == "ok",
        "final original revision authority restored",
    )
    restored = checkpoint(scenario.directory)
    assert restored["runtime_revisions"][mission] == final["runtime_revisions"][mission]
    assert scenario.get(path)["status"] == "Completed"
    new_count = new_computation_count()
    assert new_count == len(checks) == 2
    return {
        "passed": True,
        "case": case,
        "software_nodes": len(scenario.nodes),
        "executing_nodes": [scenario.nodes[0]],
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "original_pid": pid,
        "independent_verifiers": checks,
        "original_receivers": receivers,
        "old_writer": old_result,
        "original_goal_verified": True,
        "new_execute_count": new_count,
        "source_auto_resume": False,
        "restored_original_budgets": True,
        "runtime_mi": mi_result,
        "complete_typed_map_source": scenario.typed_map,
    }


def main() -> None:
    """Run the requested independent verifier; deployment uses the owning checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", nargs=2, required=True, metavar=("OUTPUT", "INDEX"))
    arguments = parser.parse_args()
    print(json.dumps(verify(Path(arguments.verify[0]), int(arguments.verify[1])), sort_keys=True))


if __name__ == "__main__":
    main()
