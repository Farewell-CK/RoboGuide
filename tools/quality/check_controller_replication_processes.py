"""Verify isolated real replica/Controller/Node processes with synthetic local work.

No Provider, simulator, hardware, production service or E6 is started. A fresh
directory retains all configurations, child logs, accepted history and journals.
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
import threading
import time
from pathlib import Path
from typing import Any

from check_group_continuation_processes import (
    ROOT,
    LocalFixture,
    await_condition,
    checkpoint,
    free_address,
    generic_plan,
    journal,
    request,
    save,
)


def run(directory: Path) -> dict[str, Any]:
    """Verify quorum-only startup, leader death, conservative restore and journal continuity."""
    directory.mkdir(parents=True, exist_ok=False)
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[Any] = []
    fixtures = [LocalFixture(directory / f"local-{node}.json") for node in ("a", "b")]
    threads = [threading.Thread(target=fixture.serve_forever, daemon=True) for fixture in fixtures]
    for thread in threads:
        thread.start()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    members = {str(index): free_address() for index in range(1, 4)}
    routes = [[free_address() for _ in range(3)] for _ in range(3)]
    configurations: list[Path] = []
    replicas: list[subprocess.Popen[bytes] | None] = [None, None, None]
    result: dict[str, Any] = {"passed": False, "schema": "roboguide.replica-process-check/v0.1"}

    def start(command: list[str], name: str) -> subprocess.Popen[bytes]:
        """Own a new process group without finding or touching existing service PIDs."""
        log = (directory / f"{name}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=log, stderr=log, env=environment, start_new_session=True
        )
        processes.append(process)
        return process

    def status(index: int) -> dict[str, Any]:
        """Read bounded local supervisor status, used only as a test observation."""
        value: dict[str, Any] = json.loads(
            (directory / f"replica-{index + 1}/status.json").read_text()
        )
        return value

    def live_leader(exclude: int | None = None) -> tuple[int, str] | None:
        """Confirm an observed owned child through its actual HTTP route."""
        for index in range(3):
            process = replicas[index]
            if index == exclude or process is None or process.poll() is not None:
                continue
            try:
                state = status(index)
                if state.get("epoch") is not None and state.get("child_pid") is not None:
                    base = "http://" + routes[index][1]
                    if request(base, "/healthz"):
                        return index, base
            except (OSError, ValueError):
                continue
        return None

    def evidence(index: int, base: str) -> dict[str, Any]:
        """Retain production Runtime/Control/orchestration and Node/local evidence independently."""
        return {
            "attempts": request(base, "/v1/execution-attempts")["attempts"],
            "checkpoint": checkpoint(directory / f"replica-{index + 1}"),
            "journals": [journal(directory, node) for node in ("a", "b")],
            "local": [fixture.records() for fixture in fixtures],
            "supervisors": [
                status(member)
                for member, process in enumerate(replicas)
                if process is not None and process.poll() is None
            ],
        }

    try:
        binary = ROOT / "target/debug/integration-server"
        result["controller_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        for index in range(3):
            config: dict[str, Any] = {
                "schema": "roboguide.controller-replica-process/v0.1",
                "replica": {
                    "schema": "roboguide.controller-replica/v0.1",
                    "cluster_id": "synthetic-process-check",
                    "replica_id": index + 1,
                    "members": members,
                    "application_schema": "roboguide.controller-checkpoint/v25",
                    "application_policy_digest": "compute-before-start",
                },
                "storage_directory": str(directory / f"replica-{index + 1}"),
                "controller_binary": str(binary),
                "grpc_address": routes[index][0],
                "http_address": routes[index][1],
                "artifact_address": routes[index][2],
                "controller_environment": {"ROBOGUIDE_CHECKPOINT_STORAGE": "full"},
            }
            path = directory / f"replica-{index + 1}.json"
            save(path, config)
            digest = (
                subprocess.check_output(
                    [str(ROOT / "target/debug/controller-replica"), "--policy-digest", str(path)],
                    cwd=ROOT,
                    env=environment,
                )
                .decode()
                .strip()
            )
            config["replica"]["application_policy_digest"] = digest
            save(path, config)
            configurations.append(path)
        replicas[0] = start(
            [str(ROOT / "target/debug/controller-replica"), str(configurations[0])], "replica-1"
        )
        await_condition(lambda: status(0), "minority supervisor starts")
        time.sleep(0.3)
        assert status(0)["child_pid"] is None, "minority started an executing Controller"
        result["minority_startup_fenced"] = True
        for index in (1, 2):
            replicas[index] = start(
                [str(ROOT / "target/debug/controller-replica"), str(configurations[index])],
                f"replica-{index + 1}",
            )
        old_index, base = await_condition(live_leader, "majority Controller startup")
        for node, fixture, old_port in zip(("a", "b"), fixtures, (28100, 28102), strict=True):
            text = (ROOT / f"scenarios/e1-shared-world-episode-51/node-{node}.toml").read_text()
            endpoints = ["http://" + route[0] for route in routes]
            text = (
                text.replace(
                    'server_endpoint = "http://127.0.0.1:25060"',
                    f"server_endpoint = {json.dumps(endpoints[0])}\n"
                    f"fallback_server_endpoints = {json.dumps(endpoints[1:])}",
                )
                .replace(f"http://127.0.0.1:{old_port}", fixture.endpoint)
                .replace("NODE_STATE_PLACEHOLDER", str(directory / f"node-{node}-state"))
            )
            config_path = directory / f"node-{node}.toml"
            config_path.write_text(text)
            start([str(ROOT / "target/debug/roboguide-node"), str(config_path)], f"node-{node}")
        await_condition(
            lambda: len(request(base, "/v1/inventory")["nodes"]) == 2, "two real Nodes register"
        )
        plan = generic_plan("replica-process-work")
        save(directory / "fixture-plan.json", plan)
        save(directory / "submission.json", request(base, "/v1/missions", plan))
        await_condition(
            lambda: (
                len(request(base, "/v1/execution-attempts")["attempts"]) == 2
                and all(
                    attempt["status"] == "Running"
                    for attempt in request(base, "/v1/execution-attempts")["attempts"]
                )
            ),
            "original actual Node work Running",
        )
        save(
            directory / "priority.json",
            request(base, "/v1/missions/replica-process-work/scheduling-policy", {"priority": 17}),
        )
        before = evidence(old_index, base)
        save(directory / "before-leader-death.json", before)
        process = replicas[old_index]
        assert process is not None
        # Kill only the owned parent; its child must fence itself when that parent disappears.
        process.kill()
        process.wait(timeout=5)
        new_index, new_base = await_condition(
            lambda: live_leader(old_index), "new majority Controller after leader death"
        )

        def old_routes_closed() -> bool:
            """Require the orphaned original child to close all three owned public listeners."""
            for address in routes[old_index]:
                host, port = address.rsplit(":", 1)
                try:
                    with socket.create_connection((host, int(port)), timeout=0.2):
                        return False
                except OSError:
                    pass
            return True

        await_condition(old_routes_closed, "original orphan Controller fences its routes")
        result["original_epoch_routes_closed"] = True
        await_condition(
            lambda: len(request(new_base, "/v1/inventory")["nodes"]) == 2, "same Nodes reconnect"
        )
        after = evidence(new_index, new_base)
        save(directory / "after-leader-death.json", after)
        assert len(after["attempts"]) == 2, "restoration prepared replacement attempts"
        assert all(attempt["status"] == "Unknown" for attempt in after["attempts"])
        assert (
            after["checkpoint"]["integration"]["control"]["reservations"]
            == (before["checkpoint"]["integration"]["control"]["reservations"])
        ), "leader switch discarded physical ownership"
        old_mission = json.loads(before["checkpoint"]["orchestration_json"])[0]
        new_mission = json.loads(after["checkpoint"]["orchestration_json"])[0]
        assert new_mission["dispatch_policy"] == old_mission["dispatch_policy"]
        assert after["journals"] == before["journals"], "same durable Node handles changed"
        assert after["local"] == before["local"], "Local EAIOS Execute was repeated on promotion"
        result["leader_death_retains_unknown_ownership_policy_and_journals"] = True
        result["original_local_execute_count"] = sum(len(records) for records in after["local"])
        replicas[old_index] = start(
            [str(ROOT / "target/debug/controller-replica"), str(configurations[old_index])],
            f"replica-{old_index + 1}-rejoin",
        )

        # Compare original complete evidence prefixes rather than a racing current heartbeat tail.
        await_condition(lambda: status(old_index), "rejoined replica status")
        with sqlite3.connect(
            f"file:{directory / f'replica-{new_index + 1}/replica.sqlite3'}?mode=ro", uri=True
        ) as database:
            retained = database.execute(
                "SELECT sequence,data FROM replica_events ORDER BY sequence"
            ).fetchall()

        def prefix_converged() -> bool:
            """Compare exact rows, including original source/time and command evidence."""
            for index in range(3):
                with sqlite3.connect(
                    f"file:{directory / f'replica-{index + 1}/replica.sqlite3'}?mode=ro", uri=True
                ) as database:
                    found = database.execute(
                        "SELECT sequence,data FROM replica_events WHERE sequence<=? "
                        "ORDER BY sequence",
                        (retained[-1][0],),
                    ).fetchall()
                if found != retained:
                    return False
            return True

        await_condition(prefix_converged, "exact accepted history prefix rejoins")
        result["rejoined_metadata_prefix_rows"] = len(retained)
        copied = directory / "standalone-copy.sqlite3"
        with (
            sqlite3.connect(
                f"file:{directory / f'replica-{new_index + 1}/controller.sqlite3'}?mode=ro",
                uri=True,
            ) as source,
            sqlite3.connect(copied) as target,
        ):
            source.backup(target)
        denied = subprocess.run(
            [
                str(binary),
                free_address(),
                str(copied),
                free_address(),
                free_address(),
                str(directory / "standalone-artifacts"),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert denied.returncode != 0 and b"replication profile differs" in denied.stderr
        result["standalone_downgrade_rejected"] = True
        paused = [index for index in range(3) if index != new_index]
        try:
            for index in paused:
                follower = replicas[index]
                assert follower is not None
                follower.send_signal(signal.SIGSTOP)

            def majority_loss_fenced() -> bool:
                """Require the actual leader HTTP route to close after majority loss."""
                try:
                    request(new_base, "/healthz")
                    return False
                except OSError:
                    return True

            await_condition(majority_loss_fenced, "leader loses actual majority")
            assert [fixture.records() for fixture in fixtures] == after["local"]
            result["majority_loss_fences_without_repeating_or_stopping_local_work"] = True
        finally:
            for index in paused:
                follower = replicas[index]
                if follower is not None and follower.poll() is None:
                    follower.send_signal(signal.SIGCONT)

        def restored_evidence() -> dict[str, Any] | None:
            """Resolve the current leader for each GET and require fresh Node sessions."""
            selected = live_leader()
            if selected is None:
                return None
            index, endpoint = selected
            inventory = request(endpoint, "/v1/inventory")["nodes"]
            if len(inventory) != 2 or any(node["liveness"] != "Reachable" for node in inventory):
                return None
            return evidence(index, endpoint)

        restored = await_condition(restored_evidence, "majority restores with fresh Node sessions")
        save(directory / "after-majority-restore.json", restored)
        assert [fixture.records() for fixture in fixtures] == after["local"]
        result["passed"] = True
        return result
    except Exception as failure:
        result["failure"] = f"{type(failure).__name__}: {failure}"
        raise
    finally:
        save(directory / "result.json", result)
        for process in reversed(processes):
            try:
                os.killpg(process.pid, signal.SIGINT)
                if process.poll() is None:
                    process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        for fixture, thread in zip(fixtures, threads, strict=True):
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=2)
        for log in logs:
            log.close()


def main() -> None:
    """Build only the maintained software binaries, then retain one isolated conformance run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    arguments = parser.parse_args()
    if not arguments.skip_build:
        subprocess.run(
            [
                "cargo",
                "build",
                "--locked",
                "-p",
                "controller-replica",
                "-p",
                "integration-server",
                "-p",
                "roboguide-node",
            ],
            cwd=ROOT,
            check=True,
        )
    print(json.dumps(run(arguments.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
