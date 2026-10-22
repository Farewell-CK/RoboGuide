"""Restore original rotated replica stores and actual CAS through the shipped recovery CLI."""

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
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO, cast

from check_controller_membership_processes import free_address, save, wait_for
from check_recovery_bundle_processes import logical_rows
from check_replica_certificate_processes import read_trust

ROOT = Path(__file__).resolve().parents[2]


def request(
    origin: str, path: str, method: str = "GET", body: bytes | None = None
) -> tuple[int, bytes]:
    """Contact only an explicitly configured owned loopback endpoint without ambient proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    message = urllib.request.Request(origin + path, data=body, method=method)
    try:
        response = opener.open(message, timeout=30)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("replica process response exceeds its explicit bound")
        return response.status, payload


def storage_profile(
    root: Path, configs: dict[int, dict[str, Any]], replicate: bool
) -> dict[str, Any]:
    """Declare every actual original supervisor, child, CAS and credential/config dependency."""
    components: list[dict[str, Any]] = []
    storage_versions = set()
    artifact_formats: set[str] = set()
    replica_ids = [f"replica-{member}" for member in configs]
    for member, config in configs.items():
        storage = Path(config["storage_directory"])
        with closing(
            sqlite3.connect((storage / "replica.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as connection:
            storage_versions.add(connection.execute("PRAGMA user_version").fetchone()[0])
        dependencies: list[str] = []
        cas = storage / "artifacts"
        if cas.exists():
            artifact_formats.add(
                "retained" if (cas / ".retention-owner.json").is_file() else "legacy"
            )
            dependencies.append(f"cas-{member}")
            components.append(
                {
                    "component_id": f"cas-{member}",
                    "kind": "artifact",
                    "root": str(cas),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                }
            )
        if (storage / "controller.sqlite3").exists():
            components.append(
                {
                    "component_id": f"controller-{member}",
                    "kind": "controller",
                    "root": str(storage),
                    "database": "controller.sqlite3",
                    "trees": [],
                    "depends_on": list(dependencies),
                }
            )
            dependencies.append(f"controller-{member}")
        components.append(
            {
                "component_id": f"replica-{member}",
                "kind": "replica",
                "root": str(storage),
                "database": "replica.sqlite3",
                "trees": [],
                "depends_on": dependencies,
            }
        )
    components.extend(
        [
            {
                "component_id": "credentials",
                "kind": "files",
                "root": str(root),
                "database": None,
                "trees": ["pki", "pki-next"],
                "depends_on": replica_ids,
            },
            {
                "component_id": "deployment",
                "kind": "files",
                "root": str(root / "deployment"),
                "database": None,
                "trees": ["configs"],
                "depends_on": replica_ids,
            },
        ]
    )
    if storage_versions not in ({0}, {1}, {2}, {3}):
        raise ValueError("actual Replica stores require one complete supported storage profile")
    if len(artifact_formats) != 1 or (artifact_formats == {"retained"} and storage_versions != {3}):
        raise ValueError("actual Artifact roots require one explicit complete storage profile")
    return {
        "schema": "roboguide.recovery-profile/v0.9"
        if artifact_formats == {"retained"}
        else "roboguide.recovery-profile/v0.7"
        if storage_versions == {3}
        else "roboguide.recovery-profile/v0.6"
        if storage_versions == {2}
        else "roboguide.recovery-profile/v0.5"
        if storage_versions == {1}
        else "roboguide.recovery-profile/v0.2",
        "profile_id": "actual-rotated-cluster-stores",
        "revision": 1,
        "max_bytes": 1024 * 1024 * 1024,
        "max_files": 10000,
        "budget_seconds": 120,
        "artifact_replica_sets": [[f"cas-{member}" for member in configs]] if replicate else [],
        "components": components,
    }


def assemble(restored: Path, destination: Path, previous: Path) -> dict[int, dict[str, Any]]:
    """Configure explicit new paths after verified restore, preserving original profile identity."""
    destination.mkdir(mode=0o700)
    stores = restored / "stores"
    for name in ("pki", "pki-next"):
        shutil.copytree(stores / "credentials" / name, destination / name)
    config_root = destination / "deployment/configs"
    config_root.mkdir(mode=0o700, parents=True)
    configs: dict[int, dict[str, Any]] = {}
    for path in sorted((stores / "deployment/configs").glob("member-*.json")):
        config = cast(dict[str, Any], json.loads(path.read_text()))
        member = int(config["replica"]["replica_id"])
        storage = destination / f"replica-{member}"
        shutil.copytree(stores / f"replica-{member}", storage)
        child = stores / f"controller-{member}/controller.sqlite3"
        if child.exists():
            shutil.copyfile(child, storage / "controller.sqlite3")
        cas = stores / f"cas-{member}"
        if cas.exists():
            shutil.copytree(cas, storage / "artifacts")
        config["storage_directory"] = str(storage)
        for field in ("ca_cert_path", "cert_path", "key_path"):
            config["replica"]["tls"][field] = str(
                destination / Path(config["replica"]["tls"][field]).relative_to(previous)
            )
        for field in ("grpc_address", "http_address", "artifact_address"):
            config[field] = free_address()
        save(config_root / path.name, config)
        configs[member] = config
    return configs


def run(source: Path, output: Path, old_recovery_root: Path | None = None) -> dict[str, Any]:
    """Restore original history and actual CAS bytes, then recover all three endpoints."""
    source = source.resolve(strict=True)
    original_result = json.loads((source / "result.json").read_text())
    if (
        not original_result["passed"]
        or not original_result["owned_supervisors_stopped_gracefully"]
        or not original_result["owned_processes_reaped"]
    ):
        raise ValueError(
            "source must be the completed original controlled certificate process gate"
        )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    binary = ROOT / "target/debug/controller-replica"
    controller = ROOT / "target/debug/integration-server"
    if (
        hashlib.sha256(controller.read_bytes()).hexdigest()
        != original_result["controller_binary_sha256"]
    ):
        raise ValueError("this same-version restore requires the exact original Controller binary")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    original_configs = {
        member: cast(dict[str, Any], json.loads((source / f"member-{member}.json").read_text()))
        for member in range(1, 4)
    }
    config_root = source / "deployment/configs"
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for member, config in original_configs.items():
        save(config_root / f"member-{member}.json", config)
    processes: dict[int, subprocess.Popen[bytes]] = {}
    logs: list[BinaryIO] = []
    owned: list[subprocess.Popen[bytes]] = []
    exit_codes: list[int] = []
    configs = original_configs
    active_root = source
    result: dict[str, Any] = {
        "schema": "roboguide.replica-cold-recovery-process-check/v0.1",
        "passed": False,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }

    def recovery(operation: str, *arguments: str, accepted: bool = True) -> Any:
        """Run the shipped independent CLI against only explicitly selected original stores."""
        completed = subprocess.run(
            [sys.executable, "-m", "roboguide_recovery", operation, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
        if not accepted:
            if completed.returncode == 0:
                raise ValueError("active supervisor incorrectly admitted a quiescent backup")
            return {"returncode": completed.returncode, "stderr": completed.stderr[-2048:]}
        if completed.returncode != 0:
            raise ValueError("actual recovery CLI failed: " + completed.stderr[-2048:])
        return json.loads(completed.stdout)

    def start_all(label: str) -> None:
        """Start each declared new directory with its original consensus identity."""
        for member in configs:
            log = (output / f"{label}-member-{member}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen(
                [str(binary), str(active_root / f"deployment/configs/member-{member}.json")],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            processes[member] = process
            owned.append(process)

    def stop_all() -> None:
        """Reap each exact owned process group and retain every actual supervisor exit result."""
        for member in list(processes):
            process = processes.pop(member)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            exit_codes.append(process.wait(timeout=0))

    def leader() -> int | None:
        """Require both actual owned process ancestry and successful real Controller HTTP health."""
        for member, process in processes.items():
            if process.poll() is not None:
                continue
            try:
                state = json.loads((active_root / f"replica-{member}/status.json").read_text())
                child = state.get("child_pid")
                if (
                    child is not None
                    and state.get("epoch") is not None
                    and os.getpgid(int(child)) == process.pid
                ):
                    if request("http://" + configs[member]["http_address"], "/healthz")[0] == 200:
                        return member
            except (OSError, ValueError):
                continue
        return None

    def admin(caller: int, target: int, *arguments: str) -> Any:
        """Use actual current mTLS and original local trust, never change SQL to select a leader."""
        completed = subprocess.run(
            [
                str(binary),
                "--admin",
                str(active_root / f"deployment/configs/member-{caller}.json"),
                "--leader",
                str(target),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=25,
        )
        if completed.returncode != 0:
            raise ValueError("actual replica admin failed: " + completed.stderr[-2048:])
        return json.loads(completed.stdout)

    def every_original_receipt() -> bool:
        """Observe all original immutable trust receipts through the actual restored databases."""
        return all(
            read_trust(active_root / f"replica-{member}") == original_result["restored_trust"]
            for member in configs
        )

    def choose(target: int, identity: str) -> int:
        """Transfer only through original authenticated protocol and observe actual target child."""
        current = int(wait_for(leader, "current actual Controller"))
        if current != target:
            admin(current, current, "--transfer-leader", str(target), "--transfer-id", identity)
            wait_for(lambda: leader() == target, "actual selected infrastructure leader")
        return target

    try:
        initial_profile = storage_profile(source, original_configs, False)
        save(output / "original-profile.json", initial_profile)
        initial = recovery(
            "backup",
            "--profile",
            str(output / "original-profile.json"),
            "--destination",
            str(output / "original-bundle"),
            "--command-id",
            "original-rotated-source",
        )
        recovery(
            "restore",
            "--bundle",
            str(output / "original-bundle"),
            "--manifest-digest",
            initial["manifest_digest"],
            "--destination",
            str(output / "source-restored"),
            "--command-id",
            "original-source-copy",
        )
        active_root = output / "working-source"
        configs = assemble(output / "source-restored", active_root, source)
        start_all("source")
        current = int(wait_for(leader, "cold-restored original rotated Controller"))
        wait_for(every_original_receipt, "all ten original receipts")
        for member in configs:
            choose(member, f"original-source-owner-{member}")
        current = int(wait_for(leader, "actual final source owner"))
        payload = (ROOT / "core/domain/src/memory.rs").read_bytes()
        checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        upload = "actual-original-replica-recovery-bytes"
        artifact = "http://" + configs[current]["artifact_address"]
        assert (
            request(
                artifact, "/v1/artifact-uploads", "POST", json.dumps({"upload_id": upload}).encode()
            )[0]
            == 201
        )
        assert (
            request(artifact, f"/v1/artifact-uploads/{upload}/content", "POST", payload)[0] == 202
        )
        status, raw_receipt = request(
            artifact,
            f"/v1/artifact-uploads/{upload}/finalize",
            "POST",
            json.dumps({"content_digest": checksum, "byte_size": len(payload)}).encode(),
        )
        assert status == 201, raw_receipt
        assert request(artifact, "/v1/artifacts/" + checksum[7:]) == (200, payload)
        result["actual_original_upload_receipt"] = json.loads(raw_receipt)
        result["actual_original_content_digest"] = checksum
        result["actual_original_content_bytes"] = len(payload)
        stop_all()
        profile = storage_profile(active_root, configs, True)
        save(output / "complete-profile.json", profile)
        if old_recovery_root is not None:
            old_environment = dict(
                environment, PYTHONPATH=str(old_recovery_root.resolve(strict=True))
            )
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "roboguide_recovery",
                    "backup",
                    "--profile",
                    str(output / "complete-profile.json"),
                    "--destination",
                    str(output / "old-tool-refused"),
                    "--command-id",
                    "original-old-tool-fence",
                ],
                cwd=ROOT,
                env=old_environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            assert (
                rejected.returncode != 0
                and not (output / "old-tool-refused.partial-original-old-tool-fence").exists()
            )
            result["actual_old_recovery_tool_refused"] = {
                "returncode": rejected.returncode,
                "stderr": rejected.stderr[-2048:],
                "module_sha256": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted((old_recovery_root / "roboguide_recovery").glob("*.py"))
                },
            }
        with (active_root / "replica-1/supervisor.writer.lock").open("rb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result["actual_live_supervisor_refused"] = recovery(
                "backup",
                "--profile",
                str(output / "complete-profile.json"),
                "--destination",
                str(output / "live-refused"),
                "--command-id",
                "original-live-fence",
                accepted=False,
            )
            assert not (output / "live-refused.partial-original-live-fence").exists()
        original_sql = {
            component["component_id"]: logical_rows(Path(component["root"]) / component["database"])
            for component in profile["components"]
            if component["database"] is not None
        }
        save(output / "original-sql-oracle.json", original_sql)
        created = recovery(
            "backup",
            "--profile",
            str(output / "complete-profile.json"),
            "--destination",
            str(output / "complete-bundle"),
            "--command-id",
            "original-complete-cluster",
        )
        recovery(
            "verify",
            "--bundle",
            str(output / "complete-bundle"),
            "--manifest-digest",
            created["manifest_digest"],
        )
        restored = recovery(
            "restore",
            "--bundle",
            str(output / "complete-bundle"),
            "--manifest-digest",
            created["manifest_digest"],
            "--destination",
            str(output / "complete-restored"),
            "--command-id",
            "original-complete-restore",
        )
        assert len(restored["artifact_replica_placements"]) == 2
        for component in profile["components"]:
            if component["database"] is not None:
                path = (
                    output
                    / "complete-restored/stores"
                    / component["component_id"]
                    / component["database"]
                )
                assert logical_rows(path) == original_sql[component["component_id"]]
        previous = active_root
        active_root = output / "working-restored"
        configs = assemble(output / "complete-restored", active_root, previous)
        start_all("restored")
        current = int(wait_for(leader, "complete restored real quorum child"))
        wait_for(every_original_receipt, "complete restored original trust")
        verified = []
        for member in configs:
            choose(member, f"restored-content-owner-{member}")
            origin = "http://" + configs[member]["artifact_address"]
            status, actual = request(origin, "/v1/artifacts/" + checksum[7:])
            assert (
                status == 200
                and hashlib.sha256(actual).digest() == hashlib.sha256(payload).digest()
            )
            status, original_finalize = request(
                origin,
                f"/v1/artifact-uploads/{upload}/finalize",
                "POST",
                json.dumps({"content_digest": checksum, "byte_size": len(payload)}).encode(),
            )
            assert status == 201 and original_finalize == raw_receipt
            verified.append(
                {
                    "member": member,
                    "actual_digest": "sha256:" + hashlib.sha256(actual).hexdigest(),
                    "original_receipt_unchanged": True,
                }
            )
        result.update(
            passed=True,
            manifest_digest=created["manifest_digest"],
            original_sql_databases=len(original_sql),
            original_sql_tables=sum(len(value) - 1 for value in original_sql.values()),
            restored_actual_endpoints=verified,
            restore_receipt=restored,
            original_trust_revision=original_result["restored_trust"]["revision"],
            controller_binary_sha256=hashlib.sha256(controller.read_bytes()).hexdigest(),
            replica_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        )
        return result
    except Exception:
        for member, process in processes.items():
            if process.poll() is not None:
                continue
            try:
                state = json.loads((active_root / f"replica-{member}/status.json").read_text())
                child = state.get("child_pid")
                if child is None or os.getpgid(int(child)) != process.pid:
                    continue
                with (output / f"failure-owned-child-{member}-stacks.log").open(
                    "wb"
                ) as diagnostic_log:
                    subprocess.run(
                        [
                            "gdb",
                            "--batch",
                            "-ex",
                            "set print frame-arguments none",
                            "-ex",
                            "thread apply all bt 10",
                            "-p",
                            str(child),
                        ],
                        stdout=diagnostic_log,
                        stderr=diagnostic_log,
                        timeout=15,
                        check=False,
                    )
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
        raise
    finally:
        stop_all()
        for log in logs:
            log.close()
        result["owned_processes_reaped"] = all(process.poll() is not None for process in owned)
        result["owned_supervisor_exit_codes"] = exit_codes
        result["passed"] = (
            bool(result["passed"])
            and result["owned_processes_reaped"]
            and all(code == 0 for code in exit_codes)
        )
        save(output / "result.json", result)


def main() -> None:
    """Require the original completed process evidence and a new isolated recovery directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-recovery-root", type=Path)
    arguments = parser.parse_args()
    result = run(arguments.source, arguments.output.resolve(), arguments.old_recovery_root)
    print(
        json.dumps(
            {key: result[key] for key in ("passed", "nodes", "devices", "owned_processes_reaped")}
        )
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
