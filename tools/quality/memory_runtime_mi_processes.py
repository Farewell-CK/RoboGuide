"""Compose actual original Requests and runtime MI with scoped file Memory conformance."""

from __future__ import annotations

import json
import ssl
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_identity_processes import request as https
from check_state_verifier_processes import ROOT, address, save, wait_for
from memory_group_lifecycle import GroupScenario
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.models import MissionPlan
from runtime_mi_process_driver import exercise_worker
from runtime_mi_process_profile import ProviderFixtureServer


def submit_original(
    directory: Path,
    mission: str,
    plan: dict[str, Any],
    controller: str,
    contexts: dict[str, ssl.SSLContext],
    leaves: dict[str, str],
    start: Callable[..., subprocess.Popen[bytes]],
) -> dict[str, Any]:
    """Create the real authenticated Request before any Node executes its original accepted plan."""
    catalog = json.loads((ROOT / "contracts/capability/v0.3/catalog.json").read_bytes())
    role = plan["tasks"][0]["roles"][0]
    contracts = [item["contract"] for item in role["requirements"]["capabilities"]]
    for contract in contracts:
        catalog["capabilities"].append(
            {"contract": contract, "description": "Actual file CPU contract.", "attributes": []}
        )
    intent = role["execution_intent"]
    catalog["operations"].append(
        {
            "operation": intent["operation"],
            "description": "Actual owned immutable-file hashing; no physical capability.",
            "required_capabilities": contracts,
            "parameters": [
                {
                    "name": name,
                    "type": "integer" if type(value) is int else "string",
                    "required": True,
                    "description": "Original authored file-computation input.",
                }
                for name, value in intent["parameters"].items()
            ],
        }
    )
    catalog_path = directory / "software-conformance-catalog.json"
    save(catalog_path, catalog)
    CanonicalCapabilityCatalog.load(catalog_path).validate_plan(MissionPlan.from_json(plan))
    save(directory / "original-plan.json", plan)
    save(
        directory / "mission-authorization.json",
        {
            "schema_version": "roboguide.mission-authorization/v0.2",
            "profile_id": uuid.uuid4().hex,
            "revision": 1,
            "grants": {
                "sha256:" + leaves[name]: {
                    "principal_id": principal,
                    "role": role,
                    "tenant_id": "local",
                }
                for name, principal, role in (
                    ("admin", "original-file-request-author", "admin"),
                    ("viewer", "independent-file-reader", "viewer"),
                )
            },
        },
    )
    endpoint = address()
    origin = "https://" + endpoint
    start(
        [
            sys.executable,
            str(Path(__file__).with_name("runtime_mi_process_profile.py")),
            "service",
            "--directory",
            str(directory),
            "--address",
            endpoint,
            "--controller",
            controller,
            "--authored-mission-id",
            mission,
        ],
        "memory-original-request-service",
    )
    wait_for(
        lambda: https(origin, contexts["admin"], "/healthz")[0] == 200,
        "actual original file Mission Request service",
    )
    key = uuid.uuid4().hex
    status, record = https(
        origin,
        contexts["admin"],
        "/v1/mission-requests",
        {"instruction": plan["mission"]["objective"]},
        {"Idempotency-Key": key},
    )
    assert status == 201 and record["lifecycle"] == "Accepted", record
    assert record["mission_id"] == mission
    save(directory / "original-request.json", record)
    save(directory / "runtime-mi-service.json", {"origin": origin})
    # This uses the engine's existing injectable UUID generator only in the explicit fixture.
    # No archived/request SQL is manufactured after execution or written around its API.
    return record


def generate(
    scenario: GroupScenario, view: Callable[[], dict[str, Any]], ticket: str
) -> dict[str, Any]:
    """Exercise real worker interruption and reply loss with the original scoped declaration."""
    record = json.loads((scenario.directory / "original-request.json").read_bytes())
    origin = json.loads((scenario.directory / "runtime-mi-service.json").read_bytes())["origin"]
    candidate = next(row for row in view()["authority"]["candidates"] if row["ticket_id"] == ticket)
    original = candidate["memory_authorizations"]
    assert len(original) == 1 and not candidate["claim"]
    provider = ProviderFixtureServer(scenario.directory)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        result = exercise_worker(
            scenario.directory,
            "https://" + scenario.controller_command[3],
            origin,
            record["request_id"],
            provider,
            scenario.start,
            view,
            "independent-file-reader",
            scenario.administrator,
        )
        assert len(provider.calls) == 2
        for call in provider.calls:
            context = json.loads(call["input"])["runtime_replanning_context"]
            assert context["schema_version"] == "roboguide.runtime-replanning-context/v0.2"
            assert context["scoped_memory_authorizations"] == original
            assert context["mission_id"] == record["mission_id"]
        wait_for(
            lambda: view()["current_plan_revision"] == 1,
            "actual reviewed scoped Memory candidate applies through the original switch",
        )
        final = view()
        assert final["history"][0]["source_group_id"] == "group-" + record["mission_id"]
        document = final["current_plan"]
        assert len(document["tasks"]) == 2
        assert all(task["id"].startswith(ticket + "-") for task in document["tasks"])
        reviewer = next(
            row for row in final["authority"]["candidates"] if row["ticket_id"] == ticket
        )["review"]
        assert reviewer["principal"] == scenario.administrator and reviewer["memory_digest"]
        save(scenario.directory / "runtime-mi-scoped-result.json", result)
        return result
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
