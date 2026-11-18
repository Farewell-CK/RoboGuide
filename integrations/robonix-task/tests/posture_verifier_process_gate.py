"""Exercise the production MI observer with private mTLS Controller/Node software processes.

Every source sample and local completion is synthetic. This gate contacts no robot, Pilot,
model, deployment configuration or field database. It tests the MI observer component;
the normal MI Interpreter/Planner/Reviewer path is a separate acceptance boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mission.controller import HttpMissionController
from mission.models import MissionPlan
from mission.posture_verifier import PostureGoalVerifier
from mission.posture_verifier_store import PostureVerificationStore
from robonix_task.posture import inspect_posture
from robonix_task.posture_goal import VERIFIER, predicate_for

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/quality"))
from check_identity_processes import client, pki, ports, request, stop  # noqa: E402
from check_state_verifier_processes import node_config, plan, wait_for  # noqa: E402

NODE = "posture-mi-software"
SOURCE = "posture-mi-verifier"
PRINCIPAL = "mi:posture-observer"


def run(controller: Path, node: Path, output: Path) -> dict[str, Any]:
    """Verify actual permission boundaries, positive/negative settlement and durable no-retry."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    certificates = output / "pki"
    digests = pki(certificates)
    admin, verifier = client(certificates, "admin"), client(certificates, "viewer")
    grpc_port, http_port, artifact_port = ports(3)
    origin = f"https://127.0.0.1:{http_port}"
    roles = output / "roles.json"
    roles.write_text(
        json.dumps(
            {
                "schema_version": "roboguide.operator-authorization/v0.3",
                "registry_id": "posture-mi-gate",
                "revision": 1,
                "grants": {
                    "sha256:" + digests["admin"]: {
                        "principal_id": "fixture:admin",
                        "role": "recovery-admin",
                        "tenant_ids": ["*"],
                    },
                    "sha256:" + digests["viewer"]: {
                        "principal_id": PRINCIPAL,
                        "role": "verifier",
                        "tenant_ids": ["local"],
                    },
                },
            }
        )
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    environment.update(
        {
            "TOKIO_WORKER_THREADS": "2",
            "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "3",
            "ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS": "200",
            "ROBOGUIDE_NODE_LEASE_DURATION_MS": "1200",
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps({NODE: digests["enrolment-first"]}),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(roles),
        }
    )
    children: list[subprocess.Popen[bytes]] = []
    calls: list[str] = []
    sample = {"posture": "down"}

    class Provider(BaseHTTPRequestHandler):
        """Publish synthetic robot State and finish software work without an actuator API."""

        def log_message(self, _format: str, *_args: Any) -> None:
            """Keep polling output bounded while public Controller exchanges remain retained."""

        def reply(self, value: dict[str, Any]) -> None:
            """Return one finite local fixture document."""
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            """Issue a distinct synthetic native sample for each source acquisition."""
            if self.path == "/health":
                self.reply({"state": "ONLINE", "detail": "software fixture"})
            elif self.path == "/capability":
                self.reply({"state": "READY", "detail": "software fixture"})
            elif self.path == "/admission":
                self.reply(
                    {
                        "chassis": {
                            "contract_id": "robonix/primitive/chassis/state",
                            "availability": "current",
                            "provider_id": "fixture-provider",
                            "registration_id": "fixture-registration",
                            "received_at_ns": time.time_ns(),
                            "age_ms": 0,
                            "value": {
                                "posture": sample["posture"],
                                "stale": False,
                                "stale_ms": 5000,
                            },
                        }
                    }
                )
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            """Complete an original software attempt and retain its sole dispatch identity."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/execute":
                calls.append(body["attempt_id"])
                self.reply({"execution_id": body["attempt_id"]})
            elif self.path == "/status":
                self.reply({"state": "COMPLETED", "detail": "synthetic call only"})
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()

    def launch(binary: Path, arguments: list[str], name: str, env: dict[str, str]) -> None:
        """Own bounded private children and preserve their logs without touching field processes."""
        with (output / (name + ".log")).open("wb") as log:
            child = subprocess.Popen(
                ["prlimit", "--as=2147483648", "--cpu=120", str(binary), *arguments],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
        children.append(child)

    def exchange(
        path: str, body: dict[str, Any] | None = None, *, restricted: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """Retain exact authenticated public replies and never retry a mutation."""
        status, document = request(
            origin,
            verifier if restricted else admin,
            path,
            body,
            headers={"X-RoboGuide-Tenant": "local"} if restricted else None,
        )
        with (output / "http.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "path": path,
                        "restricted": restricted,
                        "input": body,
                        "status": status,
                        "response": document,
                    }
                )
                + "\n"
            )
        return status, document

    def get(path: str) -> dict[str, Any]:
        """Require one actual successful private Controller read."""
        status, document = exchange(path)
        if status != 200:
            raise ValueError(f"private Controller {path}: {status}: {document}")
        return document

    identity = {
        "source_id": SOURCE,
        "source_revision": 1,
        "tenant_id": "local",
        "certificate_sha256": "sha256:" + digests["viewer"],
    }
    transport = HttpMissionController(origin, 0.2, tls_context=verifier)
    worker = PostureGoalVerifier(
        PostureVerificationStore(output / "mi.sqlite3"), transport, identity
    )

    def task_status(identity: str) -> str:
        """Observe Task lifecycle through original public Mission identity."""
        return str(get("/v1/missions/" + identity)["tasks"][0]["status"])

    def submit(identity: str) -> None:
        """Freeze explicit expected_posture before the exact production client admission POST."""
        raw = plan(identity)
        raw["tasks"] = raw["tasks"][:1]
        operation = {"namespace": "lab", "name": "report-posture", "version": "v1"}
        task = raw["tasks"][0]
        task["satisfaction"] = {
            "expected_effect": predicate_for("standing"),
            "basis": "verifier-evidence",
            "verifier": {
                "contract": VERIFIER,
                "predicate": predicate_for("standing"),
                "max_evidence_age_ms": 5000,
            },
        }
        role = task["roles"][0]
        role["requirements"]["capabilities"][0]["contract"] = operation
        role["execution_intent"].update(
            operation=operation,
            objective="Complete one software call; no physical effect",
            parameters={"expected_posture": "standing"},
        )
        parsed = MissionPlan.from_json(raw)
        worker.prepare(parsed, "local")
        receipt = HttpMissionController(origin, 3, tls_context=admin).submit_plan(parsed)
        assert receipt.accepted, receipt
        wait_for(
            lambda: task_status(identity) == "AwaitingSatisfaction", "local software completion"
        )

    try:
        launch(
            controller,
            [
                f"127.0.0.1:{grpc_port}",
                str(output / "controller.sqlite3"),
                f"127.0.0.1:{http_port}",
                f"127.0.0.1:{artifact_port}",
                str(output / "artifacts"),
            ],
            "controller",
            environment,
        )
        wait_for(lambda: get("/readyz")["ready"], "private Controller readiness")
        configuration = node_config(
            output, NODE, f"127.0.0.1:{grpc_port}", f"http://127.0.0.1:{server.server_port}"
        )
        for before, after in {
            "compute.hash-files@v1": "lab.report-posture@v1",
            'id = "admission"': 'id = "robot-state"',
            "posix-compute": "robot-observer",
            'object_class = "world"': 'object_class = "node"',
            'object_type = "software-work-admission"': 'object_type = "robot"',
            'semantic = "observed"': 'semantic = "reported"',
            'payload_schema = "roboguide.software-work-admission/v0.1"': (
                'payload_schema = "lite3.robot-state/v0.1"'
            ),
        }.items():
            configuration = configuration.replace(before, after)
        configuration = configuration.replace(
            f'server_endpoint = "http://127.0.0.1:{grpc_port}"',
            f'server_endpoint = "https://127.0.0.1:{grpc_port}"',
        )
        config_path = output / "node.toml"
        config_path.write_text(configuration)
        node_env = {
            key: value for key, value in environment.items() if not key.startswith("ROBOGUIDE_")
        }
        node_env.update(
            {
                "ROBOGUIDE_NODE_MTLS_CLIENT_CERT": str(certificates / "enrolment-first.crt"),
                "ROBOGUIDE_NODE_MTLS_CLIENT_KEY": str(certificates / "enrolment-first.key"),
                "ROBOGUIDE_NODE_MTLS_SERVER_CA": str(certificates / "ca.crt"),
                "ROBOGUIDE_NODE_MTLS_DOMAIN": "localhost",
            }
        )
        launch(node, [str(config_path)], "node", node_env)
        wait_for(
            lambda: (
                inspect_posture(
                    get("/v1/state/records?object_class=node&object_type=robot"),
                    get("/v1/inventory"),
                    node_id=NODE,
                )["status"]
                == "reported"
            ),
            "actual reported State",
        )
        adoption = {
            "schema": "roboguide.node-identity-adoption/v0.1",
            "command_id": "adopt-posture-software-node",
            "registry_id": "posture-software-deployment",
            "expected_bindings": {NODE: digests["enrolment-first"]},
            "owners": {NODE: {"tenant_id": "local", "domain_id": "software", "expires_at_ms": 0}},
        }
        assert exchange("/v1/node-identities/adopt", adoption)[0] == 200
        registry = {
            "command_id": "install-posture-verifier",
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                "registry_id": "software-posture",
                "revision": 1,
                "sources": {
                    SOURCE: {
                        "source_id": SOURCE,
                        "revision": 1,
                        "tenant_id": "local",
                        "producer_principals": [PRINCIPAL],
                        "verifier": VERIFIER,
                        "predicates": [predicate_for("standing")],
                        "max_age_ms": 5000,
                        "stop_timeout_ms": 5000,
                        "enabled": True,
                        "continuous": False,
                        "require_artifact": False,
                    }
                },
            },
        }
        assert exchange("/v1/verifier-sources", registry)[0] == 200
        for path, body in [
            ("/v1/missions", {}),
            ("/v1/verifier-sources", registry),
            ("/v1/missions/foreign/cancel", {}),
        ]:
            assert exchange(path, body, restricted=True)[0] == 403, path
        submit("mi-posture-positive")
        worker.step()
        anchor = worker.store.jobs()[0]["state"]["anchor"]
        sample["posture"] = "standing"
        wait_for(
            lambda: (worker.step(), worker.store.jobs()[0]["status"] == "complete")[1],
            "two fresh positive observations",
        )
        wait_for(
            lambda: task_status("mi-posture-positive") == "Completed", "positive goal settlement"
        )
        sample["posture"] = "down"
        submit("mi-posture-negative")
        started = time.monotonic()
        wait_for(
            lambda: (
                worker.step(),
                all(job["status"] == "complete" for job in worker.store.jobs()),
            )[1],
            "original ten-second negative observation window",
        )
        assert time.monotonic() - started >= 10
        wait_for(
            lambda: task_status("mi-posture-negative") == "SatisfactionRejected",
            "negative Task satisfaction settlement",
        )
        assert get("/v1/missions/mi-posture-negative")["status"] == "Failed"
        PostureGoalVerifier(worker.store, transport, identity).step()
        assert len(calls) == 2 and len(set(calls)) == 2, calls
        result = {
            "schema_version": "roboguide.mi-posture-process-acceptance/v0.1",
            "hardware_contacted": False,
            "checks": [
                "mTLS least-privilege verifier",
                "original wire admission binding",
                "two new same-source reports",
                "ten-second negative window",
                "actual Task Completed/SatisfactionRejected and Mission Failed",
                "durable receipt and restart without action repeat",
            ],
            "anchor": anchor,
            "jobs": worker.store.jobs(),
            "original_dispatches": calls,
            "binary_sha256": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (controller, node)
            },
        }
        (output / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        worker.close()
        for child in reversed(children):
            stop(child)
        server.shutdown()
        server.server_close()
        serving.join(timeout=3)


def main() -> None:
    """Require explicit private binaries and a new evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(
        arguments.controller.resolve(), arguments.node.resolve(), arguments.output.resolve()
    )
    print(json.dumps({"checks": result["checks"], "hardware_contacted": False}))


if __name__ == "__main__":
    main()
