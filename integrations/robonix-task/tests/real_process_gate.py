"""Exercise real RG processes against isolated fake Atlas/Pilot; no robot endpoints exist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import jsonschema
from robonix_task.adapter import Adapter, Server
from robonix_task.protocol import Liaison
from test_task_results import TaskFixture


def port():
    """Reserve an ephemeral loopback address briefly for one owned child process."""
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def await_read(read, predicate, children, timeout=20):
    """Retry only bounded read-only observations and fail if an owned child exits."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        for child in children:
            if child.poll() is not None:
                raise AssertionError(f"owned child exited with {child.returncode}")
        try:
            last = read()
            if predicate(last):
                return last
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"bounded read did not satisfy predicate: {last}")


def stop(child):
    """Stop only the explicitly owned software child, escalating after a short bounded wait."""
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run_case(args, manual):
    """Submit once, cancel through RG, check allocations, restart, and reject a prior writer."""
    root = args.output / ("manual" if manual else "automatic")
    root.mkdir(parents=True, exist_ok=False)
    package = Path(__file__).resolve().parents[1]
    repository = package.parents[1]
    fixture = TaskFixture(root, mode="planning-cancel")
    app = Adapter(
        root / "pilot-journal",
        Liaison(fixture.endpoint, "rg/fixture", "local:test"),
        allowed_operations=("task.delegate@v1",),
        enabled=True,
        task_results=True,
        task_profile="local-task-v1",
    )
    server = Server(0, app)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    grpc_port, http_port = port(), port()
    origin = f"http://127.0.0.1:{http_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ROBOGUIDE_")
    }
    if manual:
        sys.path.insert(0, str(repository / "tools/quality"))
        from check_identity_processes import client, pki

        certificates = root / "pki"
        leaves = pki(certificates)
        policy = root / "operator.json"
        policy.write_text(
            json.dumps(
                {
                    "schema_version": "roboguide.operator-authorization/v0.1",
                    "registry_id": "synthetic-settlement-operator",
                    "revision": 1,
                    "grants": {
                        "sha256:" + leaves["admin"]: {
                            "principal_id": "fixture-settlement-administrator",
                            "role": "recovery-admin",
                            "tenant_ids": ["*"],
                        }
                    },
                }
            )
        )
        for suffix, value in {
            "SERVER_CERT": certificates / "server.crt",
            "SERVER_KEY": certificates / "server.key",
            "CLIENT_CA": certificates / "ca.crt",
            "ROLE_MAP": policy,
        }.items():
            environment["ROBOGUIDE_OPERATOR_MTLS_" + suffix] = str(value)
        origin = f"https://127.0.0.1:{http_port}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=client(certificates, "admin")),
        )
    children, logs = [], []

    def exchange(path, body=None):
        """Publish mutations exactly once and retain each actual Controller response."""
        request = urllib.request.Request(
            origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=5) as response:
            value = json.load(response)
        with (root / "http-observations.jsonl").open("a") as stream:
            stream.write(
                json.dumps({"received_ns": time.time_ns(), "path": path, "value": value}) + "\n"
            )
        return value

    def start_controller(binary, name):
        """Launch one frozen executable with private SQL and exclusively loopback listeners."""
        output = (root / (name + ".log")).open("wb")
        logs.append(output)
        child = subprocess.Popen(
            [
                str(binary),
                f"127.0.0.1:{grpc_port}",
                str(root / "controller.sqlite3"),
                f"127.0.0.1:{http_port}",
                "127.0.0.1:0",
                str(root / "artifacts"),
            ],
            cwd=root,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        children.append(child)
        return child

    try:
        controller = start_controller(args.controller, "controller-first")
        await_read(lambda: exchange("/v1/inventory"), lambda _: True, children)
        fragment = (package / "deploy/node.task.fragment.toml").read_text(encoding="utf-8")
        if manual:
            begin = fragment.index("[local_systems.metadata]")
            end = fragment.index("[local_systems.health]", begin)
            fragment = fragment[:begin] + fragment[end:]
            begin = fragment.index("[[state_exports]]")
            end = fragment.index("[[resources]]", begin)
            fragment = fragment[:begin] + fragment[end:]
        fragment = fragment.replace(
            "http://127.0.0.1:18112", f"http://127.0.0.1:{server.server_address[1]}"
        )
        fragment = fragment.replace("/v1/pilot/", "/v1/")
        config = root / "node.toml"
        config.write_text(
            'schema="roboguide.node-config/v0.13"\nnode_id="lite3-dog2"\n'
            + f'server_endpoint="http://127.0.0.1:{grpc_port}"\n'
            + f"state_directory={json.dumps(str(root / 'node-state'))}\n"
            + "reconnect_delay_ms=100\n"
            + fragment
        )
        output = (root / "node.log").open("wb")
        logs.append(output)
        node = subprocess.Popen(
            [str(args.node), str(config)],
            cwd=root,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        children.append(node)
        await_read(
            lambda: exchange("/v1/inventory"), lambda value: bool(value.get("nodes")), children
        )
        plan = json.loads(
            (repository / "scenarios/local-task-delegation/mission-plan.json").read_text()
        )
        mid = "fixture-manual-cancel" if manual else "fixture-automatic-cancel"
        plan["mission"]["id"] = mid
        plan["mission"]["objective"] = "Cancel a synthetic local task with no device provider."
        plan["tasks"][0]["roles"][0]["execution_intent"]["objective"] = plan["mission"]["objective"]
        exchange("/v1/missions", plan)
        attempts = await_read(
            lambda: exchange(f"/v1/missions/{mid}/execution-attempts"),
            lambda value: bool(value.get("attempts")),
            children,
        )
        eid = attempts["attempts"][0]["execution_id"]
        await_read(
            lambda: (
                app.journal.no_effects_observations() if not fixture.tasks else len(fixture.tasks)
            ),
            lambda value: value == 1,
            children,
        )
        exchange(f"/v1/missions/{mid}/cancel", {})
        if manual:
            await_read(
                lambda: exchange(f"/v1/executions/{eid}"),
                lambda value: value.get("status") == "Cancelled",
                children,
            )
            view = exchange(f"/v1/executions/{eid}/safety-stop")
            assert view["purpose"]["record"]["phase"] == "manual_escalation", view
            assert exchange("/v1/resource-allocations")["allocations"]
            body = {
                "schema_version": "roboguide.stop-reconciliation/v0.1",
                "command_id": "synthetic-operator-settlement",
                "expected_record_digest": view["record_digest"],
                "execution_generation": view["purpose"]["record"]["identity"][
                    "execution_generation"
                ],
                "confirmed": True,
                "disposition": "no-effects",
                "attestation": "Synthetic operator verified original interruption and zero plans.",
                "evidence": app.journal.no_effects_observations(),
            }
            jsonschema.validate(
                body,
                json.loads(
                    (
                        repository
                        / "contracts/operator/stop-reconciliation-v0.1/command.schema.json"
                    ).read_text()
                ),
            )
            receipt = exchange(f"/v1/executions/{eid}/safety-stop/reconcile", body)
            assert receipt == exchange(f"/v1/executions/{eid}/safety-stop/reconcile", body)
        await_read(
            lambda: exchange(f"/v1/missions/{mid}"),
            lambda value: value.get("status") == "Cancelled",
            children,
        )
        assert not exchange("/v1/resource-allocations")["allocations"]
        view = exchange(f"/v1/executions/{eid}/safety-stop")
        assert view["purpose"]["record"]["confirmed_at"] is None, view
        assert "settlement" in view["purpose"], view
        if not manual:
            observed = view["purpose"]["settlement"]["basis"]["proof"]["record"]["value"]
            jsonschema.validate(
                observed,
                json.loads(
                    (
                        repository / "contracts/state/safe-state-observation-v0.4/schema.json"
                    ).read_text()
                ),
            )
        assert len(fixture.tasks) == 1 and len(fixture.aborts) == 1
        stop(controller)
        children.remove(controller)
        controller = start_controller(args.controller, "controller-restored")
        await_read(
            lambda: exchange(f"/v1/missions/{mid}"),
            lambda value: value.get("status") == "Cancelled",
            children,
        )
        assert not exchange("/v1/resource-allocations")["allocations"]
        assert len(fixture.tasks) == 1
        stop(node)
        children.remove(node)
        stop(controller)
        children.remove(controller)
        if args.old_controller:
            db = sqlite3.connect(root / "controller.sqlite3")
            before = "\n".join(db.iterdump())
            db.close()
            old = start_controller(args.old_controller, "old-controller-refusal")
            old.wait(timeout=15)
            assert old.returncode != 0, "prior writer accepted new settlement authority"
            old_log = (root / "old-controller-refusal.log").read_text()
            assert "unknown field" in old_log and "settlement" in old_log, old_log
            db = sqlite3.connect(root / "controller.sqlite3")
            assert "\n".join(db.iterdump()) == before, "prior writer changed original SQL authority"
            db.close()
        result = {
            "case": "manual" if manual else "automatic",
            "mission_id": mid,
            "execution_id": eid,
            "native_submissions": len(fixture.tasks),
            "native_aborts": len(fixture.aborts),
            "mission_status": "Cancelled",
            "allocations": 0,
            "physical_stop_confirmed": False,
            "cold_restart": "passed",
            "old_writer": "refused" if args.old_controller else "not-run",
        }
        (root / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        for child in reversed(children):
            stop(child)
        for output in logs:
            output.close()
        server.shutdown()
        server.server_close()
        worker.join()
        app.close()
        fixture.close()


def main():
    """Require explicit binaries and a new evidence directory, then run both software cases."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--old-controller", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    for binary in [args.controller, args.node, args.old_controller]:
        if binary is not None:
            assert binary.is_absolute() and binary.is_file(), binary
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        str(binary): hashlib.sha256(binary.read_bytes()).hexdigest()
        for binary in [args.controller, args.node, args.old_controller]
        if binary is not None
    }
    (args.output / "binaries.json").write_text(json.dumps(manifest, indent=2))
    for manual in [False, True]:
        run_case(args, manual)


if __name__ == "__main__":
    main()
