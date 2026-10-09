"""Exercise actual Controller and Node processes with synthetic Local EAIOS HTTP facts.

This is an offline process-conformance check, not a B1 experiment. Its authored
generic navigation plans and manually controlled execution outcomes are fixture
data. It never starts Mission Intelligence, a Provider or a simulator. A fresh
output directory retains process logs, Node journals and Controller checkpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations/habitat-local-eaios"))
from habitat_local_eaios.execution_recovery import execution_recovery_profile  # noqa: E402
from habitat_local_eaios.recovery_deployment import (  # noqa: E402
    prepare_deployment,
    verify_deployment,
)


def save(path: Path, value: Any) -> None:
    """Retain nonsecret fixture facts in the fresh process-check directory."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def request(base: str, path: str, body: Any = None) -> dict[str, Any]:
    """Use bounded loopback HTTP without proxy credentials or automatic POST retries."""
    data = None if body is None else json.dumps(body).encode()
    call = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        call, timeout=3
    ) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("oversized process-check response")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("expected process-check object response")
    return value


def await_condition(condition: Callable[[], Any], description: str) -> Any:
    """Poll a bounded local condition; transport gaps remain evidence, never another POST."""
    deadline = time.monotonic() + 30
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = condition()
            if value:
                return value
        except (OSError, ValueError) as failure:
            last = failure
        time.sleep(0.1)
    raise RuntimeError(f"process check timed out: {description}; last read error: {last}")


class LocalFixture(ThreadingHTTPServer):
    """A bounded controllable fixture; Cancel acceptance intentionally does not stop work."""

    def __init__(self, path: Path) -> None:
        """Bind an ephemeral local listener, preserving every dispatched invocation."""
        self.path = path
        self.executions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), FixtureHandler)

    @property
    def endpoint(self) -> str:
        """Expose only this fixture's owned loopback endpoint."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def records(self) -> list[dict[str, Any]]:
        """Read a defensive snapshot independently of ongoing Node polling."""
        with self.lock:
            return json.loads(json.dumps(list(self.executions.values())))  # type: ignore[no-any-return]

    def transition(self, state: str) -> None:
        """Emit explicit synthetic terminal facts; never infer them from Cancel receipts."""
        with self.lock:
            for record in self.executions.values():
                if record["state"] == "RUNNING":
                    record["state"] = state
            save(self.path, list(self.executions.values()))


class FixtureHandler(BaseHTTPRequestHandler):
    """Implement the actual configured Node operation workflow HTTP surface."""

    def respond(self, value: Any, status: int = 200) -> None:
        """Return a framed JSON response without logging workflow bodies."""
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        """Expose synthetic readiness and declaration, without execution truth inflation."""
        if self.path == "/v1/health":
            self.respond({"state": "ONLINE", "detail": "synthetic process fixture"})
        elif self.path.startswith("/v1/capabilities/"):
            self.respond({"state": "READY", "detail": "synthetic process fixture"})
        elif self.path == "/v1/executions/recovery-support":
            self.respond(execution_recovery_profile(shared_world=True, retain_stopped_session=True))
        elif self.path == "/v1/executions/progress":
            self.respond({"schema_version": "roboguide.execution-progress/v0.1", "executions": []})
        else:
            self.respond({"error": "unknown fixture route"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """Record real Node workflow dispatch/cancel; expose terminal facts only when directed."""
        server: Any = self.server
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw)
        with server.lock:
            if self.path == "/v1/executions":
                if len(server.executions) >= 4:
                    self.respond({"error": "fixture budget exhausted"}, 409)
                    return
                handle = f"fixture-{len(server.executions) + 1}"
                record = {
                    "execution_id": handle,
                    "state": "RUNNING",
                    "detail": "synthetic work",
                    "invocation": body["invocation"],
                    "cancel_count": 0,
                }
                server.executions[handle] = record
                save(server.path, list(server.executions.values()))
                self.respond(record)
            elif self.path in {"/v1/executions/status", "/v1/executions/cancel"}:
                record = server.executions[body["execution_id"]]
                if self.path.endswith("/cancel"):
                    record["cancel_count"] += 1
                    save(server.path, list(server.executions.values()))
                self.respond(record)
            else:
                self.respond({"error": "unknown fixture route"}, 404)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep polling noise and complete invocation bodies out of console output."""


def generic_plan(mission: str) -> dict[str, Any]:
    """Declare two independent resource-bearing tasks, with no benchmark or executor constraint."""
    operation = {"namespace": "mobility", "name": "move", "version": "v1"}
    return {
        "schema_version": "roboguide.mission-plan/v0.8",
        "mission": {
            "id": mission,
            "objective": "independent synthetic process work",
            "actors": [{"id": f"actor-{i}"} for i in range(2)],
        },
        "contexts": [
            {
                "id": "context",
                "roles": [{"id": f"participant-{i}", "actor": f"actor-{i}"} for i in range(2)],
                "relations": [],
                "coupling_mode": "independent",
                "executor_constraints": [],
            }
        ],
        "tasks": [
            {
                "id": f"task-{i}",
                "description": "synthetic navigation",
                "depends_on": [],
                "context_id": "context",
                "coupling_mode": "independent",
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": None,
                },
                "satisfaction": {
                    "expected_effect": "synthetic work complete",
                    "basis": "execution-report",
                    "verifier": None,
                },
                "roles": [
                    {
                        "id": "navigator",
                        "context_role": f"participant-{i}",
                        "resource_scope": "task",
                        "requirements": {
                            "capabilities": [{"contract": operation, "constraints": []}],
                            "resources": [{"kind": "space", "units": 1}],
                        },
                        "execution_intent": {
                            "operation": operation,
                            "objective": "synthetic navigation",
                            "parameters": {"destination": f"fixture-target-{i}"},
                        },
                    }
                ],
            }
            for i in range(2)
        ],
    }


def free_address() -> str:
    """Choose an unused local port; a later bind race fails rather than stealing an owner's port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{listener.getsockname()[1]}"


def checkpoint(directory: Path) -> dict[str, Any]:
    """Read the actual durable Controller snapshot without changing any authority."""
    with sqlite3.connect(f"file:{directory / 'controller.sqlite3'}?mode=ro", uri=True) as db:
        raw = db.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
    value: dict[str, Any] = json.loads(raw)
    value["integration"] = json.loads(value["integration_json"])
    return value


def journal(directory: Path, node: str) -> list[dict[str, Any]]:
    """Read real Node durable workflow history, including cancellation and local handles."""
    path = directory / f"node-{node}-state/execution-journal.sqlite3"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT execution_id, local_handle, cancellation_requested, status, resource_ids "
                "FROM executions ORDER BY execution_id"
            )
        ]


def run_case(directory: Path, completed_peer: bool) -> dict[str, Any]:
    """Verify real delivery, stop gating, retained ownership and bounded replacement."""
    directory.mkdir()
    fixtures = [LocalFixture(directory / f"local-{node}.json") for node in ("a", "b")]
    threads = [threading.Thread(target=f.serve_forever, daemon=True) for f in fixtures]
    for thread in threads:
        thread.start()
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[Any] = []
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    grpc, http, artifact = (free_address() for _ in range(3))
    base = "http://" + http
    mission = "process-completed-peer" if completed_peer else "process-all-live"
    group = "group-" + mission

    def start(command: list[str], name: str) -> None:
        """Own each process group and keep its output in the fresh conformance directory."""
        log = (directory / f"{name}.log").open("wb")
        logs.append(log)
        processes.append(
            subprocess.Popen(
                command, stdout=log, stderr=log, env=env, cwd=ROOT, start_new_session=True
            )
        )

    def attempts() -> list[dict[str, Any]]:
        """Read actual Runtime history rather than using local fixture state as Controller truth."""
        value = request(base, "/v1/execution-attempts")["attempts"]
        return list(value)

    def snapshot(name: str) -> dict[str, Any]:
        """Persist cross-authority observations so every assertion can be independently reviewed."""
        value = {
            "attempts": attempts(),
            "recovery": request(base, f"/v1/groups/{group}/recovery"),
            "checkpoint": checkpoint(directory),
            "journals": [journal(directory, n) for n in ("a", "b")],
            "local": [f.records() for f in fixtures],
        }
        save(directory / f"{name}.json", value)
        return value

    try:
        configs = []
        for node, fixture, old_port in zip(("a", "b"), fixtures, (28100, 28102), strict=True):
            text = (ROOT / f"scenarios/e1-shared-world-episode-51/node-{node}.toml").read_text()
            text = (
                text.replace("127.0.0.1:25060", grpc)
                .replace(f"http://127.0.0.1:{old_port}", fixture.endpoint)
                .replace("NODE_STATE_PLACEHOLDER", str(directory / f"node-{node}-state"))
            )
            path = directory / f"node-{node}.toml"
            path.write_text(text)
            configs.append(path)
        prepare_deployment(configs, directory / "deployment.json", True)
        verify_deployment(directory / "deployment.json")
        start(
            [
                str(ROOT / "target/debug/integration-server"),
                grpc,
                str(directory / "controller.sqlite3"),
                http,
                artifact,
                str(directory / "artifacts"),
            ],
            "controller",
        )
        await_condition(lambda: request(base, "/healthz"), "Controller startup")
        for node, config in zip(("a", "b"), configs, strict=True):
            start([str(ROOT / "target/debug/roboguide-node"), str(config)], f"node-{node}")
        await_condition(
            lambda: len(request(base, "/v1/inventory")["nodes"]) == 2, "both real Nodes registered"
        )
        plan = generic_plan(mission)
        save(directory / "fixture-plan.json", plan)
        save(directory / "submission.json", request(base, "/v1/missions", plan))
        await_condition(
            lambda: len(attempts()) == 2 and all(a["status"] == "Running" for a in attempts()),
            "original work Running",
        )
        if completed_peer:
            fixtures[0].transition("COMPLETED")
            await_condition(
                lambda: any(a["status"] == "Completed" for a in attempts()),
                "completed peer observed by Runtime",
            )
        before = snapshot("before-recovery")
        command = {
            "schema_version": "roboguide.group-recovery-command/v0.1",
            "recovery_id": "process-check-recovery",
            "timeout_ms": 30000,
            "max_replacements": 1,
            "members": [
                {
                    "execution_id": a["execution_id"],
                    "expected_node_id": a["node_id"],
                    "repeat_authorized": a["status"] != "Completed",
                }
                for a in before["attempts"]
            ],
        }
        save(directory / "recovery-command.json", command)
        save(
            directory / "recovery-response.json",
            request(base, f"/v1/groups/{group}/recover", command),
        )
        pending = fixtures[1:] if completed_peer else fixtures
        await_condition(
            lambda: all(f.records()[0]["cancel_count"] == 1 for f in pending),
            "actual Node cancellation delivery",
        )
        ack = snapshot("ack-is-not-stop")
        assert len(ack["attempts"]) == 2
        assert ack["recovery"]["disposition"] == "AwaitingStop"
        assert (
            ack["checkpoint"]["integration"]["control"]["reservations"]
            == (before["checkpoint"]["integration"]["control"]["reservations"])
        ), "Cancel acknowledgement released Control resources"
        if not completed_peer:
            fixtures[0].transition("CANCELLED")
            await_condition(
                lambda: any(a["status"] == "Cancelled" for a in attempts()),
                "first real Node stop report",
            )
            partial = snapshot("partial-stop")
            assert len(partial["attempts"]) == 2
            assert partial["recovery"]["disposition"] == "AwaitingStop"
        fixtures[1].transition("CANCELLED")
        expected = 3 if completed_peer else 4
        await_condition(
            lambda: (
                len(attempts()) == expected
                and sum(a["status"] == "Running" for a in attempts()) == len(pending)
            ),
            "continued real work",
        )
        continued = snapshot("continued")
        assert continued["recovery"]["disposition"] == "Continued"
        for fixture in pending:
            original, replacement = fixture.records()
            left, right = dict(original["invocation"]), dict(replacement["invocation"])
            assert left.pop("attempt_id") != right.pop("attempt_id")
            assert left == right, "continuation changed intent, session or logical ownership"
        if completed_peer:
            assert len(fixtures[0].records()) == 1
            assert fixtures[0].records()[0]["state"] == "COMPLETED"
        for fixture in pending:
            fixture.transition("COMPLETED")
        await_condition(
            lambda: request(base, f"/v1/missions/{mission}").get("status") == "Completed",
            "Mission completion after real Node reports",
        )
        final = snapshot("final")
        assert not final["checkpoint"]["integration"]["control"]["reservations"]
        return {
            "case": mission,
            "passed": True,
            "original_attempts": 2,
            "replacement_attempts": expected - 2,
            "directory": str(directory),
        }
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for fixture, thread in zip(fixtures, threads, strict=True):
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=2)
        for log in logs:
            log.close()


def main() -> None:
    """Run two offline cases once, retaining failures without another Mission POST."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binaries = [ROOT / f"target/debug/{name}" for name in ("integration-server", "roboguide-node")]
    save(
        output / "manifest.json",
        {
            "scope": "synthetic actual-process conformance",
            "provider_calls": 0,
            "habitat_resets": 0,
            "code_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "tracked_source_changes": subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True
            ).splitlines(),
            "binaries": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in binaries
            },
        },
    )
    results = []
    try:
        for name, peer in (("all-live", False), ("completed-peer", True)):
            results.append(run_case(output / name, peer))
            save(output / "summary.json", {"status": "running", "cases": results})
    except Exception as failure:
        save(
            output / "summary.json",
            {"passed": False, "cases": results, "error": f"{type(failure).__name__}: {failure}"},
        )
        raise
    save(output / "summary.json", {"status": "complete", "passed": True, "cases": results})
    print(json.dumps({"passed": True, "cases": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
