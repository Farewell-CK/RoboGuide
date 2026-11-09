"""Exercise online physical/feasibility publications with owned mTLS processes and no robots."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_identity_processes import client, pki, ports, request, stop

ROOT = Path(__file__).resolve().parents[2]


def file_hash(path: Path) -> str:
    """Hash the exact selected immutable executable or source without loading it into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(output: Path, controller: Path, node: Path, previous: Path) -> dict[str, Any]:
    """Preserve an old running binding while new evidence admits another physical participant."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    frozen_binaries = {str(p): file_hash(p) for p in (controller, node, previous)}
    source_paths = [
        Path(__file__),
        *(
            ROOT / name
            for name in (
                "apps/integration-server/src/application/deployment_management.rs",
                "apps/integration-server/src/application/physical_entity_registry.rs",
                "apps/integration-server/src/application/plan_revision/checkpoint.rs",
                "apps/integration-server/src/controller_http/server.rs",
                "apps/integration-server/src/main.rs",
                "core/control/src/deployment_publication.rs",
                "core/control/src/decision_policy.rs",
                "core/domain/src/registry_command.rs",
            )
        ),
    ]
    frozen_sources = {str(p): file_hash(p) for p in source_paths}
    certificates = output / "pki"
    digests = pki(certificates)
    admin, viewer = client(certificates, "admin"), client(certificates, "viewer")
    grpc, http, artifact = ports(3)
    origin = f"https://127.0.0.1:{http}"
    roles = output / "roles.json"
    roles.write_text(
        json.dumps(
            {
                "sha256:" + digests["admin"]: {"role": "recovery-admin", "tenant_ids": ["*"]},
                "sha256:" + digests["viewer"]: "viewer",
            }
        )
    )
    physical: dict[str, Any] = {
        "schema": "roboguide.physical-entity-registry/v0.1",
        "registry_id": "software-entities",
        "revision": 1,
        "routing_profile": "one-routable-entity-per-node",
        "entities": [{"entity_id": "entity-existing", "node_id": "existing"}],
    }
    physical_path = output / "physical-current.json"
    physical_path.write_text(json.dumps(physical))
    sources = [
        json.loads(
            (
                ROOT
                / "contracts/deployment-management/v0.1/fixtures"
                / f"feasibility-v{revision}.json"
            ).read_text()
        )
        for revision in (1, 2)
    ]
    startup_source = output / "feasibility-startup.json"
    startup_source.write_text(json.dumps(sources[0]))
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
    environment.update(
        {
            "TOKIO_WORKER_THREADS": "2",
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(
                {"existing": digests["enrolment-first"], "new": digests["enrolment-next"]}
            ),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(roles),
            "ROBOGUIDE_DEPLOYMENT_FEASIBILITY_PATH": str(startup_source),
        }
    )
    children: list[subprocess.Popen[bytes]] = []
    finished = threading.Event()

    class Fixture(BaseHTTPRequestHandler):
        """Serve synthetic workflow observations on loopback; no device or actuator exists."""

        def log_message(self, *_args: Any) -> None:
            """Keep polling out of retained finite application logs."""

        def do_GET(self) -> None:
            """Hold only the original software workflow while the second participant completes."""
            existing = self.path == "/existing"
            if self.path not in {"/existing", "/new"}:
                self.send_error(404)
                return
            value = {
                "node_id": "existing" if existing else "new",
                "health_state": "ONLINE",
                "readiness_state": "READY",
                "execution_state": "RUNNING" if existing and not finished.is_set() else "SUCCEEDED",
                "detail": "synthetic readonly deployment publication fixture",
            }
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    def launch(
        binary: Path, argv: list[str], name: str, env: dict[str, str] = environment
    ) -> subprocess.Popen[bytes]:
        """Own only exact selected software children and their original private state/logs."""
        with (output / (name + ".log")).open("wb") as log:
            process = subprocess.Popen(
                [str(binary), *argv], cwd=ROOT, env=env, stdout=log, stderr=log
            )
        children.append(process)
        return process

    def exchange(
        path: str, body: dict[str, Any] | None = None, identity: Any = admin
    ) -> tuple[int, dict[str, Any]]:
        """Retain exact public mTLS requests and replies without inventing device facts."""
        status, value = request(origin, identity, path, body)
        with (output / "http.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"path": path, "input": body, "status": status, "response": value})
                + "\n"
            )
        return status, value

    def wait(read: Any, predicate: Any) -> Any:
        """Observe the actual owned processes under one finite wait; never restart on timeout."""
        deadline, last = time.monotonic() + 30, None
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in children):
                raise RuntimeError("owned process exited; inspect retained logs")
            try:
                last = read()
                if predicate(last):
                    return last
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise TimeoutError(f"online publication observation timed out: {last}")

    def start_node(identity: str, leaf: str) -> subprocess.Popen[bytes]:
        """Enroll a real generic software Node using a normal workflow and unique TLS/journal."""
        fragment = (ROOT / "integrations/robonix-task/deploy/host-node.fragment.toml").read_text()
        fragment = fragment.replace("NODE_ID", identity).replace(
            "127.0.0.1:18112", f"127.0.0.1:{server.server_port}"
        )
        fragment = fragment.replace("/v1/host/status", "/" + identity)
        fragment = fragment.replace("system.host.inspect", "mobility.navigate")
        fragment = fragment.replace('kind = "compute"', 'kind = "space"').replace(
            'kind = "observation"', 'kind = "mobility"'
        )
        fragment = "\n".join(
            line
            for line in fragment.splitlines()
            if not line.startswith(("allocation_mode", "resource_class"))
        )
        path = output / (identity + ".toml")
        path.write_text(
            'schema="roboguide.node-config/v0.14"\n'
            + f'node_id="{identity}"\nserver_endpoint="https://127.0.0.1:{grpc}"\n'
            + f'state_directory="{output / (identity + "-state")}"\n'
            + fragment
        )
        env = {k: v for k, v in environment.items() if not k.startswith("ROBOGUIDE_")}
        env.update(
            {
                "ROBOGUIDE_NODE_MTLS_CLIENT_CERT": str(certificates / (leaf + ".crt")),
                "ROBOGUIDE_NODE_MTLS_CLIENT_KEY": str(certificates / (leaf + ".key")),
                "ROBOGUIDE_NODE_MTLS_SERVER_CA": str(certificates / "ca.crt"),
                "ROBOGUIDE_NODE_MTLS_DOMAIN": "localhost",
            }
        )
        return launch(node, [str(path)], identity, env)

    def mission(identity: str) -> dict[str, Any]:
        """Select an explicit physical Actor and normal space reservation, never a direct action."""
        plan: dict[str, Any] = json.loads(
            (ROOT / "integrations/robonix-task/deploy/host-mission-plan.json").read_text()
        )
        plan["schema_version"] = "roboguide.mission-plan/v0.8"
        plan["mission"]["id"] = "online-" + identity
        plan["mission"]["actors"] = [{"id": "host-a", "physical_entity": "entity-" + identity}]
        plan["contexts"][0]["roles"] = plan["contexts"][0]["roles"][:1]
        task = plan["tasks"][0]
        task["roles"] = task["roles"][:1]
        task["timing"]["completion_deadline_offset_ms"] = 180000
        role = task["roles"][0]
        operation = {"namespace": "mobility", "name": "navigate", "version": "v1"}
        role["requirements"]["capabilities"][0]["contract"] = operation
        role["requirements"]["resources"] = [{"kind": "space", "units": 1}]
        role["execution_intent"].update(
            operation=operation, parameters={"destination": "fixture-goal"}
        )
        return plan

    def epoch() -> str | None:
        """Read the old participant's actual State session, independent of process liveness."""
        for record in exchange("/v1/state/records")[1]["records"]:
            if (
                record["object"]["object_type"] == "host-status"
                and record["object"]["object_id"] == "existing"
            ):
                return str(record["value"]["source_epoch"])
        return None

    database = output / "controller.sqlite3"
    argv = [
        f"127.0.0.1:{grpc}",
        str(database),
        f"127.0.0.1:{http}",
        f"127.0.0.1:{artifact}",
        str(output / "artifacts"),
        "",
        str(physical_path),
    ]
    try:
        current = launch(controller, argv, "controller-hot")
        wait(lambda: exchange("/readyz"), lambda v: v[0] == 200 and v[1]["ready"])
        existing = start_node("existing", "enrolment-first")
        wait(
            lambda: exchange("/v1/inventory")[1],
            lambda v: any(
                n["node_id"] == "existing" and n["reported_health"] == "Online" for n in v["nodes"]
            ),
        )
        status, response = exchange("/v1/missions", mission("existing"))
        assert status == 202, response
        wait(
            lambda: exchange("/v1/missions/online-existing")[1],
            lambda v: v["tasks"][0]["status"] == "Active",
        )
        allocations = exchange("/v1/resource-allocations")[1]["allocations"]
        original_epoch = wait(epoch, bool)
        new = start_node("new", "enrolment-next")
        wait(
            lambda: exchange("/v1/inventory")[1],
            lambda v: len([n for n in v["nodes"] if n["reported_health"] == "Online"]) == 2,
        )
        before_entities = exchange("/v1/physical-entities")[1]["registry"]["entities"]
        assert all(e["node_id"] != "new" for e in before_entities)
        physical["revision"] = 2
        physical["entities"].append({"entity_id": "entity-new", "node_id": "new"})
        command: dict[str, Any] = {
            "command_id": "physical-hot",
            "expected_revision": 1,
            "registry": physical,
        }
        assert exchange("/v1/physical-entities", command, viewer)[0] == 403
        status, receipt = exchange("/v1/physical-entities", command)
        assert status == 200, receipt
        assert exchange("/v1/physical-entities", command) == (status, receipt)
        conflict = copy.deepcopy(command)
        conflict.update(command_id="move-live-entity", expected_revision=2)
        conflict["registry"]["revision"] = 3
        conflict["registry"]["entities"] = [{"entity_id": "entity-existing", "node_id": "new"}]
        assert exchange("/v1/physical-entities", conflict)[0] == 409
        assert exchange("/v1/resource-allocations")[1]["allocations"] == allocations
        assert exchange("/v1/deployment-feasibility")[1]["digest"] == sources[0]["digest"]
        publication = {
            "command_id": "feasibility-hot",
            "registry_id": "software-publications",
            "expected_revision": 0,
            "revision": 1,
            "expected_physical_revision": 2,
            "document": sources[1],
        }
        assert exchange("/v1/deployment-feasibility", publication, viewer)[0] == 403
        status, published = exchange("/v1/deployment-feasibility", publication)
        assert status == 200, published
        assert exchange("/v1/deployment-feasibility", publication) == (200, published)
        status, response = exchange("/v1/missions", mission("new"))
        assert status == 202, response
        wait(lambda: exchange("/v1/missions/online-new")[1], lambda v: v["status"] == "Completed")
        assert exchange("/v1/missions/online-existing")[1]["tasks"][0]["status"] == "Active"
        assert exchange("/v1/resource-allocations")[1]["allocations"] == allocations
        assert all(p.poll() is None for p in (current, existing, new))
        assert epoch() == original_epoch
        finished.set()
        wait(
            lambda: exchange("/v1/missions/online-existing")[1],
            lambda v: v["status"] == "Completed",
        )
        attempts = exchange("/v1/execution-attempts")[1]["attempts"]
        assert len(attempts) == 2 and all(a["status"] == "Completed" for a in attempts)
        assert {(a["mission_id"], a["node_id"]) for a in attempts} == {
            ("online-existing", "existing"),
            ("online-new", "new"),
        }
        assert exchange("/v1/resource-allocations")[1]["allocations"] == []
        stop(current)
        children.remove(current)
        # Cold recovery requires the explicit current physical document, never checkpoint routing.
        failed = launch(controller, argv, "cold-old-physical-refusal")
        assert failed.wait(timeout=15) != 0
        children.remove(failed)
        assert "registry" in (output / "cold-old-physical-refusal.log").read_text()
        physical_path.write_text(json.dumps(physical))
        startup_source.write_text(json.dumps(sources[1]))
        wrong_source = launch(controller, argv, "cold-replaced-startup-refusal")
        assert wrong_source.wait(timeout=15) != 0
        children.remove(wrong_source)
        assert "startup" in (output / "cold-replaced-startup-refusal.log").read_text()
        startup_source.write_text(json.dumps(sources[0]))
        with (
            closing(sqlite3.connect(database)) as source,
            closing(sqlite3.connect(output / "prior.sqlite3")) as target,
        ):
            source.backup(target)
        prior_argv = argv.copy()
        prior_argv[1] = str(output / "prior.sqlite3")
        old = launch(previous, prior_argv, "prior-writer-refusal")
        assert old.wait(timeout=15) != 0
        children.remove(old)
        refusal = (output / "prior-writer-refusal.log").read_text()
        assert "physical_entities" in refusal or "deployment_feasibility" in refusal, refusal
        current = launch(controller, argv, "controller-cold-current-physical")
        wait(lambda: exchange("/readyz"), lambda v: v[0] == 200 and v[1]["ready"])
        wait(lambda: exchange("/v1/inventory")[1], lambda v: len(v["nodes"]) == 2)
        assert exchange("/v1/execution-attempts")[1]["attempts"] == attempts
        assert exchange("/v1/deployment-feasibility", publication) == (200, published)
        assert exchange("/v1/deployment-feasibility")[1]["digest"] == sources[1]["digest"]
        assert exchange("/v1/resource-allocations")[1]["allocations"] == []
        result = {
            "passed": True,
            "physical_robot_used": False,
            "field_deployment_changed": False,
            "hot_entity_and_feasibility_update": True,
            "existing_binding_and_ownership_preserved": True,
            "stale_physical_cold_source_refused": True,
            "actual_prior_writer_refused": True,
            "original_attempts_retained_after_cold_restart": True,
            "original_session_preserved_during_publication": original_epoch,
            "replaced_startup_source_refused": True,
            "attempts": attempts,
            "binary_sha256": frozen_binaries,
            "source_sha256": frozen_sources,
        }
        assert frozen_binaries == {str(p): file_hash(p) for p in (controller, node, previous)}
        assert frozen_sources == {str(p): file_hash(p) for p in source_paths}
        (output / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        finished.set()
        for process in reversed(children):
            stop(process)
        server.shutdown()
        server.server_close()
        thread.join()


def main() -> None:
    """Require exact current/prior software executables and a fresh private Linux evidence root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--previous-controller", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output.resolve(),
                args.controller.resolve(strict=True),
                args.node.resolve(strict=True),
                args.previous_controller.resolve(strict=True),
            )
        )
    )


if __name__ == "__main__":
    main()
