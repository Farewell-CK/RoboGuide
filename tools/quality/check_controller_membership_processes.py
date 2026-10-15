"""Verify pre-enrolled learner admission, joint membership and leader transfer processes.

This check starts only RoboGuide replica/supervisor processes and their ordinary
Controller child. It creates no Node, device, model, simulator or experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]


def free_address() -> str:
    """Return one unused loopback address for a private process route."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{listener.getsockname()[1]}"


def save(path: Path, value: Any) -> None:
    """Retain bounded process evidence in the isolated output directory."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def wait_for(condition: Any, description: str, seconds: float = 30) -> Any:
    """Poll an observation without retrying any mutating request."""
    deadline = time.monotonic() + seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = condition()
            if value is not None and value is not False:
                return value
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last = error
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {description}: {last}")


def run(output: Path, skip_build: bool) -> dict[str, Any]:
    """Run four actual supervisor processes through learner, voter and handoff gates."""
    output.mkdir(parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "controller-replica", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
        )
    binary = ROOT / "target/debug/integration-server"
    replica_binary = ROOT / "target/debug/controller-replica"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    members = {str(index): free_address() for index in range(1, 5)}
    routes = [[free_address() for _ in range(3)] for _ in range(4)]
    initial_voters = [1, 2, 3]
    configurations: list[Path] = []
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[Any] = []
    result: dict[str, Any] = {
        "passed": False,
        "schema": "roboguide.replica-membership-process-check/v0.1",
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "software_replicas": 4,
    }

    def status(member: int) -> dict[str, Any]:
        """Read one supervisor's atomically published local status."""
        return cast(
            dict[str, Any], json.loads((output / f"replica-{member}/status.json").read_text())
        )

    def start(command: list[str], name: str) -> None:
        """Start only a process owned by this isolated check."""
        log = (output / f"{name}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        processes.append(process)

    def leader() -> int | None:
        """Return a member whose supervisor currently owns a Controller child and epoch."""
        for member in range(1, 5):
            try:
                state = status(member)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if state.get("epoch") is not None and state.get("child_pid") is not None:
                return member
        return None

    def admin(config: Path, leader_id: int, *arguments: str) -> Any:
        """Issue one authenticated member-side admin command and parse its immutable result."""
        completed = subprocess.run(
            [str(replica_binary), "--admin", str(config), "--leader", str(leader_id), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return json.loads(completed.stdout)

    try:
        application_schema = subprocess.check_output(
            [str(replica_binary), "--application-schema"], cwd=ROOT, env=environment, text=True
        ).strip()
        result["application_schema"] = application_schema
        result["controller_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        for member in range(1, 5):
            config: dict[str, Any] = {
                "schema": "roboguide.controller-replica-process/v0.1",
                "replica": {
                    "schema": "roboguide.controller-replica/v0.2",
                    "cluster_id": "dynamic-membership-process-check",
                    "replica_id": member,
                    "members": members,
                    "initial_voters": initial_voters,
                    "application_schema": application_schema,
                    "application_policy_digest": "pending",
                },
                "storage_directory": str(output / f"replica-{member}"),
                "controller_binary": str(binary),
                "grpc_address": routes[member - 1][0],
                "http_address": routes[member - 1][1],
                "artifact_address": routes[member - 1][2],
                "controller_environment": {"ROBOGUIDE_CHECKPOINT_STORAGE": "full"},
            }
            path = output / f"replica-{member}.json"
            save(path, config)
            digest = subprocess.check_output(
                [str(replica_binary), "--policy-digest", str(path)],
                cwd=ROOT,
                env=environment,
                text=True,
            ).strip()
            config["replica"]["application_policy_digest"] = digest
            save(path, config)
            configurations.append(path)

        for member in (1, 2, 3, 4):
            start([str(replica_binary), str(configurations[member - 1])], f"replica-{member}")
        first_leader = wait_for(leader, "initial three-voter leader")
        save(
            output / "initial-leader.json", {"member": first_leader, "status": status(first_leader)}
        )
        assert first_leader in initial_voters
        result["initial_voter_leader"] = first_leader

        added = admin(configurations[0], first_leader, "--add-learner", "4")
        result["learner_admission"] = added
        promoted = admin(configurations[0], first_leader, "--change-voters", "1,2,3,4", "--retain")
        result["joint_consensus_add"] = promoted
        wait_for(
            lambda: 4 in admin(configurations[0], first_leader, "--membership"),
            "learner promotion to voter",
        )
        result["voters_after_add"] = admin(configurations[0], first_leader, "--membership")

        old_term = status(first_leader)["epoch"]["term"]
        transfer = admin(
            configurations[0],
            first_leader,
            "--transfer-leader",
            "4",
            "--transfer-id",
            "dynamic-process-transfer-1",
        )
        result["leader_transfer"] = transfer
        new_leader = wait_for(
            lambda: (
                4
                if (state := status(4)).get("epoch")
                and state["epoch"]["replica_id"] == 4
                and state["epoch"]["term"] > old_term
                else None
            ),
            "target leader after controlled transfer",
        )
        result["leader_after_transfer"] = new_leader
        removed = admin(
            configurations[0],
            new_leader,
            "--change-voters",
            "2,3,4",
            "--retain",
        )
        result["joint_consensus_remove"] = removed
        wait_for(
            lambda: admin(configurations[0], new_leader, "--membership") == [2, 3, 4],
            "removed voter convergence",
        )
        result["voters_after_remove"] = admin(configurations[0], new_leader, "--membership")
        result["passed"] = True
        save(output / "result.json", result)
        return result
    except Exception as failure:
        result["failure"] = f"{type(failure).__name__}: {failure}"
        save(output / "result.json", result)
        raise
    finally:
        for process in reversed(processes):
            try:
                os.killpg(process.pid, signal.SIGINT)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if process.poll() is None:
                    process.wait(timeout=5)
        for log in logs:
            log.close()


def main() -> None:
    """Run the isolated dynamic membership process check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.output.resolve(), arguments.skip_build), indent=2))


if __name__ == "__main__":
    main()
