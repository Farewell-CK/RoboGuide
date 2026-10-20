"""Explicit MI protocol fixtures around actual SQL/HTTP/owned-process runtime conformance.

The initial planner and local Responses endpoint are declared fixtures, never model
quality or device evidence. The maintained Request engine, worker, mTLS Controller,
Node and CPU workers execute their actual production paths.
"""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mission.api import MissionRequestHttpServer
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.controller import HttpMissionController
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.request_engine import MissionRequestEngine
from mission.request_record import DialogueTurn, IntentAssessment
from mission.request_store import MissionRequestStore
from mission.responses import ResponsesMissionRepairer
from mission.runtime_replanning import RuntimePeer
from mission.runtime_replanning_cli import compose


class FixtureInterpreter:
    """Retain the actual submitted instruction without claiming a model interpreted it."""

    def interpret(
        self, dialogue: tuple[DialogueTurn, ...], grounding_context: GroundingContextSnapshot
    ) -> IntentAssessment:
        """Return the explicit software fixture objective without world facts."""
        return IntentAssessment(dialogue[0].content, (), (), ())


class FixturePlanner:
    """Submit the explicit generic file-computation profile, never a robot or provider result."""

    def __init__(self, path: Path) -> None:
        """Retain one authored software conformance plan."""
        self.path = path

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Preserve the actual Request identity and submitted original objective."""
        value = json.loads(self.path.read_bytes())
        value["mission"]["id"] = mission_id
        value["mission"]["objective"] = grounded_intent.objective
        plan = MissionPlan.from_json(value)
        capability_catalog.validate_plan(plan)
        return plan


def service(directory: Path, address: str, controller: str) -> None:
    """Run the real Request HTTP/SQL engine with explicitly labelled initial-planning fixtures."""
    certificates = directory / "pki"
    client = ssl.create_default_context(cafile=str(certificates / "ca.crt"))
    client.load_cert_chain(certificates / "admin.crt", certificates / "admin.key")
    engine = MissionRequestEngine(
        MissionRequestStore(directory / "mission.sqlite3"),
        FixtureInterpreter(),
        FixturePlanner(directory / "original-plan.json"),
        HttpMissionController(controller, 5, tls_context=client),
        CanonicalCapabilityCatalog.load(directory / "software-conformance-catalog.json"),
        frozenset(),
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificates / "server.crt", certificates / "server.key")
    context.load_verify_locations(certificates / "ca.crt")
    context.verify_mode = ssl.CERT_REQUIRED
    host, port = address.rsplit(":", 1)
    server = MissionRequestHttpServer(
        (host, int(port)),
        engine,
        1024 * 1024,
        tls_context=context,
        authorization_path=directory / "mission-authorization.json",
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


class PausedGenerator(ResponsesMissionRepairer):
    """Pause only the checker after the production provider receipt has actually committed."""

    def __init__(
        self,
        delegate: ResponsesMissionRepairer,
        marker: Path,
        database: Path,
        snapshot: Path | None,
    ) -> None:
        """Retain the actual production adapter without constructing a replacement provider."""
        self.delegate, self.marker = delegate, marker
        self.database, self.snapshot = database, snapshot

    def replan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        original_plan: MissionPlan,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
        runtime_context: JSONObject,
    ) -> MissionPlan:
        """Pause after the actual adapter and SQL receipt for process interruption."""
        if self.snapshot is not None:
            assert not self.snapshot.exists()
            with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as source:
                with sqlite3.connect(self.snapshot) as target:
                    source.backup(target)
        plan = self.delegate.replan(
            mission_id,
            grounded_intent,
            original_plan,
            capability_catalog,
            grounding_context,
            runtime_context,
        )
        self.marker.write_text(json.dumps({"provider_receipt_committed": True}))
        while True:
            time.sleep(0.05)
        return plan  # pragma: no cover - checker terminates this deliberately paused process


class LostCandidateResponse:
    """Discard one actual post-Commit reply; never replace the original Controller operation."""

    def __init__(self, delegate: RuntimePeer, marker: Path) -> None:
        """Keep the actual configured origin/identity and mark only the owned fault injection."""
        self.delegate, self.marker = delegate, marker
        self.endpoint, self.tenant, self.principal = (
            delegate.endpoint,
            delegate.tenant,
            delegate.principal,
        )

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 5
    ) -> tuple[int, dict[str, Any]]:
        """Deliver the exact original request once, then lose its actual successful reply."""
        result = self.delegate.request(method, path, body, timeout)
        if method == "POST" and body and body.get("action", {}).get("kind") == "submit-candidate":
            self.marker.write_text(json.dumps({"actual_reply": result}))
            raise TimeoutError("checker discarded actual candidate reply after Controller Commit")
        return result


class ProviderFixtureServer(ThreadingHTTPServer):
    """Serve declared protocol fixtures; this is never an external model or inference result."""

    def __init__(self, directory: Path) -> None:
        """Own one loopback endpoint and an inspectable actual HTTP request history."""
        super().__init__(("127.0.0.1", 0), ProviderFixtureHandler)
        self.directory = directory
        self.calls: list[dict[str, Any]] = []


class ProviderFixtureHandler(BaseHTTPRequestHandler):
    """Return syntactic plans and Review advice solely to verify the maintained wire path."""

    server: ProviderFixtureServer

    def log_message(self, format: str, *args: Any) -> None:
        """Retain exact requests in the owned fixture file rather than logging credentials."""

    def do_POST(self) -> None:  # noqa: N802
        """Use actual original logical input while generating zero device or success evidence."""
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 2 * 1024 * 1024:
            self.send_error(400)
            return
        payload = json.loads(self.rfile.read(size))
        context = json.loads(payload["input"])
        assert "runtime_replanning_context" in context
        if payload["text"]["format"]["name"] == "mission_plan_repair_v0":
            output = copy.deepcopy(context["rejected_plan"])
            runtime = context["runtime_replanning_context"]
            replacements = {
                row["id"]: runtime["new_task_id_prefix"] + str(index)
                for index, row in enumerate(output["tasks"])
                if row["id"] not in runtime["completed_tasks"]
            }
            for task in output["tasks"]:
                if task["id"] not in replacements:
                    continue
                task["id"] = replacements[task["id"]]
                task["depends_on"] = [
                    replacements.get(identity, identity) for identity in task["depends_on"]
                ]
                for role in task["roles"]:
                    intent = role["execution_intent"]
                    intent["parameters"] = [
                        {"key": key, "value": value}
                        for key, value in sorted(intent["parameters"].items())
                    ]
        else:
            output = {"approved": True, "issues": []}
        self.server.calls.append(payload)
        (self.server.directory / "provider-protocol-fixture-calls.json").write_text(
            json.dumps(self.server.calls, indent=2, sort_keys=True)
        )
        response = {
            "id": f"explicit-protocol-fixture-{len(self.server.calls)}",
            "model": "explicit-protocol-fixture-no-inference",
            "status": "completed",
            "error": None,
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ],
        }
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Run only explicit owned conformance roles, never a production fixture default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("service", "worker"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--address")
    parser.add_argument("--controller")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--pause-after-generation", type=Path)
    parser.add_argument("--lose-candidate-response", type=Path)
    parser.add_argument("--snapshot-before-generation", type=Path)
    arguments = parser.parse_args()
    if arguments.role == "service":
        service(arguments.directory, arguments.address, arguments.controller)
        return
    store, worker, requests, _ = compose(arguments.profile, Path.cwd())
    try:
        if arguments.pause_after_generation:
            worker.generator = PausedGenerator(
                worker.generator,
                arguments.pause_after_generation,
                store.path,
                arguments.snapshot_before_generation,
            )
        if arguments.lose_candidate_response:
            worker.proposer = LostCandidateResponse(
                worker.proposer, arguments.lose_candidate_response
            )
        for request in worker.poll_requests(requests):
            print(json.dumps(worker.run_once(request), sort_keys=True), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
