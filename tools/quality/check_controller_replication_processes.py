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
import urllib.request
from contextlib import closing
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


def compiled_application_schema(binary: Path, environment: dict[str, str]) -> str:
    """Read the compiled authority schema without opening storage or starting a service."""
    completed = subprocess.run(
        [str(binary), "--application-schema"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        cwd=ROOT,
        env=environment,
    )
    schema = completed.stdout.strip()
    if not schema.startswith("roboguide.controller-checkpoint/v") or "\n" in schema:
        raise ValueError(f"invalid compiled application schema from {binary.name}")
    return schema


def run(
    directory: Path,
    tls_enabled: bool = False,
    member_host: str = "127.0.0.1",
) -> dict[str, Any]:
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

    def member_address() -> str:
        """Choose one unused port on the selected replica member interface."""
        with socket.socket() as listener:
            listener.bind((member_host, 0))
            return f"{member_host}:{listener.getsockname()[1]}"

    members = {str(index): member_address() for index in range(1, 4)}
    routes = [[free_address() for _ in range(3)] for _ in range(3)]
    metric_routes = [free_address() for _ in range(3)]
    configurations: list[Path] = []
    replicas: list[subprocess.Popen[bytes] | None] = [None, None, None]
    result: dict[str, Any] = {
        "passed": False,
        "schema": "roboguide.replica-process-check/v0.2",
        "tls_enabled": tls_enabled,
        "member_host": member_host,
    }

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

    def metrics(index: int) -> str:
        """Read this actual supervisor's private observation endpoint, never a failover command."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://" + metric_routes[index] + "/metrics", timeout=3) as response:  # noqa: S310
            raw = response.read(64 * 1024 + 1)
        if not isinstance(raw, bytes):
            raise ValueError("replica metrics response is not bytes")
        assert len(raw) <= 64 * 1024
        return raw.decode()

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
        application_schema = compiled_application_schema(binary, environment)
        if application_schema != compiled_application_schema(
            ROOT / "target/debug/controller-replica", environment
        ):
            raise ValueError("Controller and replica binaries use different authority schemas")
        result["application_schema"] = application_schema
        result["controller_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        tls_profiles: list[dict[str, Any]] | None = None
        if tls_enabled:
            ca_key = directory / "replica-ca.key"
            ca_cert = directory / "replica-ca.crt"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(ca_key),
                    "-out",
                    str(ca_cert),
                    "-subj",
                    "/CN=roboguide-replica-ca",
                    "-days",
                    "2",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                    "-addext",
                    "keyUsage=critical,keyCertSign,cRLSign",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            peer_digests: dict[str, str] = {}
            leaf_paths: list[tuple[Path, Path]] = []
            for member in range(1, 4):
                leaf_key = directory / f"replica-{member}.key"
                leaf_csr = directory / f"replica-{member}.csr"
                leaf_cert = directory / f"replica-{member}.crt"
                extensions = directory / f"replica-{member}.ext"
                subprocess.run(
                    [
                        "openssl",
                        "req",
                        "-newkey",
                        "rsa:2048",
                        "-nodes",
                        "-keyout",
                        str(leaf_key),
                        "-out",
                        str(leaf_csr),
                        "-subj",
                        f"/CN=roboguide-replica-{member}",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                extensions.write_text(
                    "basicConstraints=CA:FALSE\n"
                    "subjectAltName=DNS:localhost\n"
                    "extendedKeyUsage=serverAuth,clientAuth\n"
                )
                serial_args = (
                    ["-CAcreateserial"]
                    if member == 1
                    else ["-CAserial", str(directory / "replica-ca.srl")]
                )
                subprocess.run(
                    [
                        "openssl",
                        "x509",
                        "-req",
                        "-in",
                        str(leaf_csr),
                        "-CA",
                        str(ca_cert),
                        "-CAkey",
                        str(ca_key),
                        *serial_args,
                        "-out",
                        str(leaf_cert),
                        "-days",
                        "2",
                        "-sha256",
                        "-extfile",
                        str(extensions),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                der = subprocess.check_output(
                    ["openssl", "x509", "-in", str(leaf_cert), "-outform", "DER"]
                )
                peer_digests[str(member)] = f"sha256:{hashlib.sha256(der).hexdigest()}"
                leaf_paths.append((leaf_key, leaf_cert))
            tls_profiles = [
                {
                    "ca_cert_path": str(ca_cert),
                    "cert_path": str(cert),
                    "key_path": str(key),
                    "server_name": "localhost",
                    "peer_certificate_digests": peer_digests,
                }
                for key, cert in leaf_paths
            ]
        for index in range(3):
            config: dict[str, Any] = {
                "schema": "roboguide.controller-replica-process/v0.1",
                "replica": {
                    "schema": "roboguide.controller-replica/v0.1",
                    "cluster_id": "synthetic-process-check",
                    "replica_id": index + 1,
                    "members": members,
                    "application_schema": application_schema,
                    "application_policy_digest": "compute-before-start",
                },
                "storage_directory": str(directory / f"replica-{index + 1}"),
                "controller_binary": str(binary),
                "grpc_address": routes[index][0],
                "http_address": routes[index][1],
                "artifact_address": routes[index][2],
                "metrics_address": metric_routes[index],
                "controller_environment": {"ROBOGUIDE_CHECKPOINT_STORAGE": "full"},
            }
            if tls_profiles is not None:
                config["replica"]["tls"] = tls_profiles[index]
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
        initial_metrics = metrics(0)
        assert "roboguide_replica_owned_controller 0\n" in initial_metrics
        assert "roboguide_replica_quorum_epoch_present 0\n" in initial_metrics
        (directory / "minority-metrics.prom").write_text(initial_metrics)
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
        with (
            closing(
                sqlite3.connect(
                    f"file:{directory / f'replica-{new_index + 1}/replica.sqlite3'}?mode=ro",
                    uri=True,
                )
            ) as database,
            database,
        ):
            retained = database.execute(
                "SELECT sequence,data FROM replica_events ORDER BY sequence"
            ).fetchall()

        def prefix_converged() -> bool:
            """Compare exact rows, including original source/time and command evidence."""
            for index in range(3):
                with (
                    closing(
                        sqlite3.connect(
                            f"file:{directory / f'replica-{index + 1}/replica.sqlite3'}?mode=ro",
                            uri=True,
                        )
                    ) as database,
                    database,
                ):
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
            closing(
                sqlite3.connect(
                    f"file:{directory / f'replica-{new_index + 1}/controller.sqlite3'}?mode=ro",
                    uri=True,
                )
            ) as source,
            source,
            closing(sqlite3.connect(copied)) as target,
            target,
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
            await_condition(
                lambda: "roboguide_replica_quorum_epoch_present 0\n" in metrics(new_index),
                "actual supervisor metrics observe quorum loss",
            )
            (directory / "quorum-loss-metrics.prom").write_text(metrics(new_index))
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
        for index in range(3):
            (directory / f"restored-replica-{index + 1}.prom").write_text(metrics(index))
        result["actual_supervisor_metrics"] = True
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
    parser.add_argument(
        "--tls",
        action="store_true",
        help="run the same software fault matrix over mutual-TLS replica RPC",
    )
    parser.add_argument(
        "--member-host",
        default="127.0.0.1",
        help="interface/address used by replica RPC (non-loopback requires --tls)",
    )
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
    print(
        json.dumps(
            run(arguments.output.resolve(), arguments.tls, arguments.member_host),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
