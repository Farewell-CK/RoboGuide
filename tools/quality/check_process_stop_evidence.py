"""Check original process exit through real Controller and Node processes.

Uses an owned POSIX child and authored software fixture tasks, with zero devices,
models or simulators. Cancel acceptance deliberately leaves the child alive.
Only an actual wait() result can provide v0.3 exit evidence. Artifacts remain local.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from check_group_continuation_processes import (
    ROOT,
    FixtureHandler,
    LocalFixture,
    await_condition,
    checkpoint,
    free_address,
    generic_plan,
    request,
    save,
)


class ProcessFixture(LocalFixture):
    """Observe terminal outcomes separately from one original shared child process."""

    def __init__(self, path: Path, owner: ProcessOwner) -> None:
        """Share the original process witness across both actual Node endpoints."""
        super().__init__(path)
        self.RequestHandlerClass = ProcessHandler
        self.owner = owner


class ProcessOwner:
    """Keep an original OS process and immutable worker identity, without replacement."""

    def __init__(self) -> None:
        """Start a single model-free software worker that waits for its original stdin."""
        self.instance = str(uuid.uuid4())
        self.process = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.fixtures: list[ProcessFixture] = []

    def observe(self) -> dict[str, Any] | None:
        """Read an actual child exit code and the original complete invocation coverage."""
        exit_code = self.process.poll()
        if exit_code is None:
            return None
        records = [record for fixture in self.fixtures for record in fixture.records()]
        invocations = [record["invocation"] for record in records]
        groups = {invocation["group_id"] for invocation in invocations}
        if len(groups) != 1:
            raise ValueError("software fixture must cover exactly one original Group")
        return {
            "instance_id": self.instance,
            "pid": self.process.pid,
            "exit_code": exit_code,
            "group_id": next(iter(groups)),
            "attempts": [
                {
                    "execution_id": invocation["attempt_id"],
                    "execution_generation": invocation["execution_generation"],
                }
                for invocation in invocations
            ],
        }

    def close(self) -> None:
        """Reap only the owned worker; never fabricate exit while it remains alive."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=5)


class ProcessHandler(FixtureHandler):
    """Add the production safety export to the maintained Local EAIOS fixture."""

    def do_GET(self) -> None:  # noqa: N802
        """Publish empty safety evidence until the original child has actually exited."""
        server: Any = self.server
        if self.path != "/v1/executions/safe-state":
            super().do_GET()
            return
        proof = server.owner.observe()
        observations = []
        if proof is not None:
            for record in server.records():
                if record["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    continue
                invocation = record["invocation"]
                observations.append(
                    {
                        "execution_id": invocation["attempt_id"],
                        "execution_generation": invocation["execution_generation"],
                        "command_id": None,
                        "safe_state": True,
                        "assurance": "software-process",
                        "terminal_state": record["state"],
                        "process_exit": proof,
                    }
                )
        self.respond({"observations": observations})


def stop(process: subprocess.Popen[bytes]) -> None:
    """Terminate only an owned actual Controller or Node process group."""
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def run(directory: Path) -> dict[str, Any]:
    """Check alive, partial, full exit, Mission closure and durable restart in one session."""
    directory.mkdir(parents=True, exist_ok=False)
    owner = ProcessOwner()
    fixtures = [ProcessFixture(directory / f"local-{node}.json", owner) for node in ("a", "b")]
    owner.fixtures = fixtures
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
    mission = "software-process-stop"
    group = "group-" + mission

    def start(command: list[str], name: str) -> subprocess.Popen[bytes]:
        """Retain owned process logs and handles for exact cleanup."""
        log = (directory / f"{name}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            command, stdout=log, stderr=log, env=env, cwd=ROOT, start_new_session=True
        )
        processes.append(process)
        return process

    def attempts() -> list[dict[str, Any]]:
        """Observe authoritative Runtime attempts from the production HTTP read API."""
        return list(request(base, "/v1/execution-attempts")["attempts"])

    def snapshot(name: str) -> dict[str, Any]:
        """Retain independent OS and Controller evidence for every gate."""
        value = {
            "attempts": attempts(),
            "mission": request(base, f"/v1/missions/{mission}"),
            "checkpoint": checkpoint(directory),
            "original_process_exit": owner.observe(),
            "local": [fixture.records() for fixture in fixtures],
        }
        save(directory / f"{name}.json", value)
        return value

    command = [
        str(ROOT / "target/debug/integration-server"),
        grpc,
        str(directory / "controller.sqlite3"),
        http,
        artifact,
        str(directory / "artifacts"),
    ]
    try:
        controller = start(command, "controller")
        await_condition(lambda: request(base, "/healthz"), "Controller startup")
        for node, fixture, old_port in zip(("a", "b"), fixtures, (28100, 28102), strict=True):
            text = (ROOT / f"scenarios/e1-shared-world-episode-51/node-{node}.toml").read_text()
            text = (
                text.replace("127.0.0.1:25060", grpc)
                .replace(f"http://127.0.0.1:{old_port}", fixture.endpoint)
                .replace("NODE_STATE_PLACEHOLDER", str(directory / f"node-{node}-state"))
                .replace(
                    'schema = "roboguide.node-config/v0.7"', 'schema = "roboguide.node-config/v0.8"'
                )
            )
            path = directory / f"node-{node}.toml"
            path.write_text(text)
            start([str(ROOT / "target/debug/roboguide-node"), str(path)], f"node-{node}")
        await_condition(
            lambda: len(request(base, "/v1/inventory")["nodes"]) == 2,
            "actual v0.8 Node registration",
        )
        plan = generic_plan(mission)
        save(directory / "fixture-plan.json", plan)
        save(directory / "submission.json", request(base, "/v1/missions", plan))
        await_condition(
            lambda: len(attempts()) == 2 and all(a["status"] == "Running" for a in attempts()),
            "two original software attempts Running",
        )
        original = snapshot("before-stop")
        execution = original["attempts"][0]["execution_id"]
        save(directory / "cancel.json", request(base, f"/v1/executions/{execution}/cancel", {}))
        await_condition(
            lambda: all(fixture.records()[0]["cancel_count"] == 1 for fixture in fixtures),
            "actual durable cancellation delivery",
        )
        alive = snapshot("cancel-accepted-worker-alive")
        assert alive["original_process_exit"] is None
        assert alive["checkpoint"]["integration"]["control"]["reservations"]
        assert all(
            purpose["record"]["phase"] != "stop_confirmed"
            for purpose in alive["checkpoint"]["safety_stops"].values()
        )
        fixtures[0].transition("COMPLETED")
        await_condition(
            lambda: any(a["status"] == "Completed" for a in attempts()), "completed peer"
        )
        partial = snapshot("partial-terminal-worker-alive")
        assert partial["original_process_exit"] is None
        assert partial["checkpoint"]["integration"]["control"]["reservations"]
        owner.close()
        fixtures[1].transition("FAILED")
        await_condition(
            lambda: request(base, f"/v1/missions/{mission}")["status"] == "Failed",
            "original failure closes only after actual original worker exit",
        )
        final = snapshot("confirmed-failed")
        assert sorted(a["status"] for a in final["attempts"]) == ["Completed", "Failed"]
        assert not final["checkpoint"]["integration"]["control"]["reservations"]
        assert len(final["checkpoint"]["safety_stops"]) == 2
        assert all(
            purpose["record"]["phase"] == "stop_confirmed"
            for purpose in final["checkpoint"]["safety_stops"].values()
        )
        stop(controller)
        time.sleep(0.2)
        start(command, "controller-restarted")
        await_condition(lambda: request(base, "/healthz"), "Controller durable restart")
        restored = snapshot("restored")
        assert restored["checkpoint"]["safety_stops"] == final["checkpoint"]["safety_stops"]
        assert restored["mission"]["status"] == "Failed"
        assert len(restored["attempts"]) == 2
        return {
            "passed": True,
            "actual_nodes": 2,
            "actual_original_workers": 1,
            "original_pid": owner.process.pid,
            "exit_code": owner.process.returncode,
            "original_terminal_statuses": ["Completed", "Failed"],
            "controller_restart": True,
            "provider_calls": 0,
            "habitat_resets": 0,
            "hardware_stop_claim": False,
            "group_id": group,
        }
    finally:
        for process in reversed(processes):
            stop(process)
        owner.close()
        for fixture, thread in zip(fixtures, threads, strict=True):
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=2)
        for log in logs:
            log.close()


def main() -> None:
    """Execute once with explicit output ownership, retaining failure evidence without retry."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    try:
        result = run(directory)
    except Exception as failure:
        if directory.is_dir():
            save(directory / "RESULT.json", {"passed": False, "error": str(failure)})
        raise
    save(directory / "RESULT.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
