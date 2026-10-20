"""Drive actual runtime MI worker interruption and reply-loss conformance with declared fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, stop, wait_for
from runtime_mi_process_profile import ProviderFixtureServer


def original_rows(path: Path) -> dict[str, Any]:
    """Read committed original budgets, calls and independent observations."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        work = connection.execute("SELECT work_id,kernel_json,duration_ms FROM work").fetchall()
        effects = connection.execute(
            "SELECT phase,status,request_json,response_json,nonce FROM effects ORDER BY phase"
        ).fetchall()
        observations = connection.execute(
            "SELECT phase,proof_json,result_json FROM observations"
        ).fetchall()
    return {"work": work, "effects": effects, "observations": observations}


def copy_sql(source: Path, target: Path) -> None:
    """Copy an actual committed snapshot without editing, inventing or renewing any original row."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(target) as destination:
            original.backup(destination)


def exercise_worker(
    directory: Path,
    controller: str,
    service: str,
    request_id: str,
    provider: ProviderFixtureServer,
    start: Callable[[list[str], str, dict[str, str] | None], subprocess.Popen[bytes]],
    view: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Interrupt and resume actual workers without repeating original calls."""
    certificates = directory / "pki"
    fixture_endpoint = f"http://127.0.0.1:{provider.server_port}"
    mission_configuration = directory / "runtime-mi-mission.toml"
    mission_configuration.write_text(f"""
[mission]
planner = "llm"
contract_version = "roboguide.mission-plan/v0.8"
schema_path = "contracts/mission/v0.8/mission-plan.schema.json"
capability_catalog_path = {json.dumps(str(directory / "software-conformance-catalog.json"))}
review_enabled = true
max_repair_attempts = 0
prevalidation_recovery_attempts = 0
[mission.prompts]
version = "v0"
interpreter_path = "mission/prompts/v0/interpreter.md"
planner_path = "mission/prompts/v0/planner.md"
reviewer_path = "mission/prompts/v0/reviewer.md"
repairer_path = "mission/prompts/v0/repairer.md"
[mission.llm]
model_provider = "ProtocolFixture"
model = "explicit-protocol-fixture-no-inference"
review_model = "explicit-protocol-fixture-no-inference"
model_reasoning_effort = "low"
disable_response_storage = true
network_access = "enabled"
max_output_tokens = 4096
timeout_seconds = 20
[model_providers.ProtocolFixture]
name = "Explicit conformance fixture, not model inference"
base_url = {json.dumps(fixture_endpoint)}
wire_api = "responses"
responses_path = "/responses"
requires_openai_auth = false
api_key_env = "ROBOGUIDE_PROTOCOL_FIXTURE_UNUSED_CREDENTIAL"
""")
    database = directory / "runtime-mi.sqlite3"
    profile = directory / "runtime-mi-worker.toml"
    sections = [
        'schema_version = "roboguide.runtime-mi-worker/v0.1"',
        "state_db = " + json.dumps(str(database)),
        "mission_config = " + json.dumps(str(mission_configuration)),
        "request_ids = " + json.dumps([request_id]),
        "poll_ms = 1000",
    ]
    for name, endpoint, leaf, principal in (
        ("service", service, "viewer", "revision-proposer"),
        ("proposer", controller, "viewer", "revision-proposer"),
        ("reviewer", controller, "admin", "revision-administrator"),
    ):
        sections.append("[" + name + "]")
        for key, value in {
            "endpoint": endpoint,
            "tenant": "local",
            "principal": principal,
            "certificate": str(certificates / (leaf + ".crt")),
            "key": str(certificates / (leaf + ".key")),
            "ca": str(certificates / "ca.crt"),
        }.items():
            sections.append(key + " = " + json.dumps(value))
    profile.write_text("\n".join(sections) + "\n")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP"] = "1"
    helper = Path(__file__).with_name("runtime_mi_process_profile.py")
    command = [sys.executable, str(helper), "worker", "--profile", str(profile)]
    pause = directory / "runtime-mi-provider-receipt-committed.json"
    earlier = directory / "runtime-mi-before-provider.sqlite3"
    first = start(
        [
            *command,
            "--pause-after-generation",
            str(pause),
            "--snapshot-before-generation",
            str(earlier),
        ],
        "runtime-mi-paused",
        environment,
    )
    wait_for(
        pause.is_file, "actual provider HTTP result and SQL receipt before worker interruption"
    )
    before = original_rows(database)
    save(directory / "runtime-mi-before-interruption.json", before)
    assert len(provider.calls) == 1 and len(before["work"]) == 1
    generation = next(row for row in before["effects"] if row[0] == "provider-generate")
    assert generation[1] == "completed"
    assert all(
        candidate["output_received"] is False
        for candidate in view()["authority"]["candidates"]
        if candidate["ticket_id"].startswith("work-")
    )
    stop(first)
    complete = directory / "runtime-mi-after-provider.sqlite3"
    copy_sql(database, complete)
    copy_sql(earlier, database)
    regressed = start(command, "runtime-mi-regressed-snapshot-rejected", environment)
    regressed.wait(timeout=20)
    assert regressed.returncode != 0
    regression = original_rows(database)
    save(directory / "runtime-mi-regressed-snapshot.json", regression)
    permit = next(row for row in regression["effects"] if row[0] == "permit-provider-generate")
    assert json.loads(permit[3])["status_code"] == 409
    assert (
        next(row for row in regression["effects"] if row[0] == "provider-generate")[4]
        != generation[4]
    )
    assert len(provider.calls) == 1
    copy_sql(database, directory / "runtime-mi-regressed-copy-retained.sqlite3")
    copy_sql(complete, database)
    lost = directory / "runtime-mi-actual-reply-discarded.json"
    second = start(
        [*command, "--lose-candidate-response", str(lost)], "runtime-mi-reply-lost", environment
    )
    second.wait(timeout=20)
    assert second.returncode != 0 and lost.is_file()
    assert json.loads(lost.read_text())["actual_reply"][0] == 200
    after_lost = original_rows(database)
    save(directory / "runtime-mi-after-reply-loss.json", after_lost)
    assert len(provider.calls) == 1
    assert any(row[0] == "candidate" and row[1] == "uncertain" for row in after_lost["effects"])
    assert view()["current_plan_revision"] == 1
    third = start(command, "runtime-mi-resumed", environment)
    third.wait(timeout=20)
    assert third.returncode == 0
    after = original_rows(database)
    save(directory / "runtime-mi-after-observation-and-review.json", after)
    assert after["work"] == before["work"]
    assert len(provider.calls) == 2
    assert any(row[0] == "candidate" and row[1] == "uncertain" for row in after["effects"])
    assert len(after["observations"]) == 1 and after["observations"][0][0] == "candidate"
    for call in provider.calls:
        logical = json.loads(call["input"])["runtime_replanning_context"]
        assert not any(
            name in json.dumps(logical) for name in ('"node_id"', '"session_id"', '"attempt_id"')
        )
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        configured = json.loads(connection.execute("SELECT identity_json FROM work").fetchone()[0])
    evidence = directory / "runtime-mi-after-observation-and-review.json"
    return {
        "passed": True,
        "actual_worker_exit_codes": [
            first.returncode,
            regressed.returncode,
            second.returncode,
            third.returncode,
        ],
        "actual_request_service": True,
        "actual_mtls_controller_commands": True,
        "original_provider_result_reused": True,
        "original_work_budget_preserved": True,
        "uncertain_http_outcome_retained": True,
        "separate_readonly_observation": True,
        "older_sql_snapshot_cannot_repeat_provider": True,
        "provider_protocol_fixture_calls": 2,
        "external_model_calls": 0,
        "devices": 0,
        "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "original_mi_identity": configured,
        "original_sql_evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "provider_wire_history_sha256": hashlib.sha256(
            (directory / "provider-protocol-fixture-calls.json").read_bytes()
        ).hexdigest(),
    }
