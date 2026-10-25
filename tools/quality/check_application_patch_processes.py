"""Repair a real failed activation through cold recovery and a distinct same-schema patch."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any, BinaryIO

from check_application_upgrade_processes import metadata
from check_replica_content_processes import digest, save
from check_replica_content_recovery_processes import assemble, command, profile
from check_runnable_content_processes import http
from check_state_verifier_processes import ROOT, wait_for


def run(
    source: Path,
    output: Path,
    supervisor: Path,
    controller: Path,
    *,
    abort_before_patch: bool = False,
) -> dict[str, Any]:
    """Preserve a real failed activation and repair restored stores with a fresh command."""
    original = json.loads((source / "result.json").read_bytes())
    assert original["passed"] is False and all(
        row["returncode"] == 0 for row in original["actual_supervisors"]
    )
    assert all(
        original[field] == 0 for field in ["embodied_nodes", "devices", "models", "simulators"]
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    shutil.copytree(source / "tls", output / "tls")
    binary_root = output / "binaries"
    binary_root.mkdir(mode=0o700)
    for name, binary in {"supervisor": supervisor, "controller": controller}.items():
        target = binary_root / name
        shutil.copy2(binary, target)
        assert digest(binary) == digest(target)
    supervisor, controller = binary_root / "supervisor", binary_root / "controller"
    save(
        output / "source-identity.json",
        {str(path): digest(path) for path in [supervisor, controller]},
    )
    source_configs = {
        value["replica"]["replica_id"]: value
        for value in [json.loads(path.read_bytes()) for path in source.glob("configuration-*.json")]
    }
    source_rows = {
        member: metadata(Path(config["storage_directory"]) / "replica.sqlite3")
        for member, config in source_configs.items()
    }
    authority = next(iter(source_rows.values()))["application_upgrade_authority_v1"]
    assert authority and all(
        row["application_upgrade_authority_v1"] == authority for row in source_rows.values()
    )
    assert len(authority["receipts"]) >= 1
    failed_receipt = authority["receipts"][-1]
    original_schema = failed_receipt["intent"]["request"]["target"]["application_schema"]
    assert (
        subprocess.check_output(
            [str(controller), "--application-schema"], text=True, timeout=5
        ).strip()
        == original_schema
    )
    assert (
        "sha256:" + digest(controller)
        != failed_receipt["intent"]["request"]["target"]["binary_digest"]
    )
    operation = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    selected_profile = profile(source, output)
    assert selected_profile["schema"] == (
        "roboguide.recovery-profile/v0.27"
        if original_schema == "roboguide.controller-checkpoint/v58"
        else "roboguide.recovery-profile/v0.26"
        if original_schema == "roboguide.controller-checkpoint/v57"
        else "roboguide.recovery-profile/v0.25"
    )
    save(output / "profile.json", selected_profile)
    backup = command(
        output,
        "backup",
        [
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "actual-failed-application-backup-" + operation,
        ],
    )
    checksum = backup["manifest_digest"]
    command(
        output,
        "verify",
        ["verify", "--bundle", str(output / "bundle"), "--manifest-digest", checksum],
    )
    restored = command(
        output,
        "restore",
        [
            "restore",
            "--bundle",
            str(output / "bundle"),
            "--manifest-digest",
            checksum,
            "--destination",
            str(output / "restored"),
            "--command-id",
            "actual-failed-application-restore-" + operation,
        ],
    )
    assert len(restored["replica_content_bindings"]) == len(source_configs)
    processes: dict[int, subprocess.Popen[bytes]] = {}
    process_rows: list[dict[str, Any]] = []
    logs: list[BinaryIO] = []
    started: dict[int, float] = {}
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "actual_failed_source": str(source),
        "original_failed_receipt": failed_receipt,
        "manifest_digest": checksum,
    }
    with ExitStack() as scope:
        ipc = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="rg-patch-")))
        configs = assemble(output / "restored", ipc, supervisor)

        def status(member: int) -> dict[str, Any]:
            """Read a recent publication only from this actual still-live supervisor incarnation."""
            if processes[member].poll() is not None:
                return {}
            path = Path(configs[member][1]["storage_directory"]) / "status.json"
            if not path.exists():
                return {}
            observed: dict[str, Any] = json.loads(path.read_bytes())
            if (
                observed["observed_unix_ms"] < started[member]
                or time.time() * 1000 - observed["observed_unix_ms"] > 3000
            ):
                return {}
            return observed

        def admin(member: int, *arguments: str, refused: bool = False) -> dict[str, Any]:
            """Retain the raw outcome of one authenticated request without mutation retries."""
            completed = subprocess.run(
                [
                    str(supervisor),
                    "--admin",
                    str(configs[member][0]),
                    "--leader",
                    str(member),
                    *arguments,
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=135,
            )
            record = {
                "arguments": arguments,
                "returncode": completed.returncode,
                "stdout": completed.stdout.decode(),
                "stderr": completed.stderr.decode(),
            }
            save(output / f"admin-{len(list(output.glob('admin-*.json')))}.json", record)
            assert (completed.returncode != 0) == refused, record
            return record if refused else dict(json.loads(completed.stdout))

        def failed_leader() -> int | None:
            """Require actual application failure beside current infrastructure quorum."""
            for member in configs:
                observed = status(member)
                if (
                    (observed.get("epoch") or {}).get("replica_id") == member
                    and observed.get("application_error")
                    and observed.get("child_pid") is None
                ):
                    return member
            return None

        def serving(required: int | None = None) -> int | None:
            """Require actual application health under recent supervisor ownership."""
            for member, (_, config) in configs.items():
                if required is not None and member != required:
                    continue
                observed = status(member)
                if observed.get("epoch") is None or observed.get("child_pid") is None:
                    continue
                try:
                    if http(config["http_address"], "/healthz", timeout_seconds=1)[0] == 200:
                        return member
                except OSError:
                    pass
            return None

        try:
            for member, (path, config) in configs.items():
                retained = copy.deepcopy(config["application_upgrade"])
                config["application_upgrade"]["retained_binaries"] = [
                    {"binary": retained["binary"], "target": retained["target"]}
                ]
                config["application_upgrade"]["binary"] = str(controller)
                config["application_upgrade"]["target"] = {
                    "application_schema": original_schema,
                    "binary_digest": "sha256:" + digest(controller),
                    "policy_digest": "pending",
                }
                save(path, config)
                config["application_upgrade"]["target"]["policy_digest"] = subprocess.check_output(
                    [str(supervisor), "--upgrade-policy-digest", str(path)], text=True, timeout=10
                ).strip()
                save(path, config)
                save(output / f"configuration-{member}.json", config)
                log = (output / f"supervisor-{member}.log").open("xb")
                logs.append(log)
                started[member] = time.time() * 1000
                process = subprocess.Popen(
                    [str(supervisor), str(path)],
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                processes[member] = process
                process_rows.append(
                    {
                        "member": member,
                        "pid": process.pid,
                        "configuration": str(path),
                        "returncode": None,
                    }
                )
            leader = wait_for(
                failed_leader, "actual failed target with infrastructure still available", 60
            )
            before = metadata(Path(configs[leader][1]["storage_directory"]) / "replica.sqlite3")
            process_count = len(before["controller_processes_v1"])
            observed = admin(leader, "--observe-application-upgrade")
            assert observed["authority"] == authority
            # A bounded observation interval cannot be mistaken for a long-term availability claim.
            wait_for(
                lambda: all(status(member) for member in configs),
                "all actual infrastructure observations",
                15,
            )
            time.sleep(2)
            stable = metadata(Path(configs[leader][1]["storage_directory"]) / "replica.sqlite3")
            assert len(stable["controller_processes_v1"]) == process_count
            assert stable["checkpoint"] == before["checkpoint"]
            if abort_before_patch:
                interrupted = {
                    "schema": "roboguide.application-upgrade/v0.1",
                    "command_id": "actual-original-interrupted-patch",
                    "expected_revision": len(authority["receipts"]),
                    "from_schema": original_schema,
                    "target": configs[leader][1]["application_upgrade"]["target"],
                    "budget_ms": 120000,
                }
                interrupted_path = output / "original-interrupted-patch.json"
                save(interrupted_path, interrupted)
                blocked = next(member for member in sorted(configs) if member != leader)
                blocked_sql = Path(configs[blocked][1]["storage_directory"]) / "replica.sqlite3"
                with closing(sqlite3.connect(blocked_sql, timeout=0)) as held:
                    held.execute("BEGIN IMMEDIATE")
                    refusal = admin(
                        leader, "--application-upgrade", str(interrupted_path), refused=True
                    )
                    assert "locked" in refusal["stderr"]
                    held.rollback()
                retained = {
                    member: [
                        row
                        for row in metadata(Path(config["storage_directory"]) / "replica.sqlite3")[
                            "application_upgrade_operations_v1"
                        ]
                        if row["intent"]["request"]["command_id"] == interrupted["command_id"]
                    ]
                    for member, (_, config) in configs.items()
                }
                assert retained[leader]
                assert (
                    metadata(Path(configs[leader][1]["storage_directory"]) / "replica.sqlite3")[
                        "application_upgrade_authority_v1"
                    ]
                    == authority
                )
                abandonment = admin(
                    leader,
                    "--abort-application-upgrade",
                    interrupted["command_id"],
                    "actual-original-interrupted-patch-abandonment",
                )
                assert abandonment["intent"]["request"] == interrupted
                for member, (_, config) in configs.items():
                    observed_rows = metadata(Path(config["storage_directory"]) / "replica.sqlite3")[
                        "application_upgrade_operations_v1"
                    ]
                    for original_row in retained[member]:
                        assert original_row in observed_rows
                result["actual_sql_failure_and_original_abandonment"] = {
                    "blocked_member": blocked,
                    "receipt": abandonment,
                    "original_local_rows": retained,
                    "activation_never_retried": True,
                }
            request = {
                "schema": "roboguide.application-upgrade/v0.1",
                "command_id": "actual-original-same-schema-patch",
                "expected_revision": len(authority["receipts"]),
                "from_schema": original_schema,
                "target": configs[leader][1]["application_upgrade"]["target"],
                "budget_ms": 120000,
            }
            path = output / "original-patch-request.json"
            save(path, request)
            receipt = admin(leader, "--application-upgrade", str(path))
            assert receipt["intent"]["request"] == request
            assert (
                receipt["intent"]["predecessor"]["checkpoint_digest"] == before["checkpoint_digest"]
            )
            wait_for(
                lambda: serving(leader), "actual fixed application serving after original patch", 60
            )
            quota = json.loads(http(configs[leader][1]["http_address"], "/v1/resource-quotas")[1])[
                "registry"
            ]
            source_checkpoint = json.loads(before["checkpoint"]["checkpoint_json"])
            assert (
                quota
                == json.loads(source_checkpoint["integration_json"])["control"][
                    "resource_quota_registry"
                ]
            )
            assert (
                json.loads(http(configs[leader][1]["http_address"], "/v1/inventory")[1])["nodes"]
                == []
            )
            assert admin(leader, "--application-upgrade", str(path)) == receipt
            rollback = copy.deepcopy(request)
            rollback.update(
                command_id="actual-refused-retired-binary", expected_revision=receipt["revision"]
            )
            rollback["target"] = failed_receipt["intent"]["request"]["target"]
            save(output / "unsafe-retired-binary-request.json", rollback)
            admin(
                leader,
                "--application-upgrade",
                str(output / "unsafe-retired-binary-request.json"),
                refused=True,
            )
            current = leader
            for member in configs:
                if member != current:
                    admin(
                        current,
                        "--transfer-leader",
                        str(member),
                        "--transfer-id",
                        "actual-patched-leader-" + str(member),
                    )
                    wait_for(
                        lambda member=member: serving(member),
                        "actual patched leader " + str(member),
                        60,
                    )
                    current = member
                assert (
                    json.loads(http(configs[member][1]["http_address"], "/v1/resource-quotas")[1])[
                        "registry"
                    ]
                    == quota
                )
            result.update(
                passed=True,
                actual_generation_receipt=receipt,
                original_quota=quota,
                original_complete_member_views={
                    str(member): metadata(Path(config["storage_directory"]) / "replica.sqlite3")[
                        "checkpoint"
                    ]
                    for member, (_, config) in configs.items()
                },
                unsafe_retired_binary_refused=True,
                failed_application_does_not_restart_loop=True,
                infrastructure_repair_available=True,
            )
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            for row in process_rows:
                process = processes[row["member"]]
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    row["returncode"] = process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    row["returncode"] = process.wait(timeout=5)
                    result["passed"] = False
            result["actual_supervisors"] = process_rows
            save(output / "result.json", result)
            for owned_log in logs:
                owned_log.close()
    return result


def main() -> None:
    """Require an explicit actual failed source and actual repair binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ["source", "output", "supervisor", "controller"]:
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--abort-before-patch", action="store_true")
    arguments = parser.parse_args()
    result = run(
        arguments.source.resolve(strict=True),
        arguments.output,
        arguments.supervisor.resolve(strict=True),
        arguments.controller.resolve(strict=True),
        abort_before_patch=arguments.abort_before_patch,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "original_complete_member_views",
                    "actual_generation_receipt",
                    "original_failed_receipt",
                }
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
