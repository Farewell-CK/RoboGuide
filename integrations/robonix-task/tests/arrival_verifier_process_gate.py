"""Exercise arrival registration and goal barriers with private real Controller/Node processes.

Every execution and target is an invented loopback fixture. This gate neither contacts a
robot nor reads deployment SQL. Manual confirmation inputs below are software test data;
passing this gate proves software contracts, never physical arrival, stop or safe recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO

from robonix_task.arrival_verifier import ArrivalVerifier, HttpArrivalController
from robonix_task.arrival_verifier_store import ArrivalVerifierStore
from robonix_task.patrol import ARRIVAL_CONTRACT, ASSIGNMENTS, arrival_predicate, build_patrol_plan

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/quality"))
from check_identity_processes import client, pki, ports  # noqa: E402
from check_state_verifier_processes import node_config  # noqa: E402

SOURCE = "software-onsite-arrival"
PRINCIPAL = "software-arrival-verifier"
MISSION = "arrival-process-fixture"


def digest(path: Path) -> str:
    """Hash an exact executable or source without retaining its entire contents in memory."""
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def save(root: Path, name: str, value: Any) -> Any:
    """Retain an inspectable observation in the newly owned private evidence directory."""
    path = root / (name + ".json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def task_status(mission: dict[str, Any], identity: str) -> str:
    """Select one exact Task status independently of projection array order."""
    matches = [task for task in mission["tasks"] if task["task_id"] == identity]
    assert len(matches) == 1, matches
    return str(matches[0]["status"])


def receipt(value: dict[str, Any]) -> dict[str, Any]:
    """Locate the original Controller receipt inside a retained publisher result."""
    if "evidence" in value and "input_digest" in value:
        return value
    child = value.get("receipt")
    if not isinstance(child, dict):
        raise AssertionError(f"publisher did not retain a Controller receipt: {value}")
    return receipt(child)


def fixture_plan() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build two fixed physical Actor routes plus two explicitly synthetic dependent operations."""
    targets = []
    for index, assignment in enumerate(ASSIGNMENTS):
        targets.append(
            {
                "entity_id": assignment.entity_id,
                "node_id": assignment.node_id,
                "target_name": assignment.target_name,
                "map_id": "software-map-" + str(index),
                "region_id": "software-region-" + str(index),
                "region_source_digest": "sha256:" + hashlib.sha256(str(index).encode()).hexdigest(),
            }
        )
    snapshot = {"targets": targets}
    document = build_patrol_plan(snapshot, MISSION)
    document["mission"]["objective"] = "Loopback software arrival barriers; no physical execution"
    for task in document["tasks"]:
        task["roles"][0]["requirements"]["resources"] = [{"kind": "compute", "units": 1}]
    for assignment, navigation in zip(ASSIGNMENTS, list(document["tasks"]), strict=True):
        successor = deepcopy(navigation)
        successor["id"] = assignment.task_id + "-photo-fixture"
        successor["description"] = "Readonly invented photo successor; no camera or robot exists"
        successor["depends_on"] = [assignment.task_id]
        successor["roles"][0]["id"] = assignment.role_id + "-photo-fixture"
        successor["roles"][0]["execution_intent"]["objective"] = successor["description"]
        successor["satisfaction"] = {
            "basis": "execution-report",
            "expected_effect": "Readonly software fixture completed",
        }
        document["tasks"].append(successor)
    configuration = {
        "schema_version": "roboguide.onsite-arrival-verifier/v0.1",
        "source_id": SOURCE,
        "revision": 1,
        "tenant_id": "local",
        "producer_principal": PRINCIPAL,
        "verifier": dict(ARRIVAL_CONTRACT),
        "max_age_ms": 5000,
        "targets": [
            {**target, "predicate": arrival_predicate(snapshot, assignment)}
            for target, assignment in zip(targets, ASSIGNMENTS, strict=True)
        ],
    }
    return document, configuration


class ProcessGate:
    """Own one short-lived Controller, two synthetic Nodes and all their isolated authority."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Prepare fresh private files and loopback addresses without field discovery."""
        self.root: Path = args.output.resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.controller_binary: Path = args.controller.resolve(strict=True)
        self.node_binary: Path = args.node.resolve(strict=True)
        self.binary_digests = {
            str(path): digest(path) for path in (self.controller_binary, self.node_binary)
        }
        self.source_paths = (
            Path(__file__),
            ROOT / "integrations/robonix-task/robonix_task/arrival_verifier.py",
            ROOT / "integrations/robonix-task/robonix_task/arrival_verifier_store.py",
            ROOT / "integrations/robonix-task/robonix_task/patrol.py",
            ROOT / "tools/quality/check_identity_processes.py",
            ROOT / "tools/quality/check_state_verifier_processes.py",
        )
        self.source_digests = {
            str(path.relative_to(ROOT)): digest(path) for path in self.source_paths
        }
        save(
            self.root,
            "frozen-input-digests",
            {"binary_sha256": self.binary_digests, "source_sha256": self.source_digests},
        )
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
        self.environment.update(
            TOKIO_WORKER_THREADS="2",
            ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA=args.storage_schema,
            ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA="roboguide.artifact-storage/v3",
            ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS="200",
            ROBOGUIDE_NODE_LEASE_DURATION_MS="1200",
        )
        self.children: list[subprocess.Popen[bytes]] = []
        self.logs: list[BinaryIO] = []
        self.calls: list[dict[str, Any]] = []
        self.calls_lock = threading.Lock()
        self.checks: list[str] = []
        self.contexts: dict[str, ssl.SSLContext] = {}
        self.controller: subprocess.Popen[bytes] | None = None
        self.nodes: list[subprocess.Popen[bytes]] = []
        self.node_paths: list[Path] = []
        self.document, self.configuration = fixture_plan()
        self.raw_plan = json.dumps(
            self.document, ensure_ascii=False, separators=(",", ":")
        ).encode()
        self.raw_plan_path = self.root / "accepted-plan.json"
        self.raw_plan_path.write_bytes(self.raw_plan)
        save(self.root, "verifier-config", self.configuration)
        self.certificates = self.root / "pki"
        self.configure_authority()
        self.grpc, self.http, self.artifact = ports(3)
        self.origin = f"https://127.0.0.1:{self.http}"
        self.command = [
            f"127.0.0.1:{self.grpc}",
            str(self.root / "controller.sqlite3"),
            f"127.0.0.1:{self.http}",
            f"127.0.0.1:{self.artifact}",
            str(self.root / "artifacts"),
            "",
            str(self.root / "physical-current.json"),
        ]
        self.server = self.make_provider()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def configure_authority(self) -> None:
        """Create actual mTLS verifier grants and original physical routes only for this fixture."""
        leaves = pki(self.certificates)
        grants = {}
        for leaf, principal, role, tenants in (
            ("admin", "software-administrator", "recovery-admin", ["*"]),
            ("viewer", PRINCIPAL, "verifier", ["local"]),
            ("admin-next", "foreign-arrival-producer", "verifier", ["local"]),
            ("enrolment-next", "foreign-tenant-producer", "verifier", ["foreign"]),
        ):
            grants["sha256:" + leaves[leaf]] = {
                "principal_id": principal,
                "role": role,
                "tenant_ids": tenants,
                "not_before_ms": 0,
                "expires_at_ms": 0,
                "revoked": False,
            }
            self.contexts[leaf] = client(self.certificates, leaf)
        authorization = save(
            self.root,
            "operator-authorization",
            {
                "schema_version": "roboguide.operator-authorization/v0.3",
                "registry_id": "software-arrival-operators",
                "revision": 1,
                "grants": grants,
            },
        )
        assert authorization["grants"]
        self.environment.update(
            ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT=str(self.certificates / "server.crt"),
            ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY=str(self.certificates / "server.key"),
            ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA=str(self.certificates / "ca.crt"),
            ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP=str(self.root / "operator-authorization.json"),
        )
        save(
            self.root,
            "physical-current",
            {
                "schema": "roboguide.physical-entity-registry/v0.1",
                "registry_id": "software-arrival-entities",
                "revision": 1,
                "routing_profile": "one-routable-entity-per-node",
                "entities": [{"entity_id": a.entity_id, "node_id": a.node_id} for a in ASSIGNMENTS],
            },
        )

    def make_provider(self) -> ThreadingHTTPServer:
        """Expose invented software operations without a Pilot or actuator API."""
        owner = self

        class Handler(BaseHTTPRequestHandler):
            """Serve fixture capability readiness and terminal software execution reports."""

            def log_message(self, *_args: Any) -> None:
                """Keep bounded structured execution records instead of repetitive access logs."""

            def reply(self, value: dict[str, Any]) -> None:
                """Return one finite invented JSON observation."""
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                """Report software health and admission without inventing physical facts."""
                if self.path == "/health":
                    self.reply({"state": "ONLINE", "detail": "loopback software fixture"})
                elif self.path == "/capability":
                    self.reply({"state": "READY", "detail": "readonly loopback fixture"})
                elif self.path == "/admission":
                    self.reply({"software_fixture": True})
                else:
                    self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                """Record local executions once and complete them without any physical operation."""
                length = int(self.headers["Content-Length"])
                if not 0 < length <= 65536:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(length))
                if self.path == "/execute":
                    with owner.calls_lock:
                        owner.calls.append(body)
                        save(owner.root, "provider-execute-calls", owner.calls)
                    self.reply({"execution_id": body["attempt_id"]})
                elif self.path == "/status":
                    self.reply({"state": "COMPLETED", "detail": "invented local completion"})
                else:
                    self.send_error(404)

        candidate = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if candidate.server_port in (self.grpc, self.http, self.artifact):
            candidate.server_close()
            raise RuntimeError(
                "private provider listener collided with reserved Controller address"
            )
        return candidate

    def exchange(
        self, path: str, body: Any = None, who: str = "admin"
    ) -> tuple[int, dict[str, Any]]:
        """Use actual loopback mTLS and retain the original bounded request and response."""
        data = (
            body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode()
        )
        headers = {"Content-Type": "application/json"}
        if data is None:
            headers["Content-Length"] = "0"
        request = urllib.request.Request(self.origin + path, data=data, headers=headers)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.contexts[who]),
        )
        try:
            response = opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("fixture HTTP response exceeds its original bound")
            value = json.loads(raw)
            status = response.code
        assert isinstance(value, dict), value
        with (self.root / "http.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "path": path,
                        "principal_leaf": who,
                        "input": body.decode() if isinstance(body, bytes) else body,
                        "status": status,
                        "value": value,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return int(status), value

    def get(self, path: str) -> dict[str, Any]:
        """Require one successful public Controller read without mutating its state."""
        status, value = self.exchange(path)
        assert status == 200, (path, status, value)
        return value

    def wait(self, read: Callable[[], Any], predicate: Callable[[Any], bool], why: str) -> Any:
        """Bound process observations and retain the actual final response on timeout."""
        deadline, last = time.monotonic() + 30, None
        while time.monotonic() < deadline:
            for child in self.children:
                if child not in self.stopped and child.poll() is not None:
                    raise RuntimeError("owned fixture process exited; inspect retained logs")
            try:
                last = read()
                if predicate(last):
                    return last
            except (OSError, ValueError) as error:
                last = str(error)
            time.sleep(0.08)
        raise TimeoutError(f"{why}: {last}")

    def launch(self, binary: Path, argv: list[str], name: str) -> subprocess.Popen[bytes]:
        """Launch one explicitly owned process against private files and scrubbed environment."""
        output = (self.root / (name + ".log")).open("wb")
        self.logs.append(output)
        child = subprocess.Popen(
            [str(binary), *argv],
            cwd=self.root,
            env=self.environment,
            stdout=output,
            stderr=output,
        )
        self.children.append(child)
        return child

    def stop(self, child: subprocess.Popen[bytes]) -> None:
        """Reap only an owned child while preserving all of its SQL, receipts and Node journals."""
        self.stopped.add(child)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    def start(self) -> None:
        """Start one Controller and two independent synthetic Node protocol clients."""
        self.stopped: set[subprocess.Popen[bytes]] = set()
        self.controller = self.launch(self.controller_binary, self.command, "controller")
        self.wait(lambda: self.get("/readyz"), lambda v: v["ready"], "Controller ready")
        for assignment in ASSIGNMENTS:
            directory = self.root / assignment.node_id
            directory.mkdir()
            config = node_config(
                directory,
                assignment.node_id,
                f"127.0.0.1:{self.grpc}",
                f"http://127.0.0.1:{self.server.server_port}",
            ).replace("compute.hash-files@v1", "task.delegate@v1")
            path = directory / "node.toml"
            path.write_text(config, encoding="utf-8")
            self.node_paths.append(path)
            self.nodes.append(self.launch(self.node_binary, [str(path)], assignment.node_id))
        self.wait(
            lambda: self.get("/v1/inventory"),
            lambda v: (
                len(v["nodes"]) == 2 and all(n["liveness"] == "Reachable" for n in v["nodes"])
            ),
            "both private Nodes reachable",
        )

    def source_command(self) -> dict[str, Any]:
        """Declare one explicit revision of the independent manual source using the public API."""
        return {
            "command_id": "install-software-arrival-verifier",
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                "registry_id": "software-arrival-sources",
                "revision": 1,
                "sources": {
                    SOURCE: {
                        "source_id": SOURCE,
                        "revision": 1,
                        "tenant_id": "local",
                        "producer_principals": [PRINCIPAL],
                        "verifier": dict(ARRIVAL_CONTRACT),
                        "predicates": [t["predicate"] for t in self.configuration["targets"]],
                        "max_age_ms": 5000,
                        "stop_timeout_ms": 5000,
                        "enabled": True,
                        "continuous": False,
                        "require_artifact": False,
                    }
                },
            },
        }

    def mission(self) -> dict[str, Any]:
        """Read only this fixture Mission through its exact original identity."""
        return self.get("/v1/missions/" + MISSION)

    def allocations(self) -> list[dict[str, Any]]:
        """Read original Control ownership, never infer resource release from elapsed time."""
        value = self.get("/v1/resource-allocations")["allocations"]
        assert isinstance(value, list), value
        return value

    def attempts(self) -> list[dict[str, Any]]:
        """Read this Mission's complete attempts for exact binding and dispatch proof."""
        value = self.get("/v1/missions/" + MISSION + "/execution-attempts")["attempts"]
        assert isinstance(value, list), value
        return value

    def epochs(self) -> dict[str, str]:
        """Read actual Node State epochs so recovery cannot pass using cached reachability."""
        records = self.get(
            "/v1/state/records?object_class=world&object_type=software-work-admission"
        )["records"]
        selected = {}
        for record in records:
            identity = record["object"]["object_id"]
            if identity in {a.node_id for a in ASSIGNMENTS}:
                selected[identity] = str(record["value"]["source_epoch"])
        return selected

    def publisher(self) -> ArrivalVerifier:
        """Use the production manual publisher with an independent restricted TLS identity."""
        return ArrivalVerifier(
            self.configuration,
            HttpArrivalController(
                self.origin,
                ca=self.certificates / "ca.crt",
                certificate=self.certificates / "viewer.crt",
                private_key=self.certificates / "viewer.key",
            ),
            ArrivalVerifierStore(self.root / "manual-arrival.sqlite3"),
        )

    def confirmation(self, index: int, verdict: str) -> dict[str, Any]:
        """Bind an invented report to original bytes, exact Actor and completed attempts."""
        assignment = ASSIGNMENTS[index]
        target = self.configuration["targets"][index]
        attempts = [a for a in self.attempts() if a["task_id"] == assignment.task_id]
        assert attempts and all(a["status"] == "Completed" for a in attempts), attempts
        return {
            "schema_version": "roboguide.onsite-arrival-confirmation/v0.1",
            "observation_id": f"software-confirmation-{index}-{verdict}",
            "tenant_id": "local",
            "mission_id": MISSION,
            "task_id": assignment.task_id,
            "plan_digest": self.get("/v1/missions/" + MISSION + "/tenant")["source_plan_digest"],
            "wire_plan_digest": "sha256:" + hashlib.sha256(self.raw_plan).hexdigest(),
            "attempts": [
                {"role_id": a["role_id"], "attempt_id": a["execution_id"]} for a in attempts
            ],
            "entity_id": assignment.entity_id,
            "node_id": assignment.node_id,
            "predicate": target["predicate"],
            "observer_id": "invented-software-observer",
            "verdict": verdict,
        }

    def invalid_evidence(self, confirmation: dict[str, Any]) -> dict[str, Any]:
        """Prove wrong plan, attempt, tenant and producer cannot consume either arrival barrier."""
        base = {
            "evidence_id": "rejected-software-arrival",
            "source_id": SOURCE,
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": MISSION, "task_id": confirmation["task_id"]},
            "plan_digest": confirmation["plan_digest"],
            "attempts": confirmation["attempts"],
            "predicate": confirmation["predicate"],
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.time_ns() // 1_000_000,
            "valid_for_ms": 5000,
            "sequence": 1,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }
        for case, who in (
            ("plan", "viewer"),
            ("attempt", "viewer"),
            ("tenant", "enrolment-next"),
            ("principal", "admin-next"),
        ):
            wrong = deepcopy(base)
            wrong["evidence_id"] += "-" + case
            if case == "plan":
                wrong["plan_digest"] = "sha256:" + "0" * 64
            elif case == "attempt":
                wrong["attempts"][0]["attempt_id"] = "foreign-attempt"
            elif case == "tenant":
                wrong["tenant_id"] = "foreign"
            status, value = self.exchange("/v1/verifier-evidence", wrong, who)
            save(self.root, "rejected-evidence-" + case, {"status": status, "value": value})
            assert status in (403, 409), (case, status, value)
        assert len(self.calls) == 2, self.calls
        assert len(self.allocations()) == 2
        self.checks.append("wrong plan, attempt, tenant and producer rejected over actual mTLS")
        return base

    def freeze_history(self) -> None:
        """Retain a bounded original event prefix including ordinary Commit, Bind and release."""
        after = 0
        through: int | None = None
        events = []
        for _ in range(32):
            page = self.get(f"/v1/events?after={after}&limit=256")
            if through is None:
                through = int(page["through_sequence"])
            for event in page["events"]:
                if event["sequence"] <= through:
                    events.append(event)
            cursor = page.get("next_after_sequence")
            if not page["has_more"] or cursor is None or int(cursor) >= through:
                save(self.root, "controller-event-prefix", {"through": through, "events": events})
                return
            if int(cursor) <= after:
                raise AssertionError("event history cursor did not advance")
            after = int(cursor)
        raise AssertionError("private event evidence exceeded the bounded prefix collection")

    def run(self) -> dict[str, Any]:
        """Exercise source registration, production publication and cold replay barriers."""
        self.start()
        initial_epochs = self.wait(
            self.epochs,
            lambda value: set(value) == {a.node_id for a in ASSIGNMENTS},
            "both actual Node State source epochs",
        )
        save(self.root, "initial-node-epochs", initial_epochs)
        code, blocked = self.exchange("/v1/missions/validate-plan", self.raw_plan)
        save(self.root, "before-source-plan-blocked", blocked)
        assert code == 200 and blocked["decision"] == "blocked", blocked
        assert any(i["code"] == "verifier.source_unavailable" for i in blocked["issues"]), blocked
        assert not self.calls
        self.checks.append("plan validation blocks before source registration without execution")

        command = self.source_command()
        code, registered = self.exchange("/v1/verifier-sources", command)
        assert code == 200, registered
        assert self.exchange("/v1/verifier-sources", command) == (code, registered)
        conflict = deepcopy(command)
        conflict["registry"]["sources"][SOURCE]["max_age_ms"] = 4999
        assert self.exchange("/v1/verifier-sources", conflict)[0] == 409
        save(self.root, "source-registration-receipt", registered)
        current = self.get("/v1/verifier-sources")["registry"]
        assert current["revision"] == 1 and current["sources"][SOURCE]["revision"] == 1
        self.checks.append(
            "public source revision 1 installed with stable receipt and conflict refusal"
        )

        for path, body in (
            ("/v1/missions", self.raw_plan),
            ("/v1/missions/" + MISSION + "/cancel", {"command_id": "forbidden-cancel"}),
            ("/v1/verifier-sources", command),
        ):
            assert self.exchange(path, body, "viewer")[0] == 403, path
        self.checks.append(
            "actual verifier identity cannot admit/cancel Missions or manage registry"
        )

        code, admission = self.exchange("/v1/missions", self.raw_plan)
        assert code in (200, 202), admission
        save(self.root, "mission-admission", admission)
        awaiting = self.wait(
            self.mission,
            lambda m: all(task_status(m, a.task_id) == "AwaitingSatisfaction" for a in ASSIGNMENTS),
            "both local completions await independent arrival evidence",
        )
        save(self.root, "both-awaiting-arrival", awaiting)
        before = save(self.root, "awaiting-allocations", self.allocations())
        assert len(before) == 2 and len(self.calls) == 2, (before, self.calls)
        initial_attempts = save(self.root, "both-local-completed-attempts", self.attempts())
        assert len(initial_attempts) == 2, initial_attempts
        for a in ASSIGNMENTS:
            selected = [item for item in initial_attempts if item["task_id"] == a.task_id]
            assert len(selected) == 1 and selected[0]["node_id"] == a.node_id, selected
            assert selected[0]["role_id"] == a.role_id and selected[0]["status"] == "Completed"
            assert task_status(awaiting, a.task_id + "-photo-fixture") != "Completed"
        self.checks.append("local Completed preserves two slots and blocks both photo successors")

        positive = self.confirmation(0, "arrived-and-stopped")
        save(self.root, "first-positive-confirmation", positive)
        prototype = self.invalid_evidence(positive)
        publisher = self.publisher()
        save(
            self.root,
            "production-publisher-inspect",
            publisher.inspect(self.raw_plan, MISSION, ASSIGNMENTS[0].task_id),
        )
        unknown = self.confirmation(1, "unknown")
        save(self.root, "unknown-confirmation", unknown)
        unknown_result = save(
            self.root, "unknown-publisher-result", publisher.publish(self.raw_plan, unknown)
        )
        assert unknown_result["status"] == "unknown" and unknown_result["evidence"] is None
        assert unknown_result["receipt"] is None
        provisional = deepcopy(prototype)
        provisional.update(
            evidence_id="provisional-unknown-software-observation",
            task_ref={"mission_id": MISSION, "task_id": unknown["task_id"]},
            attempts=unknown["attempts"],
            predicate=unknown["predicate"],
            verdict="unknown",
            finality="provisional",
            source_observed_at_ms=time.time_ns() // 1_000_000,
        )
        status, unknown_receipt = self.exchange("/v1/verifier-evidence", provisional, "viewer")
        assert status == 200 and receipt(unknown_receipt)["applied"] is False, unknown_receipt
        assert self.exchange("/v1/verifier-evidence", provisional, "viewer") == (
            status,
            unknown_receipt,
        )
        save(self.root, "unknown-generic-evidence-receipt", unknown_receipt)
        assert self.allocations() == before
        assert all(
            task_status(self.mission(), a.task_id) == "AwaitingSatisfaction" for a in ASSIGNMENTS
        )
        assert len(self.calls) == 2
        self.checks.append(
            "unknown manual observation retains both allocations without successor execution"
        )

        first_result = save(
            self.root, "first-publisher-result", publisher.publish(self.raw_plan, positive)
        )
        first_receipt = receipt(first_result)
        assert first_receipt["applied"] is True
        assert first_receipt["authority_subject"] == PRINCIPAL
        assert first_receipt["evidence"]["attempts"] == positive["attempts"]
        assert first_receipt["evidence"]["plan_digest"] == positive["plan_digest"]
        assert first_receipt["evidence"]["predicate"] == positive["predicate"]
        assert receipt(publisher.publish(self.raw_plan, positive)) == first_receipt
        self.wait(
            self.mission,
            lambda m: task_status(m, ASSIGNMENTS[0].task_id + "-photo-fixture") == "Completed",
            "only first accepted arrival opens its successor",
        )
        partial = save(self.root, "first-only-successor-completed", self.mission())
        assert task_status(partial, ASSIGNMENTS[1].task_id) == "AwaitingSatisfaction"
        assert task_status(partial, ASSIGNMENTS[1].task_id + "-photo-fixture") != "Completed"
        assert len(self.calls) == 3 and len(self.allocations()) == 1
        self.checks.append("production publisher exact binding opens only its own successor once")

        for node in self.nodes:
            self.stop(node)
        assert self.controller is not None
        self.stop(self.controller)
        self.controller = self.launch(self.controller_binary, self.command, "controller-recovered")
        self.wait(lambda: self.get("/readyz"), lambda v: v["ready"], "private Controller recovery")
        self.nodes = [
            self.launch(self.node_binary, [str(path)], "node-recovered-" + str(i))
            for i, path in enumerate(self.node_paths)
        ]
        self.wait(
            lambda: self.get("/v1/inventory"),
            lambda v: (
                len(v["nodes"]) == 2 and all(n["liveness"] == "Reachable" for n in v["nodes"])
            ),
            "private Node journal recovery",
        )
        recovered_epochs = self.wait(
            self.epochs,
            lambda value: (
                set(value) == set(initial_epochs)
                and all(value[identity] != old for identity, old in initial_epochs.items())
            ),
            "both new Node epochs, not cached reachable inventory",
        )
        save(self.root, "recovered-node-epochs", recovered_epochs)
        publisher = self.publisher()
        assert receipt(publisher.publish(self.raw_plan, positive)) == first_receipt
        assert self.exchange("/v1/verifier-evidence", first_receipt["evidence"], "viewer") == (
            200,
            {
                "schema_version": "roboguide.semantic-verifier-receipt/v0.1",
                "receipt": first_receipt,
            },
        )
        recovered = save(self.root, "recovered-goal-barriers", self.mission())
        assert task_status(recovered, ASSIGNMENTS[1].task_id) == "AwaitingSatisfaction"
        assert len(self.calls) == 3 and len(self.allocations()) == 1
        self.checks.append(
            "publisher, Controller and Nodes recover original receipts without extra Execute"
        )

        second = self.confirmation(1, "arrived-and-stopped")
        save(self.root, "second-positive-confirmation", second)
        second_result = save(
            self.root, "second-publisher-result", publisher.publish(self.raw_plan, second)
        )
        assert receipt(second_result)["applied"] is True
        completed = self.wait(
            self.mission, lambda m: m["status"] == "Completed", "both successors complete"
        )
        save(self.root, "completed-mission", completed)
        self.wait(
            self.allocations, lambda value: not value, "Control releases both final allocations"
        )
        assert len(self.calls) == 4
        assert len({call["attempt_id"] for call in self.calls}) == 4
        save(self.root, "final-attempts", self.attempts())
        save(self.root, "final-allocations", self.allocations())
        self.freeze_history()
        self.checks.append(
            "second independent arrival completes its successor and releases through Control"
        )
        assert self.binary_digests == {
            str(path): digest(path) for path in (self.controller_binary, self.node_binary)
        }, "a selected binary changed during the software gate"
        assert self.source_digests == {
            str(path.relative_to(ROOT)): digest(path) for path in self.source_paths
        }, "a selected source changed during the software gate"
        return {
            "passed": True,
            "physical_robot_used": False,
            "field_deployment_changed": False,
            "checks": self.checks,
            "software_execute_calls": self.calls,
            "binary_sha256": self.binary_digests,
            "source_sha256": self.source_digests,
            "limits": [
                "synthetic targets and manual confirmations are not physical evidence",
                "photo successors are read-only software fixtures, not camera validation",
                "arrival-and-stopped does not grant recovery, door clearance or movement authority",
                "run under an externally bounded 1 CPU / 1 GiB isolated scope on ThinkPad",
            ],
        }

    def close(self) -> None:
        """Stop only owned children and provider, retaining all original private evidence files."""
        if not hasattr(self, "stopped"):
            self.stopped = set()
        for child in reversed(self.children):
            self.stop(child)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for output in self.logs:
            output.close()


def main() -> None:
    """Require explicit binaries and a fresh private directory; never infer a deployment target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-schema", choices=("legacy", "1", "2", "3"), default="3")
    gate = ProcessGate(parser.parse_args())
    try:
        result = gate.run()
        save(gate.root, "result", result)
        print(json.dumps({"passed": True, "checks": len(gate.checks), "output": str(gate.root)}))
    except BaseException as error:
        save(
            gate.root,
            "failure",
            {"type": type(error).__name__, "error": str(error), "checks": gate.checks},
        )
        raise
    finally:
        gate.close()


if __name__ == "__main__":
    main()
