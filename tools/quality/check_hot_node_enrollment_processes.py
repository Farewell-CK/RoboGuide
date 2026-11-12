"""Prove hot Node enrollment with owned mTLS Controller and Node processes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
import ssl
import subprocess
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_identity_processes import client, pki, ports, request, stop

ROOT = Path(__file__).resolve().parents[2]


def run(directory: Path, controller: Path, node: Path, previous: Path) -> dict[str, Any]:
    """Admit a new Node beside a running Task, then verify cold and old-writer fences."""
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    certificates = directory / "pki"
    digests = pki(certificates)
    admin, viewer = client(certificates, "admin"), client(certificates, "viewer")
    grpc_port, http_port, artifact_port = ports(3)
    origin = f"https://127.0.0.1:{http_port}"
    bindings = {"existing": digests["enrolment-first"]}
    role_map = directory / "operator-roles.json"
    role_map.write_text(
        json.dumps(
            {
                "sha256:" + digests["admin"]: {
                    "role": "recovery-admin",
                    "tenant_ids": ["*"],
                },
                "sha256:" + digests["viewer"]: "viewer",
            }
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
    env.update(
        {
            "TOKIO_WORKER_THREADS": "2",
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(bindings),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(role_map),
        }
    )
    processes: list[subprocess.Popen[bytes]] = []
    fixture_done = threading.Event()

    class Observation(BaseHTTPRequestHandler):
        """Expose only synthetic host facts; no Pilot, ROS or actuator interface exists."""

        def log_message(self, *_args: Any) -> None:
            """Keep fixture polling out of the finite retained process logs."""

        def do_GET(self) -> None:
            """Hold the first readonly workflow until the new participant has finished."""
            existing = self.path == "/existing"
            if self.path not in {"/existing", "/new"}:
                self.send_error(404)
                return
            value = {
                "node_id": "existing" if existing else "new",
                "health_state": "ONLINE",
                "readiness_state": "READY",
                "execution_state": "RUNNING"
                if existing and not fixture_done.is_set()
                else "SUCCEEDED",
                "detail": "owned synthetic readonly enrollment inspection; no physical robot",
            }
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Observation)
    thread = threading.Thread(target=fixture.serve_forever)
    thread.start()

    def launch(
        binary: Path, args: list[str], name: str, environment: dict[str, str]
    ) -> subprocess.Popen[bytes]:
        """Launch only an exact selected executable with private state and bounded owned logs."""
        with (directory / (name + ".log")).open("wb") as stream:
            process = subprocess.Popen(
                [str(binary), *args], cwd=ROOT, env=environment, stdout=stream, stderr=stream
            )
        processes.append(process)
        return process

    def exchange(
        path: str,
        body: dict[str, Any] | None = None,
        identity: ssl.SSLContext = admin,
        tenant: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Record exact public mTLS commands and replies; a request never invents device state."""
        status, value = request(
            origin, identity, path, body, {"X-RoboGuide-Tenant": tenant} if tenant else {}
        )
        with (directory / "http.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"path": path, "body": body, "status": status, "response": value}) + "\n"
            )
        return status, value

    def await_value(read: Any, predicate: Any, timeout: float = 30) -> Any:
        """Observe a specific owned live process set without restarting on observation failure."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("an owned process exited; inspect retained logs")
            try:
                value = read()
                if predicate(value):
                    return value
            except (OSError, ValueError, StopIteration):
                pass
            time.sleep(0.1)
        raise TimeoutError("owned enrollment process observation timed out")

    def start_node(node_id: str, leaf: str) -> subprocess.Popen[bytes]:
        """Start a real generic Node with its own leaf, journal and readonly fixture workflow."""
        fragment = (ROOT / "integrations/robonix-task/deploy/host-node.fragment.toml").read_text()
        fragment = fragment.replace("NODE_ID", node_id).replace(
            "127.0.0.1:18112", f"127.0.0.1:{fixture.server_port}"
        )
        fragment = fragment.replace("/v1/host/status", "/" + node_id)
        fragment = fragment.replace(
            "system.host.inspect", "test.enrollment-" + node_id + ".inspect"
        )
        config = directory / (node_id + ".toml")
        config.write_text(
            'schema="roboguide.node-config/v0.14"\n'
            f'node_id="{node_id}"\nserver_endpoint="https://127.0.0.1:{grpc_port}"\n'
            f'state_directory="{directory / (node_id + "-state")}"\nreconnect_delay_ms=100\n'
            + fragment
        )
        environment = {k: v for k, v in env.items() if not k.startswith("ROBOGUIDE_")}
        environment.update(
            {
                "ROBOGUIDE_NODE_MTLS_CLIENT_CERT": str(certificates / (leaf + ".crt")),
                "ROBOGUIDE_NODE_MTLS_CLIENT_KEY": str(certificates / (leaf + ".key")),
                "ROBOGUIDE_NODE_MTLS_SERVER_CA": str(certificates / "ca.crt"),
                "ROBOGUIDE_NODE_MTLS_DOMAIN": "localhost",
            }
        )
        return launch(node, [str(config)], node_id, environment)

    def submit(node_id: str) -> dict[str, Any]:
        """Submit a normal Mission using the operation unique to its fixture participant."""
        plan = json.loads(
            (ROOT / "integrations/robonix-task/deploy/host-mission-plan.json").read_text()
        )
        plan["mission"]["id"] = "hot-enrollment-" + node_id
        plan["mission"]["actors"] = plan["mission"]["actors"][:1]
        plan["contexts"][0]["roles"] = plan["contexts"][0]["roles"][:1]
        task = plan["tasks"][0]
        task["roles"] = task["roles"][:1]
        task["timing"]["completion_deadline_offset_ms"] = 180000
        role = task["roles"][0]
        for operation in (
            role["requirements"]["capabilities"][0]["contract"],
            role["execution_intent"]["operation"],
        ):
            operation["namespace"] = "test.enrollment-" + node_id
        status, response = exchange(
            "/v1/missions/assess-initial-support",
            plan,
            tenant="local" if node_id == "new" else None,
        )
        assert status == 200, response
        status, response = exchange(
            "/v1/missions", plan, tenant="local" if node_id == "new" else None
        )
        assert status == 202, response
        (directory / (node_id + "-plan.json")).write_text(json.dumps(plan, indent=2))
        return response

    def state_epoch() -> str:
        """Read the original session epoch from the actual Node State export."""
        rows = exchange("/v1/state/records")[1]["records"]
        return str(
            next(
                r["value"]["source_epoch"]
                for r in rows
                if r["object"]["object_type"] == "host-status"
                and r["object"]["object_id"] == "existing"
            )
        )

    database = directory / "controller.sqlite3"
    args = [
        f"127.0.0.1:{grpc_port}",
        str(database),
        f"127.0.0.1:{http_port}",
        f"127.0.0.1:{artifact_port}",
        str(directory / "artifacts"),
    ]
    try:
        current = launch(controller, args, "controller-hot", env)
        await_value(lambda: exchange("/readyz"), lambda v: v[0] == 200 and v[1]["ready"])
        existing = start_node("existing", "enrolment-first")
        await_value(
            lambda: exchange("/v1/inventory")[1],
            lambda v: any(
                n["node_id"] == "existing" and n["liveness"] == "Reachable" for n in v["nodes"]
            ),
        )
        await_value(state_epoch, bool)
        epoch = state_epoch()
        submit("existing")
        await_value(
            lambda: exchange("/v1/missions/hot-enrollment-existing")[1],
            lambda v: v["status"] == "Running",
        )
        original_allocations = exchange("/v1/resource-allocations")[1]["allocations"]
        assert len(original_allocations) == 1
        adoption: dict[str, Any] = {
            "schema": "roboguide.node-identity-adoption/v0.1",
            "command_id": "adopt-original-static",
            "registry_id": "hot-deployment",
            "expected_bindings": bindings,
            "owners": {
                "existing": {"tenant_id": "local", "domain_id": "domain", "expires_at_ms": 0}
            },
        }
        assert exchange("/v1/node-identities/adopt", adoption, viewer)[0] == 403
        wrong = copy.deepcopy(adoption)
        wrong["expected_bindings"]["existing"] = digests["reenrolment"]
        assert exchange("/v1/node-identities/adopt", wrong)[0] == 409
        assert exchange("/v1/node-identities")[1]["registry"] is None
        status, adopted = exchange("/v1/node-identities/adopt", adoption)
        assert status == 200, adopted
        assert exchange("/v1/node-identities/adopt", adoption) == (status, adopted)
        assert exchange("/v1/resource-allocations")[1]["allocations"] == original_allocations
        assert state_epoch() == epoch and current.poll() is None and existing.poll() is None
        new = start_node("new", "enrolment-next")
        await_value(
            lambda: (directory / "new.log").read_text(),
            lambda v: "Unauthenticated" in v or "not admitted" in v,
        )
        assert all(n["node_id"] != "new" for n in exchange("/v1/inventory")[1]["nodes"])
        register = {
            "command_id": "register-new",
            "node_id": "new",
            "expected_revision": 0,
            "action": {
                "kind": "register",
                "tenant_id": "local",
                "domain_id": "domain",
                "digest": digests["enrolment-next"],
                "expires_at_ms": 0,
            },
        }
        assert exchange("/v1/node-identities/commands", register)[0] == 200
        await_value(
            lambda: exchange("/v1/inventory")[1],
            lambda v: (
                len(
                    [
                        n
                        for n in v["nodes"]
                        if n["liveness"] == "Reachable" and n["reported_health"] == "Online"
                    ]
                )
                == 2
            ),
        )
        submit("new")
        await_value(
            lambda: exchange("/v1/missions/hot-enrollment-new")[1],
            lambda v: v["status"] == "Completed",
        )
        assert exchange("/v1/missions/hot-enrollment-existing")[1]["status"] == "Running"
        assert state_epoch() == epoch
        hot_pids = {"controller": current.pid, "existing_node": existing.pid, "new_node": new.pid}
        fixture_done.set()
        await_value(
            lambda: exchange("/v1/missions/hot-enrollment-existing")[1],
            lambda v: v["status"] == "Completed",
        )
        assert exchange("/v1/resource-allocations")[1]["allocations"] == []
        attempts = exchange("/v1/execution-attempts")[1]["attempts"]
        assert len(attempts) == 2 and all(a["status"] == "Completed" for a in attempts)
        stop(current)
        processes.remove(current)
        with (
            closing(sqlite3.connect(database)) as db,
            closing(sqlite3.connect(directory / "old-writer.sqlite3")) as target,
        ):
            db.backup(target)
        old_args = args.copy()
        old_args[1] = str(directory / "old-writer.sqlite3")
        old = launch(previous, old_args, "old-writer-refusal", env)
        assert old.wait(timeout=15) != 0
        processes.remove(old)
        assert (
            "adoption" in (directory / "old-writer-refusal.log").read_text()
            or "schema" in (directory / "old-writer-refusal.log").read_text()
        )
        current = launch(controller, args, "controller-cold-original-static-profile", env)
        await_value(lambda: exchange("/readyz"), lambda v: v[0] == 200 and v[1]["ready"])
        await_value(
            lambda: exchange("/v1/inventory")[1],
            lambda v: len([n for n in v["nodes"] if n["liveness"] == "Reachable"]) == 2,
        )
        assert exchange("/v1/execution-attempts")[1]["attempts"] == attempts
        assert exchange("/v1/node-identities/adopt", adoption) == (status, adopted)
        assert exchange("/v1/resource-allocations")[1]["allocations"] == []
        assert exchange("/readyz")[1]["unknown_attempts"] == 0
        result = {
            "passed": True,
            "physical_robot_used": False,
            "hot_phase_pids": hot_pids,
            "original_node_epoch_preserved_during_adoption": epoch,
            "original_running_task_ownership_preserved": True,
            "new_node_authenticated_after_register": True,
            "cold_original_static_profile_preserved_dynamic_enrollment": True,
            "old_writer_refused": True,
            "attempts": attempts,
            "sha256": {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (controller, node, previous, Path(__file__))
            },
        }
        (directory / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        fixture_done.set()
        for process in reversed(processes):
            stop(process)
        fixture.shutdown()
        fixture.server_close()
        thread.join()


def main() -> None:
    """Require exact current/old executables and a fresh private software evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--previous-controller", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        args.controller.resolve(strict=True),
        args.node.resolve(strict=True),
        args.previous_controller.resolve(strict=True),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
