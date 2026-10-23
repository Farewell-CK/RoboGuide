"""Check real supervisor/Controller/HTTP/CAS wiring with zero embodied Nodes or device facts."""

from __future__ import annotations

import argparse
import hashlib
import http.client as http_client
import json
import os
import signal
import sqlite3
import subprocess
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from check_network_content_processes import actual_bytes
from check_replica_certificate_processes import create_ca, issue
from check_replica_content_processes import Processes, digest, save
from check_state_verifier_processes import address

ROOT = Path(__file__).resolve().parents[2]


def http(origin: str, path: str, method: str = "GET", body: bytes = b"") -> tuple[int, bytes]:
    """Send one actual local HTTP request; mutation errors never automatically re-execute it."""
    host, port = origin.rsplit(":", 1)
    connection = http_client.HTTPConnection(host, int(port), timeout=60)
    try:
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("actual HTTP evidence exceeds its original bound")
        return response.status, raw
    finally:
        connection.close()


def run(output: Path, supervisor: Path, controller: Path, storage: Path) -> dict[str, Any]:
    """Retain exact binaries, native waits, complete SQL and independently verified bytes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    paths = [supervisor, controller, storage, Path(__file__)]
    for owner in (
        "core/controller-replication",
        "core/artifact-store",
        "core/state",
        "apps/controller-replica",
        "apps/integration-server",
    ):
        paths += list((ROOT / owner).rglob("*.rs"))
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    calls = Processes(storage, output)
    owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
    configs: dict[int, dict[str, Any]] = {}
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "scope": "actual current-owner runnable HTTP and original child restart",
        "observed_phases": [],
    }

    def observed(label: str, **facts: Any) -> None:
        """Retain each actual completed observation even if a later original step fails."""
        result["observed_phases"].append({"phase": label, **facts})
        save(output / "result.json", result)

    with ExitStack() as scope:
        try:
            ipc = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="rg-live-content-")))
            tls = output / "tls"
            create_ca(tls)
            members = [307, 311, 337]
            leaves = {member: issue(tls, f"member-{member}") for member in members}
            pins = {str(member): value[1] for member, value in leaves.items()}
            endpoints = {str(member): address() for member in members}
            schema = (
                subprocess.check_output([str(controller), "--application-schema"], timeout=10)
                .decode()
                .strip()
            )
            owners: dict[str, str] = {}
            profiles = set()
            for member in members:
                directory = output / f"member-{member}"
                directory.mkdir(mode=0o700)
                bundle = str(leaves[member][0])
                config: dict[str, Any] = {
                    "schema": "roboguide.controller-replica-process/v0.2",
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": "actual-runnable-original-content",
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members,
                        "application_schema": schema,
                        "application_policy_digest": "pending",
                        "tls": {
                            "ca_cert_path": str(tls / "ca.crt"),
                            "cert_path": bundle,
                            "key_path": bundle,
                            "server_name": "localhost",
                            "peer_certificate_digests": pins,
                        },
                    },
                    "storage_directory": str(directory),
                    "controller_binary": str(controller),
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
                        "socket": str(ipc / f"member-{member}.sock"),
                        "child_budget_ms": 45000,
                        "quorum_observation_ms": 15000,
                    },
                }
                path = output / f"configuration-{member}.json"
                save(path, config)
                policy = (
                    subprocess.check_output(
                        [str(supervisor), "--policy-digest", str(path)], timeout=10
                    )
                    .decode()
                    .strip()
                )
                config["replica"]["application_policy_digest"] = policy
                save(path, config)
                profile = (
                    subprocess.check_output(
                        [str(supervisor), "--profile-identity", str(path)], timeout=10
                    )
                    .decode()
                    .strip()
                )
                profiles.add(profile)
                owner = calls.run(
                    {
                        "action": "create",
                        "store": {
                            "root": config["content"]["root"],
                            "profile": profile,
                            "member": member,
                        },
                    },
                    "actual-runnable-root",
                )["identity"]
                owners[str(member)] = owner["store_owner"]
                configs[member] = config
            assert len(profiles) == 1
            for member, config in configs.items():
                config["content"]["bootstrap_owners"] = owners
                path = output / f"configuration-{member}.json"
                save(path, config)
                log = scope.enter_context((output / f"supervisor-{member}.log").open("xb"))
                process = subprocess.Popen(
                    [str(supervisor), str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                    start_new_session=True,
                )
                owned.append(
                    (
                        process,
                        {
                            "pid": process.pid,
                            "member": member,
                            "configuration": str(path),
                            "binary": str(supervisor),
                        },
                    )
                )

            def current(previous_pid: int | None = None) -> tuple[int, dict[str, Any]]:
                """Poll actual status/health without restarting a failed supervisor."""
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    for process, record in owned:
                        if process.poll() is not None:
                            raise ValueError(
                                f"actual supervisor exited: {record['member']}:{process.poll()}"
                            )
                        member = int(record["member"])
                        try:
                            status = json.loads(
                                (
                                    Path(configs[member]["storage_directory"]) / "status.json"
                                ).read_bytes()
                            )
                            if status["epoch"] is not None and status["child_pid"] is not None:
                                if status["child_pid"] == previous_pid:
                                    continue
                                code, _ = http(configs[member]["http_address"], "/healthz")
                                if code == 200:
                                    return member, status
                        except (OSError, ValueError, http_client.HTTPException):
                            pass
                    time.sleep(0.05)
                raise ValueError(
                    "actual runnable Controller did not become healthy within its bound"
                )

            member, first = current()
            observed("original-controller-healthy", member=member, status=first)
            origin = configs[member]["artifact_address"]
            payload = os.urandom(128 * 1024 + 29)
            checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
            upload = "actual-runnable-original-upload"
            assert (
                http(
                    origin,
                    "/v1/artifact-uploads",
                    "POST",
                    json.dumps({"upload_id": upload}).encode(),
                )[0]
                == 201
            )
            observed("original-upload-started", upload_id=upload)
            assert http(origin, f"/v1/artifact-uploads/{upload}/content", "POST", payload)[0] == 202
            observed("original-content-appended", upload_id=upload, actual_bytes=len(payload))
            code, raw = http(
                origin,
                f"/v1/artifact-uploads/{upload}/finalize",
                "POST",
                json.dumps({"content_digest": checksum, "byte_size": len(payload)}).encode(),
            )
            assert code == 201, raw
            completed = json.loads(raw)
            assert completed["receipt"]["phase"] == "completed"
            observed("original-upload-completed", receipt=completed)
            assert http(origin, "/v1/artifacts/" + checksum[7:]) == (200, payload)
            observed("original-http-content-read", actual_digest=checksum)
            originals: dict[int, Any] = {}
            for replica, config in configs.items():
                parent = Path(config["storage_directory"]) / "replica.sqlite3"
                with closing(sqlite3.connect(parent.as_uri() + "?mode=ro", uri=True)) as sql:
                    assert sql.execute("PRAGMA user_version").fetchone()[0] == 8
                    content = json.loads(
                        sql.execute(
                            "SELECT value FROM replica_v1_meta WHERE key='content_state'"
                        ).fetchone()[0]
                    )
                assert any(
                    key["content_digest"] == checksum for key in content["candidate"]["inputs"]
                )
                assert any(
                    key["content_digest"] == checksum
                    for key in actual_bytes(Path(config["content"]["root"]))
                )
                originals[replica] = content
            observed("all-three-original-byte-views", members=sorted(originals))
            original_inodes = {
                str(path): [path.stat().st_dev, path.stat().st_ino]
                for path in (Path(configs[member]["storage_directory"])).rglob("*")
                if path.is_file() and (path.suffix == ".sqlite3" or path.name.endswith(".writer"))
            }
            assert original_inodes
            source_writer = originals[member]["candidate"]["source_writer"]
            old_pid = first["child_pid"]
            os.kill(old_pid, signal.SIGKILL)
            observed("original-child-sigkill-sent", pid=old_pid)
            successor, restarted = current(old_pid)
            assert successor == member
            assert restarted["epoch"] == first["epoch"]
            observed("actual-successor-healthy", member=successor, status=restarted)
            assert http(origin, "/v1/artifacts/" + checksum[7:]) == (200, payload)
            observed("restarted-http-content-read", actual_digest=checksum)
            with closing(
                sqlite3.connect(
                    (Path(configs[member]["storage_directory"]) / "replica.sqlite3").as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as sql:
                history = json.loads(
                    sql.execute(
                        "SELECT value FROM replica_v1_meta WHERE key='controller_processes_v1'"
                    ).fetchone()[0]
                )
            ended = [row for row in history if row["process"] and row["process"]["pid"] == old_pid]
            assert len(ended) == 1 and ended[0]["outcome"]["signal"] == 9
            assert len(history) == 2, "actual Controller restarted outside the one original fault"
            assert all(
                [Path(path).stat().st_dev, Path(path).stat().st_ino] == identity
                for path, identity in original_inodes.items()
            )
            with closing(
                sqlite3.connect(
                    (Path(configs[member]["storage_directory"]) / "replica.sqlite3").as_uri()
                    + "?mode=ro",
                    uri=True,
                )
            ) as sql:
                current_content = json.loads(
                    sql.execute(
                        "SELECT value FROM replica_v1_meta WHERE key='content_state'"
                    ).fetchone()[0]
                )
            assert current_content["candidate"]["source_writer"] == source_writer
            assert all(digest(Path(path)) == expected for path, expected in identities.items())
            result.update(
                passed=True,
                original_controller=first,
                actual_restarted_controller=restarted,
                original_member=member,
                original_upload=completed,
                actual_digest=checksum,
                actual_bytes=len(payload),
                original_complete_member_views=originals,
                actual_original_wait=ended[0]["outcome"],
                original_store_inodes=original_inodes,
                original_artifact_writer=source_writer,
            )
        finally:
            cleanup_errors = []
            for process, record in reversed(owned):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
                try:
                    record["returncode"] = process.wait(timeout=20)
                    if record["returncode"] != 0:
                        cleanup_errors.append(f"{record['member']}:{record['returncode']}")
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    record["returncode"] = process.wait(timeout=10)
                    cleanup_errors.append(f"{record['member']}:forced cleanup")
            calls.cleanup()
            result["actual_supervisors"] = [record for _, record in owned]
            result["actual_storage_processes"] = [record for _, record in calls.owned]
            if cleanup_errors:
                result["passed"] = False
                result["cleanup_errors"] = cleanup_errors
            save(output / "result.json", result)
    if not result["passed"]:
        raise ValueError("actual runnable content check did not pass; original evidence retained")
    return result


def main() -> None:
    """Require frozen actual product binaries and fresh inspectable evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supervisor", type=Path, required=True)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--storage-probe", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.output,
        args.supervisor.resolve(strict=True),
        args.controller.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
