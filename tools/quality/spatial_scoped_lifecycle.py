"""Compose actual map use, stopped Group handoff, transitive revision and local GC."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import time
import uuid
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, wait_for
from memory_group_lifecycle import GroupScenario
from memory_handoff_processes import checkpoint
from memory_use_profile import request
from spatial_content_policy import digest
from spatial_memory_consumer import SpatialMemoryConsumer


def active_map(scenario: GroupScenario, attempt: dict[str, Any], label: str) -> dict[str, Any]:
    """Select/query exact original map bytes under this Task's actual current receiving lease."""
    typed = scenario.typed_map
    assert typed is not None
    preparation = scenario.get("/v1/memory/preparations")["records"][attempt["execution_id"]][
        "preparation"
    ]
    item = preparation["inputs"][0]
    if item["manifest"]["scope"]["kind"] == "execution_group":
        assert preparation["schema"] == "roboguide.memory-preparation/v0.3"
        assert item["requirement"]["source_contract"] == "scoped_typed_map"
    assert item["typed_map"] == typed and preparation["execution_id"] == attempt["execution_id"]
    uses = scenario.get("/v1/memory/use-leases")["records"][attempt["execution_id"]]
    grant = max(
        (
            row
            for row in uses["controls"].values()
            if row["command"]["operation"] == "grant"
            and (row.get("receipt") or {}).get("status") == "granted"
        ),
        key=lambda row: row["command"]["generation"],
    )
    status, state = request(scenario.backends[0], "/v1/active-map/state")
    assert status == 200
    selector = typed["map_id"] + ":" + typed["revision_id"]
    previous = next((head for head in state["heads"] if head["selector"] == selector), None)
    purpose = {
        "schema": "roboguide.active-map/v0.1",
        "map_id": typed["map_id"],
        "revision_id": typed["revision_id"],
        "content_digest": typed["content_digest"],
        "byte_size": typed["byte_size"],
        "frame_id": typed["root_frame"],
        "spatial_anchor_id": typed["spatial_anchor"]["id"],
        "attempt_id": attempt["execution_id"],
        "execution_id": attempt["execution_id"],
        "use_lease_id": grant["command"]["lease_id"],
        "generation": 1 if previous is None else previous["generation"] + 1,
        "node_id": attempt["node_id"],
        "group_id": attempt["group_id"],
    }
    status, selection = request(scenario.backends[0], "/v1/active-map/select", purpose)
    assert status == 200 and selection["state"] == "active", selection
    query = purpose | {
        "query_id": "scoped-query-" + uuid.uuid4().hex,
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "radius": 1000.0,
    }
    status, observed = request(scenario.backends[0], "/v1/active-map/query", query)
    assert status == 200 and observed["localization"] is None, observed
    actual = scenario.directory / "consumer/backend/content" / typed["content_digest"][7:]
    with actual.open("rb") as stream:
        assert (
            "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest() == typed["content_digest"]
        )
    with zipfile.ZipFile(actual) as bundle:
        tile_manifest = json.loads(bundle.read("manifest.json"))
        tile_digests = {tile["content_digest"] for tile in tile_manifest["tiles"]}
        assert {hit["tile_digest"] for hit in observed["hits"]} <= tile_digests
    forged = query | {
        "query_id": "forged-frame-" + uuid.uuid4().hex,
        "frame_id": typed["root_frame"] + "-other",
    }
    denied, _ = request(scenario.backends[0], "/v1/active-map/query", forged)
    assert denied != 200
    result = {
        "purpose": purpose,
        "selection": selection,
        "query": observed,
        "preparation": preparation,
        "original_typed_root": typed,
        "forged_frame_status": denied,
    }
    save(scenario.directory / (label + "-active-map.json"), result)
    return result


def reclamation(scenario: GroupScenario, *, expected: str, label: str) -> dict[str, Any]:
    """Use a distinct original operation over actual import/revoke/worker/reference coverage."""
    identity = request(scenario.backends[0], "/v1/instance")[1]
    command = {
        "schema": "roboguide.content-reclamation-command/v0.1",
        "command_id": "scoped-gc-" + uuid.uuid4().hex,
        "provider_id": identity["provider_id"],
        "provider_instance_id": identity["instance_id"],
        **scenario.manifest["artifact"],
    }
    status, receipt = request(scenario.backends[0], "/v1/content/tombstone", command)
    assert status == 200 and receipt["state"] == expected, receipt
    result = {"command": command, "tombstone": receipt}
    if expected == "tombstoned":
        status, deleted = request(scenario.backends[0], "/v1/content/collect", command)
        assert status == 200 and deleted["state"] == "deleted", deleted
        assert request(scenario.backends[0], "/v1/content/collect", command) == (200, deleted)
        result["deleted"] = deleted
        actual = scenario.directory / "consumer/backend/content" / command["content_digest"][7:]
        assert not actual.exists()
        with closing(
            sqlite3.connect(scenario.directory / "consumer/backend/memory.sqlite3")
        ) as sql:
            assert sql.execute(
                "SELECT state FROM content_tombstones WHERE content_digest=?",
                (command["content_digest"],),
            ).fetchone() == ("deleted",)
    save(scenario.directory / (label + ".json"), result)
    return result


def run(
    scenario: GroupScenario,
    mission: str,
    original: dict[str, Any],
    consumer: SpatialMemoryConsumer,
    *,
    runtime_mi: bool = False,
) -> dict[str, Any]:
    """Require actual consuming source, original PID wait/revoke and normal Control recipients."""
    from memory_revision_processes import run as revise

    typed = scenario.typed_map
    assert typed is not None and typed["source_mission_id"] != mission
    source = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == mission and row["status"] == "Running"
            ),
            None,
        ),
        "actual consuming map worker running",
    )
    with closing(sqlite3.connect(scenario.directory / "consumer/backend/file-jobs.sqlite3")) as sql:
        pid, instance = sql.execute(
            "SELECT pid,instance_id FROM original_processes WHERE execution_id=?",
            (source["execution_id"],),
        ).fetchone()
    assert Path(f"/proc/{pid}/stat").is_file()
    source_active = active_map(scenario, source, "scoped-original-consumer")
    blocked = reclamation(scenario, expected="blocked", label="scoped-live-gc-blocked")
    target_id = "mission-scoped-handoff-" + uuid.uuid4().hex
    target_group = "group-" + target_id
    child = copy.deepcopy(scenario.manifest)
    child.update(
        selector=child["selector"] | {"revision_id": uuid.uuid4().hex},
        scope={"kind": "execution_group", "execution_group_id": target_group},
        created_at=time.time_ns() // 1_000_000,
    )
    plan = copy.deepcopy(original)
    plan["mission"]["id"] = target_id
    plan["schema_version"] = "roboguide.mission-plan/v0.11"
    role = plan["tasks"][0]["roles"][0]
    role["requirements"]["memories"] = [
        {key: child[key] for key in ("selector", "kind", "payload_schema", "media_type")}
        | {"source_contract": "scoped_typed_map"}
    ]
    save(scenario.directory / "scoped-target-plan.json", plan)
    if runtime_mi:
        import ssl

        from check_identity_processes import client
        from memory_runtime_mi_processes import submit_original

        certificates = scenario.directory / "pki"
        contexts = {name: client(certificates, name) for name in ("admin", "viewer")}
        leaves = {
            name: hashlib.sha256(
                ssl.PEM_cert_to_DER_cert((certificates / (name + ".crt")).read_text())
            ).hexdigest()
            for name in contexts
        }
        submit_original(
            scenario.directory,
            target_id,
            plan,
            "https://" + scenario.controller_command[3],
            contexts,
            leaves,
            scenario.start,
        )
    else:
        status, receipt = scenario.exchange("/v1/missions", plan)
        assert status == 202, receipt
    target_attempt = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == target_id
            ),
            None,
        ),
        "actual compatible receiving Group bound before handoff",
    )
    assert target_attempt["node_id"] == consumer.node
    registry = scenario.get("/v1/memory-authorizations")["registry"]
    previous = registry["revision"]
    registry["revision"] += 1
    root_key = typed["map_id"] + "/" + typed["revision_id"]
    registry["policies"][root_key]["principals"][scenario.administrator] = ["handoff"]
    registry["policies"]["/".join(child["selector"].values())] = {
        "policy_id": "scoped-child-" + uuid.uuid4().hex,
        "selector": child["selector"],
        "manifest_digest": digest(child),
        "tenant_id": "local",
        "principals": {
            "independent-file-reader": ["discover"],
            scenario.administrator: ["handoff"],
        },
        "groups": [
            {
                "group_id": target_group,
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
    status, installed = scenario.exchange(
        "/v1/memory-authorizations",
        {"command_id": uuid.uuid4().hex, "expected_revision": previous, "registry": registry},
    )
    assert status == 200, installed
    groups = json.loads(checkpoint(scenario.directory)["integration_json"])["control"]["groups"]
    source_group = next(group for group in groups if group["group_id"] == source["group_id"])
    target = next(group for group in groups if group["group_id"] == target_group)
    command = {
        "schema_version": "roboguide.memory-handoff/v0.3",
        "handoff_id": "scoped-" + uuid.uuid4().hex,
        "parent_selector": scenario.manifest["selector"],
        "child": child,
        "typed_map": typed,
        "source_group_id": source["group_id"],
        "source_membership": source_group["membership_version"],
        "source_execution_ids": [source["execution_id"]],
        "target_membership": target["membership_version"],
        "registry_id": registry["registry_id"],
        "registry_revision": registry["revision"],
        "deadline_ms": time.time_ns() // 1_000_000 + 90000,
    }
    for label in ("old-producer-only", "forged-root", "forged-scope", "old-policy", "expired"):
        invalid = copy.deepcopy(command)
        invalid["handoff_id"] = uuid.uuid4().hex
        if label == "old-producer-only":
            invalid["schema_version"] = "roboguide.memory-handoff/v0.2"
        elif label == "forged-root":
            invalid["typed_map"]["root_frame"] += "-substituted"
        elif label == "forged-scope":
            invalid["child"]["scope"]["execution_group_id"] += "-other"
        elif label == "old-policy":
            invalid["registry_revision"] = previous
        else:
            invalid["deadline_ms"] = time.time_ns() // 1_000_000
        status, rejected = scenario.exchange("/v1/memory/handoffs", invalid)
        assert status == 409, rejected
        save(
            scenario.directory / ("scoped-" + label + ".json"),
            {"input": invalid, "status": status, "response": rejected},
        )
    from memory_handoff_processes import quiesce_node_publishers, recover_original_writer

    with quiesce_node_publishers(scenario, "scoped-admission"):
        before = checkpoint(scenario.directory)
        with closing(sqlite3.connect(scenario.directory / "controller.sqlite3")) as sql, sql:
            sql.execute(
                "CREATE TRIGGER scoped_admission_failure BEFORE INSERT ON events "
                "WHEN json_extract(NEW.payload_json,'$.MemoryHandoffTransition.phase')='stopping' "
                "BEGIN SELECT RAISE(ABORT,'actual scoped handoff SQL rollback'); END"
            )
        status, failure = scenario.exchange("/v1/memory/handoffs", command)
        save(
            scenario.directory / "scoped-sql-admission-observed.json",
            {
                "status": status,
                "response": failure,
                "authority": scenario.get("/v1/authority-status"),
            },
        )
        assert status == 409, failure
        after = checkpoint(scenario.directory)
        assert after["memory_handoffs"] == before["memory_handoffs"]
        assert after["safety_stops"] == before["safety_stops"]
        assert (
            json.loads(after["integration_json"])["control"]["capacity_ledger"]
            == json.loads(before["integration_json"])["control"]["capacity_ledger"]
        )
        with closing(sqlite3.connect(scenario.directory / "controller.sqlite3")) as sql, sql:
            sql.execute("DROP TRIGGER scoped_admission_failure")
        recover_original_writer(scenario, "scoped-admission")
        save(
            scenario.directory / "scoped-actual-sql-rollback.json",
            {"status": status, "failure": failure, "source_stop_not_published": True},
        )
    status, accepted = scenario.exchange("/v1/memory/handoffs", command)
    assert status in {200, 202}, accepted
    assert scenario.exchange("/v1/memory/handoffs", command) == (status, accepted)
    save(scenario.directory / "scoped-handoff-command.json", command)

    def view() -> dict[str, Any]:
        """Observe only the original immutable handoff, without renewing its deadline or source."""
        return next(
            row["record"]
            for row in scenario.get("/v1/memory/handoffs")["records"]
            if row["handoff_id"] == command["handoff_id"]
        )

    ready = wait_for(
        lambda: value if (value := view())["phase"] == "ready" else None,
        "original map consumer stopped and revoked before scoped receiver readiness",
        35,
    )
    frozen = checkpoint(scenario.directory)
    stop = frozen["safety_stops"][source["execution_id"]]
    assert stop["record"]["phase"] == "stop_confirmed"
    assert stop["safe_proof"]["observation"]["process_exit"]["pid"] == pid
    assert not Path(f"/proc/{pid}").exists()
    assert frozen["memory_use"]["records"][source["execution_id"]]["revoked"] is True
    assert frozen["memory_preparation"]["records"][source["execution_id"]]["phase"] == "revoked"
    status, committed = scenario.exchange(
        "/v1/memory/handoffs/" + command["handoff_id"] + "/commit", {}
    )
    assert status == 202 and committed["record"]["phase"] == "committed", committed
    save(
        scenario.directory / "scoped-handoff-proof.json",
        {
            "source_pid": pid,
            "source_instance": instance,
            "stop": stop,
            "ready": ready,
            "committed": committed,
        },
    )
    target_attempt = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["mission_id"] == target_id and row["status"] == "Running"
            ),
            None,
        ),
        "actual scoped receiving map computation",
        25,
    )
    receiving_active = active_map(scenario, target_attempt, "scoped-handoff-receiver")
    denied, _ = request(
        scenario.backends[0],
        "/v1/active-map/query",
        source_active["purpose"]
        | {"query_id": "retired-original-query", "x": 0.0, "y": 0.0, "z": 0.0, "radius": 1000.0},
    )
    assert denied != 200
    status, cancelled = scenario.exchange("/v1/missions/" + mission + "/cancel", {})
    assert status in {200, 202}, cancelled
    wait_for(
        lambda: scenario.get("/v1/missions/" + mission)["status"] == "Cancelled",
        "original stopped source ownership released",
    )
    scenario.manifest = child
    save(scenario.directory / "manifest.json", child)
    save(scenario.directory / "handoff-source-mission.json", plan)
    revision = revise(scenario, target_id, "revision-mi" if runtime_mi else "revision-restart")
    assert revision["original_goal_verified"] and revision["new_execute_count"] == 2
    contexts = checkpoint(scenario.directory)["memory_preparation"]["records"]
    original_contexts = [
        record["preparation"]
        for record in contexts.values()
        if record.get("preparation") is not None
        and record["preparation"]["schema"] == "roboguide.memory-preparation/v0.3"
    ]
    assert (
        len(original_contexts) == 3
        and len({context["preparation_id"] for context in original_contexts}) == 3
    )
    for context in original_contexts:
        assert context["inputs"][0]["typed_map"] == typed
        assert context["inputs"][0]["manifest"]["source_mission_id"] == typed["source_mission_id"]
    collected = reclamation(scenario, expected="tombstoned", label="scoped-original-provider-gc")
    result = {
        "passed": True,
        "original_production_mission": typed["source_mission_id"],
        "source_consuming_mission": mission,
        "receiving_mission": target_id,
        "source_process_wait": stop["safe_proof"]["observation"]["process_exit"],
        "handoff": committed,
        "receiving_active_map": receiving_active,
        "transitive_revision": revision,
        "distinct_task_preparations": [context["preparation_id"] for context in original_contexts],
        "blocked_live_gc": blocked,
        "local_gc": collected,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    save(scenario.directory / "scoped-lifecycle-result.json", result)
    return result
