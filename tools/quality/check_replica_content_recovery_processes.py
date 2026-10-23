"""Exercise actual parent8/Root cold backup, new-inode restore and zero-Node native restart."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import digest, save
from check_runnable_content_processes import http

ROOT = Path(__file__).resolve().parents[2]


def profile(source: Path, output: Path) -> dict[str, Any]:
    """Declare only actual original members/stores and copy explicit deployment inputs privately."""
    components: list[dict[str, Any]] = []
    inputs = output / "inputs"
    inputs.mkdir(mode=0o700)
    shutil.copytree(source / "tls", inputs / "tls")
    configs = inputs / "configs"
    configs.mkdir(mode=0o700)
    for path in sorted(source.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        member = config["replica"]["replica_id"]
        shutil.copyfile(path, configs / path.name)
        directory = Path(config["storage_directory"])
        dependencies = []
        if (directory / "controller.sqlite3").exists():
            components.append(
                {
                    "component_id": f"controller-{member}",
                    "kind": "controller",
                    "root": str(directory),
                    "database": "controller.sqlite3",
                    "trees": [],
                    "depends_on": [f"cas-{member}"],
                    "storage_schema": "roboguide.controller-storage/v1",
                }
            )
            components.append(
                {
                    "component_id": f"cas-{member}",
                    "kind": "artifact",
                    "root": str(directory / "artifacts"),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                    "storage_schema": "roboguide.artifact-storage/v3",
                }
            )
            dependencies = [f"controller-{member}", f"cas-{member}"]
        components.append(
            {
                "component_id": f"parent-{member}",
                "kind": "replica",
                "root": str(directory),
                "database": "replica.sqlite3",
                "trees": [],
                "depends_on": dependencies,
            }
        )
        with closing(
            sqlite3.connect((directory / "content/content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            layout = sql.execute("SELECT value FROM metadata WHERE key='layout'").fetchone()[0]
        components.append(
            {
                "component_id": f"root-{member}",
                "kind": "replica-content",
                "root": str(directory / "content"),
                "database": None,
                "trees": [],
                "depends_on": [f"parent-{member}"],
                "storage_schema": layout,
            }
        )
    components.append(
        {
            "component_id": "inputs",
            "kind": "files",
            "root": str(inputs),
            "database": None,
            "trees": ["tls", "configs"],
            "depends_on": [
                value["component_id"] for value in components if value["kind"] == "replica"
            ],
        }
    )
    return {
        "schema": "roboguide.recovery-profile/v0.19",
        "profile_id": "actual-complete-content-roots",
        "revision": 1,
        "max_bytes": 1024**3,
        "max_files": 20000,
        "budget_seconds": 180,
        "artifact_replica_sets": [],
        "components": components,
    }


def command(
    output: Path, label: str, arguments: list[str], *, refused: bool = False
) -> dict[str, Any]:
    """Run the actual CLI once, retaining its real native wait and bounded raw response."""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    process = subprocess.run(
        [sys.executable, "-m", "roboguide_recovery", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        timeout=190,
    )
    record = {
        "arguments": arguments,
        "returncode": process.returncode,
        "stdout": process.stdout.decode(),
        "stderr": process.stderr.decode(),
    }
    save(output / (label + ".json"), record)
    assert (process.returncode != 0) == refused, record
    if refused:
        return record
    result = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


def assemble(restored: Path, ipc: Path, supervisor: Path) -> dict[int, tuple[Path, dict[str, Any]]]:
    """Move child projections/CAS into parents, preserving every bound Root/SQL inode."""
    stores = restored / "stores"
    receipts = json.loads((restored / "restore-receipt.json").read_text())
    bindings = {
        value["component_id"]: value["binding_digest"]
        for value in receipts["replica_content_bindings"]
    }
    result = {}
    configured = restored.parent / "restored-configurations"
    configured.mkdir(mode=0o700)
    for path in sorted((stores / "inputs/configs").glob("configuration-*.json")):
        config = json.loads(path.read_text())
        member = config["replica"]["replica_id"]
        parent = stores / f"parent-{member}"
        child = stores / f"controller-{member}"
        if child.exists():
            for file in child.iterdir():
                file.rename(parent / file.name)
        cas = stores / f"cas-{member}"
        if cas.exists():
            cas.rename(parent / "artifacts")
        config["schema"] = "roboguide.controller-replica-process/v0.3"
        config["storage_directory"] = str(parent)
        config["content"]["root"] = str(stores / f"root-{member}")
        config["content"]["socket"] = str(ipc / f"{member}.sock")
        config["content"]["restore_binding_digest"] = bindings[f"root-{member}"]
        config["replica"]["tls"].update(
            {
                "ca_cert_path": str(stores / "inputs/tls/ca.crt"),
                "cert_path": str(
                    stores / "inputs/tls" / Path(config["replica"]["tls"]["cert_path"]).name
                ),
                "key_path": str(
                    stores / "inputs/tls" / Path(config["replica"]["tls"]["key_path"]).name
                ),
            }
        )
        target = configured / f"configuration-{member}.json"
        save(target, config)
        actual_policy = (
            subprocess.check_output([str(supervisor), "--policy-digest", str(target)], timeout=10)
            .decode()
            .strip()
        )
        assert actual_policy == config["replica"]["application_policy_digest"]
        result[member] = (target, config)
    return result


def run(
    source: Path, output: Path, supervisor: Path, old_supervisor: Path | None, cold_only: bool
) -> dict[str, Any]:
    """Require actual source waits and complete product backup/restore before restart."""
    original = json.loads((source / "result.json").read_text())
    assert original["passed"] and all(
        value["returncode"] == 0 for value in original["actual_supervisors"]
    )
    assert (
        original["embodied_nodes"]
        == original["devices"]
        == original["models"]
        == original["simulators"]
        == 0
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")) + [
        Path(__file__),
        supervisor,
    ]
    if old_supervisor is not None:
        files.append(old_supervisor)
    identities = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identities)
    declaration = profile(source, output)
    operation = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    save(output / "profile.json", declaration)
    # Actual inode lock must refuse before creating any new Snapshot producer or bundle.
    root_sql = (
        Path(
            next(
                value["root"]
                for value in declaration["components"]
                if value["kind"] == "replica-content"
            )
        )
        / "content.sqlite3"
    )
    with root_sql.open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command(
            output,
            "active-inode-refused",
            [
                "backup",
                "--profile",
                str(output / "profile.json"),
                "--destination",
                str(output / "refused-bundle"),
                "--command-id",
                "actual-busy",
            ],
            refused=True,
        )
    created = command(
        output,
        "backup",
        [
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "actual-content-backup-" + operation,
        ],
    )
    checksum = created["manifest_digest"]
    command(
        output,
        "verify",
        ["verify", "--bundle", str(output / "bundle"), "--manifest-digest", checksum],
    )
    restore_args = [
        "restore",
        "--bundle",
        str(output / "bundle"),
        "--manifest-digest",
        checksum,
        "--destination",
        str(output / "restored"),
        "--command-id",
        "actual-content-restore-" + operation,
    ]
    restored = command(output, "restore", restore_args)
    assert len(restored["replica_content_bindings"]) == len(
        original["original_complete_member_views"]
    )
    assert command(output, "restore-readonly-repeat", restore_args) == restored
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "source": str(source),
        "manifest_digest": checksum,
        "bindings": restored["replica_content_bindings"],
        "actual_processes": [],
        "observed_leaders": [],
    }
    if cold_only:
        result["passed"] = True
        save(output / "result.json", result)
        return result
    with tempfile.TemporaryDirectory(prefix="rg-root-restore-") as temporary:
        configs = assemble(output / "restored", Path(temporary), supervisor)
        first_path = configs[min(configs)][0]
        legacy = dict(configs[min(configs)][1])
        legacy["content"] = dict(legacy["content"])
        del legacy["content"]["restore_binding_digest"]
        legacy["schema"] = "roboguide.controller-replica-process/v0.2"
        legacy_path = output / "original-root-entry.json"
        save(legacy_path, legacy)
        rejected = subprocess.run(
            [str(supervisor), str(legacy_path)], capture_output=True, timeout=10
        )
        save(
            output / "original-root-entry-refused.json",
            {
                "returncode": rejected.returncode,
                "stdout": rejected.stdout.decode(),
                "stderr": rejected.stderr.decode(),
                "actual_binary_sha256": digest(supervisor),
            },
        )
        assert rejected.returncode != 0
        result["actual_old_binary_proof"] = None
        if old_supervisor is not None and digest(old_supervisor) != digest(supervisor):
            original_binaries = json.loads((source / "source-identity.json").read_text())
            assert original_binaries.get(str(old_supervisor)) == digest(old_supervisor)
            rejected = subprocess.run(
                [str(old_supervisor), str(first_path)], capture_output=True, timeout=10
            )
            proof = {
                "returncode": rejected.returncode,
                "stdout": rejected.stdout.decode(),
                "stderr": rejected.stderr.decode(),
                "actual_binary_sha256": digest(old_supervisor),
            }
            save(output / "old-supervisor-refused.json", proof)
            assert rejected.returncode != 0
            result["actual_old_binary_proof"] = proof
        owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
        try:
            for member, (path, _) in configs.items():
                log = (output / f"supervisor-{member}.log").open("wb")
                try:
                    process = subprocess.Popen(
                        [str(supervisor), str(path)],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                finally:
                    log.close()
                row: dict[str, Any] = {
                    "member": member,
                    "pid": process.pid,
                    "configuration": str(path),
                }
                owned.append((process, row))
                result["actual_processes"].append(row)

            def current(expected: int | None = None) -> tuple[int, dict[str, Any]]:
                """Observe an actual live child and health, never a stale status-file ready flag."""
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    for member, (_, config) in configs.items():
                        if expected is not None and member != expected:
                            continue
                        path = Path(config["storage_directory"]) / "status.json"
                        try:
                            status = json.loads(path.read_text())
                            if (
                                status["child_pid"] is not None
                                and status["epoch"]["replica_id"] == member
                            ):
                                os.kill(status["child_pid"], 0)
                                if (
                                    http(config["http_address"], "/healthz", timeout_seconds=5)[0]
                                    == 200
                                ):
                                    return member, status
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                    time.sleep(0.1)
                raise ValueError("actual restored Controller did not become healthy")

            expected = None
            completed = original["actual_prepared_recovery"]["recovered"]["completion"][
                "replacement"
            ]
            published_digest, published_size = completed["content_digest"], completed["byte_size"]
            unknown = original["actual_prepared_recovery"]["unknown"]
            for turn in range(len(configs)):
                member, status = current(expected)
                _, config = configs[member]
                code, raw = http(
                    config["artifact_address"], "/v1/artifacts/" + published_digest[7:]
                )
                assert code == 200 and len(raw) == published_size
                assert "sha256:" + hashlib.sha256(raw).hexdigest() == published_digest
                code, assessment = http(
                    config["artifact_address"],
                    "/v1/artifact-uploads/"
                    + unknown["upload"]["upload_id"]
                    + "/recoveries/"
                    + unknown["command_id"]
                    + "/assessment",
                )
                assert code == 200 and json.loads(assessment)["record"] == unknown
                result["observed_leaders"].append(
                    {
                        "member": member,
                        "status": status,
                        "actual_digest": published_digest,
                        "actual_bytes": len(raw),
                    }
                )
                remaining = set(configs) - {value["member"] for value in result["observed_leaders"]}
                if not remaining:
                    break
                expected = min(remaining)
                source_sql = Path(config["storage_directory"]) / "replica.sqlite3"
                with closing(sqlite3.connect(source_sql.as_uri() + "?mode=ro", uri=True)) as sql:
                    sql.execute("BEGIN")
                    floor = dict(
                        sql.execute(
                            "SELECT key,value FROM replica_v1_meta "
                            "WHERE key IN ('applied','revision','checkpoint_digest')"
                        )
                    )
                deadline = time.monotonic() + 45
                target_sql = Path(configs[expected][1]["storage_directory"]) / "replica.sqlite3"
                caught = None
                while time.monotonic() < deadline:
                    with closing(
                        sqlite3.connect(target_sql.as_uri() + "?mode=ro", uri=True)
                    ) as sql:
                        sql.execute("BEGIN")
                        facts = dict(
                            sql.execute(
                                "SELECT key,value FROM replica_v1_meta "
                                "WHERE key IN ('applied','revision','checkpoint_digest')"
                            )
                        )
                    if json.loads(facts["applied"])["index"] >= json.loads(floor["applied"])[
                        "index"
                    ] and int(facts["revision"]) >= int(floor["revision"]):
                        caught = facts
                        break
                    observed = Path(configs[expected][1]["storage_directory"]) / "status.json"
                    if observed.exists():
                        status = json.loads(observed.read_text())
                        if status.get("raft_error"):
                            save(output / f"failure-status-{expected}.json", status)
                            raise RuntimeError(
                                "actual target Raft failure: " + status["raft_error"]
                            )
                    time.sleep(0.1)
                save(
                    output / f"catchup-before-transfer-{turn}.json",
                    {"source_floor": floor, "target": expected, "target_observation": caught},
                )
                assert caught is not None, (
                    "actual original target did not reach frozen source position"
                )
                transfer = subprocess.run(
                    [
                        str(supervisor),
                        "--admin",
                        str(configs[member][0]),
                        "--leader",
                        str(member),
                        "--transfer-leader",
                        str(expected),
                        "--transfer-id",
                        f"actual-restored-transfer-{turn}",
                    ],
                    capture_output=True,
                    timeout=100,
                )
                save(
                    output / f"transfer-{turn}.json",
                    {
                        "returncode": transfer.returncode,
                        "stdout": transfer.stdout.decode(),
                        "stderr": transfer.stderr.decode(),
                    },
                )
                assert transfer.returncode == 0
            assert len({value["member"] for value in result["observed_leaders"]}) == len(configs)
            assert all(digest(Path(path)) == checksum for path, checksum in identities.items())
            result["passed"] = True
        finally:
            for process, row in reversed(owned):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
                try:
                    row["returncode"] = process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    row["returncode"] = process.wait(timeout=10)
                    row["forced_cleanup"] = True
                if row["returncode"] != 0:
                    result["passed"] = False
            save(output / "result.json", result)
    assert result["passed"], result
    return result


def main() -> None:
    """Require explicit source evidence and frozen current/original supervisor binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supervisor", type=Path, required=True)
    parser.add_argument("--old-supervisor", type=Path)
    parser.add_argument("--cold-only", action="store_true")
    args = parser.parse_args()
    result = run(
        args.source.resolve(strict=True),
        args.output.absolute(),
        args.supervisor.resolve(strict=True),
        args.old_supervisor.resolve(strict=True) if args.old_supervisor is not None else None,
        args.cold_only,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
