"""Roll genuine old/new supervisors and applications over original mTLS/SQL with zero Nodes."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any, BinaryIO

from check_replica_certificate_processes import create_ca, issue
from check_replica_content_processes import Processes, digest, save
from check_runnable_content_processes import http
from check_state_verifier_processes import ROOT, address, wait_for


def metadata(path: Path) -> dict[str, Any]:
    """Read one original committed WAL snapshot without going through an HTTP success label."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        sql.execute("BEGIN")
        values = dict(sql.execute("SELECT key,value FROM replica_v1_meta"))
        return {"version": sql.execute("PRAGMA user_version").fetchone()[0]} | {
            key: json.loads(value) for key, value in values.items()
        }


def run(
    output: Path,
    supervisor: Path,
    old_supervisor: Path,
    controller: Path,
    old_controller: Path,
    storage: Path,
) -> dict[str, Any]:
    """Require genuine old processes, original child waits and retained new writers."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    binary_root = output / "binaries"
    binary_root.mkdir(mode=0o700)
    frozen: dict[str, Path] = {}
    identities: dict[str, str] = {}
    source_identities: dict[str, str] = {}
    for name, binary_source in {
        "supervisor": supervisor,
        "old-supervisor": old_supervisor,
        "controller": controller,
        "old-controller": old_controller,
        "storage": storage,
    }.items():
        checksum = digest(binary_source)
        destination = binary_root / (checksum + "-" + name)
        shutil.copy2(binary_source, destination)
        assert digest(destination) == checksum
        frozen[name], identities[name] = destination, checksum
        source_identities[str(binary_source)] = source_identities[str(destination)] = checksum
    save(output / "binary-identity.json", identities)
    save(output / "source-identity.json", source_identities)
    supervisor, old_supervisor, controller, old_controller, storage = (
        frozen[name]
        for name in ["supervisor", "old-supervisor", "controller", "old-controller", "storage"]
    )
    old_schema = subprocess.check_output(
        [str(old_controller), "--application-schema"], text=True, timeout=5
    ).strip()
    target_schema = subprocess.check_output(
        [str(controller), "--application-schema"], text=True, timeout=5
    ).strip()
    assert old_schema != target_schema
    configs: dict[int, dict[str, Any]] = {}
    old_configs: dict[int, Path] = {}
    current_paths: dict[int, Path] = {}
    owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
    running: dict[int, subprocess.Popen[bytes]] = {}
    started: dict[int, float] = {}
    logs: list[BinaryIO] = []
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "original_application_schema": old_schema,
        "target_application_schema": target_schema,
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }

    def start(member: int, binary: Path, configuration: Path) -> None:
        """Track every real process incarnation and leave its original SQL/content inode intact."""
        log = (output / f"supervisor-{member}-{len(owned)}.log").open("xb")
        logs.append(log)
        started[member] = time.time() * 1000
        process = subprocess.Popen(
            [str(binary), str(configuration)],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        record = {
            "member": member,
            "pid": process.pid,
            "binary_sha256": digest(binary),
            "configuration": str(configuration),
            "returncode": None,
        }
        owned.append((process, record))
        running[member] = process

    def stop(member: int) -> None:
        """Ask the owned supervisor to reap its original child and return its real wait."""
        process = running.pop(member)
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        status = process.wait(timeout=20)
        for child, record in owned:
            if child is process:
                record["returncode"] = status
        assert status == 0

    def status(member: int) -> dict[str, Any]:
        """Read only a publication from this member's actual current live supervisor."""
        if member not in running or running[member].poll() is not None:
            return {}
        path = Path(configs[member]["storage_directory"]) / "status.json"
        if not path.exists():
            return {}
        value = json.loads(path.read_bytes())
        if (
            value["observed_unix_ms"] < started[member]
            or time.time() * 1000 - value["observed_unix_ms"] > 3000
        ):
            return {}
        return dict(value)

    def serving(required: int | None = None) -> int | None:
        """Observe current leadership and actual Controller health."""
        for member in configs:
            if required is not None and member != required:
                continue
            value = status(member)
            if value.get("epoch") is not None and value.get("child_pid") is not None:
                try:
                    if (
                        http(configs[member]["http_address"], "/healthz", timeout_seconds=1)[0]
                        == 200
                    ):
                        return member
                except OSError:
                    pass
        return None

    def admin(member: int, *arguments: str) -> dict[str, Any]:
        """Execute one actual authenticated command; response loss never repeats a mutation."""
        command = [
            str(supervisor),
            "--admin",
            str(current_paths[member]),
            "--leader",
            str(member),
            *arguments,
        ]
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, timeout=135
        )
        save(
            output / f"admin-{len(list(output.glob('admin-*.json')))}.json",
            {
                "arguments": arguments,
                "returncode": completed.returncode,
                "stdout": completed.stdout.decode(),
                "stderr": completed.stderr.decode(),
            },
        )
        if completed.returncode:
            raise ValueError(completed.stderr.decode(errors="replace"))
        value: dict[str, Any] = json.loads(completed.stdout)
        return value

    with ExitStack() as scope:
        try:
            ipc = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="rg-upg-")))
            create_ca(output / "tls")
            members = [503, 509, 521]
            leaves = {member: issue(output / "tls", f"member-{member}") for member in members}
            endpoints = {str(member): address() for member in members}
            owners: dict[str, str] = {}
            calls = Processes(storage, output)
            for member in members:
                directory = output / f"member-{member}"
                directory.mkdir(mode=0o700)
                certificate = str(leaves[member][0])
                config: dict[str, Any] = {
                    "schema": "roboguide.controller-replica-process/v0.6",
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": "actual-original-application-upgrade",
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members,
                        "application_schema": old_schema,
                        "application_policy_digest": "pending",
                        "tls": {
                            "ca_cert_path": str(output / "tls/ca.crt"),
                            "cert_path": certificate,
                            "key_path": certificate,
                            "server_name": "localhost",
                            "peer_certificate_digests": {
                                str(key): value[1] for key, value in leaves.items()
                            },
                        },
                    },
                    "storage_directory": str(directory),
                    "controller_binary": str(old_controller),
                    "grpc_address": address(),
                    "http_address": address(),
                    "artifact_address": address(),
                    "controller_environment": {
                        "TOKIO_WORKER_THREADS": "1",
                        "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "1",
                        "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
                        "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN": "1",
                    },
                    "content": {
                        "schema": "roboguide.controller-content-process/v0.1",
                        "root": str(directory / "content"),
                        "bootstrap_owners": {},
                        "socket": str(ipc / f"{member}.sock"),
                        "child_budget_ms": 45000,
                        "quorum_observation_ms": 15000,
                    },
                }
                path = output / f"old-configuration-{member}.json"
                save(path, config)
                policy = subprocess.check_output(
                    [str(old_supervisor), "--policy-digest", str(path)], text=True, timeout=10
                ).strip()
                config["replica"]["application_policy_digest"] = policy
                save(path, config)
                profile = subprocess.check_output(
                    [str(old_supervisor), "--profile-identity", str(path)], text=True, timeout=10
                ).strip()
                owner = calls.run(
                    {
                        "action": "create",
                        "store": {
                            "root": config["content"]["root"],
                            "profile": profile,
                            "member": member,
                            "staging_gc": True,
                        },
                    },
                    "actual-original-root",
                )["identity"]
                owners[str(member)] = owner["store_owner"]
                configs[member], old_configs[member] = config, path
            for member in members:
                configs[member]["content"]["bootstrap_owners"] = owners
                save(old_configs[member], configs[member])
                current_paths[member] = old_configs[member]
                start(member, old_supervisor, old_configs[member])
            original_leader = wait_for(lambda: serving(), "genuine old application serving", 45)
            policy_request = {
                "command_id": "original-upgrade-quota-policy",
                "expected_revision": 0,
                "registry": {
                    "registry_id": "actual-upgrade-policy",
                    "revision": 1,
                    "limits": [
                        {"tenant_id": "declared-software-tenant", "kind": "Compute", "max_units": 7}
                    ],
                },
            }
            code, raw = http(
                configs[original_leader]["http_address"],
                "/v1/resource-quotas",
                "POST",
                json.dumps(policy_request).encode(),
            )
            assert code == 200, raw
            original_quota = json.loads(
                http(configs[original_leader]["http_address"], "/v1/resource-quotas")[1]
            )["registry"]
            inodes = {
                member: (Path(configs[member]["storage_directory"]) / "replica.sqlite3")
                .stat()
                .st_ino
                for member in members
            }
            for member in members:
                original = configs[member]
                upgraded = copy.deepcopy(original)
                upgraded["schema"] = "roboguide.controller-replica-process/v0.8"
                upgraded["application_upgrade"] = {
                    "base_process_schema": original["schema"],
                    "binary": str(controller),
                    "target": {
                        "application_schema": target_schema,
                        "binary_digest": "sha256:" + digest(controller),
                        "policy_digest": "pending",
                    },
                }
                target_path = output / f"configuration-{member}.json"
                save(target_path, upgraded)
                upgraded["application_upgrade"]["target"]["policy_digest"] = (
                    subprocess.check_output(
                        [str(supervisor), "--upgrade-policy-digest", str(target_path)],
                        text=True,
                        timeout=10,
                    ).strip()
                )
                save(target_path, upgraded)
                assert (
                    subprocess.check_output(
                        [str(supervisor), "--policy-digest", str(target_path)],
                        text=True,
                        timeout=10,
                    ).strip()
                    == original["replica"]["application_policy_digest"]
                )
                configs[member], current_paths[member] = upgraded, target_path
            for member in members:
                if member == original_leader:
                    continue
                stop(member)
                start(member, supervisor, current_paths[member])
                wait_for(
                    lambda member=member: status(member),
                    "new infrastructure member publication",
                    30,
                )
            successor = next(member for member in members if member != original_leader)
            admin(
                original_leader,
                "--transfer-leader",
                str(successor),
                "--transfer-id",
                "original-rolling-infrastructure-handoff",
            )
            wait_for(lambda: serving(successor), "original writer under new infrastructure", 45)
            stop(original_leader)
            start(original_leader, supervisor, current_paths[original_leader])
            wait_for(
                lambda: status(original_leader), "last original infrastructure replacement", 30
            )
            assert all(
                (Path(configs[member]["storage_directory"]) / "replica.sqlite3").stat().st_ino
                == inodes[member]
                for member in members
            )
            before = metadata(Path(configs[successor]["storage_directory"]) / "replica.sqlite3")
            assert before["checkpoint"]["schema"] == old_schema and before["version"] == 8
            original_child = status(successor)["child_pid"]
            target = configs[successor]["application_upgrade"]["target"]
            request = {
                "schema": "roboguide.application-upgrade/v0.1",
                "command_id": "actual-original-generation-upgrade",
                "expected_revision": 0,
                "from_schema": old_schema,
                "target": target,
                "budget_ms": 120000,
            }
            request_path = output / "original-upgrade-request.json"
            save(request_path, request)
            receipt = admin(successor, "--application-upgrade", str(request_path))
            assert receipt["intent"]["request"] == request and len(receipt["drains"]) == len(
                members
            )
            waits = [
                proof["proof"]["outcome"]
                for proof in receipt["drains"]
                if proof["proof"]["kind"] == "process-exit"
            ]
            assert any(outcome["process"]["pid"] == original_child for outcome in waits)
            wait_for(lambda: serving(successor), "actual upgraded application serving", 45)
            wait_for(
                lambda: all(
                    metadata(Path(configs[member]["storage_directory"]) / "replica.sqlite3")
                    .get("checkpoint", {})
                    .get("schema")
                    == target_schema
                    for member in members
                ),
                "original migration committed on all members",
                30,
            )
            assert admin(successor, "--application-upgrade", str(request_path)) == receipt
            assert (
                json.loads(http(configs[successor]["http_address"], "/v1/resource-quotas")[1])[
                    "registry"
                ]
                == original_quota
            )
            assert (
                json.loads(http(configs[successor]["http_address"], "/v1/inventory")[1])["nodes"]
                == []
            )
            negative_member = original_leader
            stop(negative_member)
            refused = subprocess.run(
                [str(old_supervisor), str(old_configs[negative_member])],
                env=environment,
                capture_output=True,
                timeout=15,
            )
            save(
                output / "actual-original-parent10-old-writer-refusal.json",
                {
                    "returncode": refused.returncode,
                    "stdout": refused.stdout.decode(),
                    "stderr": refused.stderr.decode(),
                },
            )
            assert refused.returncode != 0 and b"storage" in refused.stderr.lower()
            negative_sql = Path(configs[negative_member]["storage_directory"]) / "replica.sqlite3"
            before_unsupported = metadata(negative_sql)
            unsupported = copy.deepcopy(configs[negative_member])
            unsupported["application_upgrade"]["binary"] = str(old_controller)
            unsupported["application_upgrade"]["target"] = {
                "application_schema": old_schema,
                "binary_digest": "sha256:" + digest(old_controller),
                "policy_digest": unsupported["replica"]["application_policy_digest"],
            }
            unsupported_path = output / "unsupported-target.json"
            save(unsupported_path, unsupported)
            refusal = subprocess.run(
                [str(supervisor), str(unsupported_path)],
                env=environment,
                capture_output=True,
                timeout=20,
            )
            save(
                output / "actual-unsupported-upgrade-contract-refusal.json",
                {
                    "returncode": refusal.returncode,
                    "stdout": refusal.stdout.decode(),
                    "stderr": refusal.stderr.decode(),
                },
            )
            assert refusal.returncode != 0 and b"contract" in refusal.stderr
            after_unsupported = metadata(negative_sql)
            for key in [
                "application_upgrade_operations_v1",
                "application_upgrade_authority_v1",
                "controller_processes_v1",
                "checkpoint",
                "revision",
            ]:
                assert before_unsupported[key] == after_unsupported[key]
            start(negative_member, supervisor, current_paths[negative_member])
            wait_for(lambda: status(negative_member), "exact original new member restart", 30)
            current = successor
            for member in members:
                if member != current:
                    admin(
                        current,
                        "--transfer-leader",
                        str(member),
                        "--transfer-id",
                        f"actual-upgraded-leader-{member}",
                    )
                    wait_for(
                        lambda member=member: serving(member),
                        "actual upgraded leader " + str(member),
                        45,
                    )
                    current = member
                assert (
                    json.loads(http(configs[member]["http_address"], "/v1/resource-quotas")[1])[
                        "registry"
                    ]
                    == original_quota
                )
            original_views = {
                str(member): metadata(
                    Path(configs[member]["storage_directory"]) / "replica.sqlite3"
                )["checkpoint"]
                for member in members
            }
            assert all(view["schema"] == target_schema for view in original_views.values())
            assert all(
                metadata(Path(configs[member]["storage_directory"]) / "replica.sqlite3")["version"]
                == 10
                for member in members
            )
            result.update(
                passed=True,
                actual_generation_receipt=receipt,
                original_quota=original_quota,
                original_child_waits=waits,
                original_store_inodes=inodes,
                original_complete_member_views=original_views,
                original_infrastructure_leader=original_leader,
                actual_upgraded_source=successor,
                unsafe_original_writer_refused=True,
                unsupported_target_refused_before_intent=True,
            )
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            for member in list(running):
                try:
                    stop(member)
                except BaseException as error:
                    result.setdefault("cleanup_errors", []).append(str(error))
                    result["passed"] = False
            for process, record in owned:
                record["returncode"] = process.poll()
            result["actual_supervisors"] = [record for _, record in owned]
            save(output / "result.json", result)
            for log in logs:
                log.close()
    return result


def main() -> None:
    """Require genuine original binaries and a fresh evidence root without device inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    for option in [
        "output",
        "supervisor",
        "old-supervisor",
        "controller",
        "old-controller",
        "storage-probe",
    ]:
        parser.add_argument("--" + option, type=Path, required=True)
    arguments = parser.parse_args()
    result = run(
        arguments.output,
        arguments.supervisor.resolve(strict=True),
        arguments.old_supervisor.resolve(strict=True),
        arguments.controller.resolve(strict=True),
        arguments.old_controller.resolve(strict=True),
        arguments.storage_probe.resolve(strict=True),
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"original_complete_member_views", "actual_generation_receipt"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
