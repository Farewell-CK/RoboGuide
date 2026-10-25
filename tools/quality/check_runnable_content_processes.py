"""Check real supervisor/Controller/HTTP/CAS wiring with zero embodied Nodes or device facts."""

from __future__ import annotations

import argparse
import hashlib
import http.client as http_client
import json
import os
import shutil
import signal
import socket
import sqlite3
import ssl
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
from runnable_prepared_recovery import release as release_prepared
from runnable_prepared_recovery import retire_completed
from runnable_prepared_recovery import stage as stage_prepared
from runnable_prepared_recovery import verify as verify_prepared

ROOT = Path(__file__).resolve().parents[2]


def http(
    origin: str,
    path: str,
    method: str = "GET",
    body: bytes = b"",
    *,
    timeout_seconds: float = 60,
    tls_context: ssl.SSLContext | None = None,
) -> tuple[int, bytes]:
    """Send one actual local HTTP request; mutation errors never automatically re-execute it."""
    secure = origin.startswith("https://")
    if secure:
        origin = origin.removeprefix("https://")
        if tls_context is None:
            raise ValueError("HTTPS observation requires the explicit original client TLS context")
    host, port = origin.rsplit(":", 1)
    connection = (
        http_client.HTTPSConnection(host, int(port), timeout=timeout_seconds, context=tls_context)
        if secure
        else http_client.HTTPConnection(host, int(port), timeout=timeout_seconds)
    )
    try:
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("actual HTTP evidence exceeds its original bound")
        return response.status, raw
    finally:
        connection.close()


def recovery_http(origin: str, path: str, method: str, body: bytes) -> tuple[int, bytes]:
    """Wait once for the declared 120-second budget and bounded response time."""
    return http(origin, path, method, body, timeout_seconds=135)


def idle_snapshot(directory: Path) -> dict[str, Any]:
    """Read original committed SQL and complete raw checkpoint without any application request."""
    with closing(
        sqlite3.connect((directory / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        sql.execute("BEGIN")
        checkpoint = sql.execute(
            "SELECT event_sequence,schema,checkpoint_json FROM controller_checkpoint "
            "WHERE singleton=1"
        ).fetchone()
        assert checkpoint is not None
        revision = sql.execute(
            "SELECT value FROM event_log_metadata WHERE key='journal_committed_revision'"
        ).fetchone()
        assert revision is not None
        local = {
            "journal_revision": int(revision[0]),
            "event_sequence": checkpoint[0],
            "checkpoint_schema": checkpoint[1],
            "checkpoint_sha256": hashlib.sha256(checkpoint[2].encode()).hexdigest(),
        }
    with closing(
        sqlite3.connect((directory / "replica.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        local["parent_revision"] = int(
            sql.execute("SELECT value FROM replica_v1_meta WHERE key='revision'").fetchone()[0]
        )
    return local


def source_streams(configs: dict[int, dict[str, Any]], owner: str) -> dict[int, dict[str, str]]:
    """Read exact retained source-owner stream rows without creating another transfer or receipt."""
    result = {}
    for member, config in configs.items():
        path = Path(config["content"]["root"]) / "content.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
            result[member] = {
                key: raw
                for key, raw in sql.execute("SELECT stream_id,record FROM content_streams")
                if json.loads(raw)["intent"]["content"]["source_owner"] == owner
            }
    return result


def run(
    output: Path,
    supervisor: Path,
    controller: Path,
    storage: Path,
    idle_timer_check: bool = False,
    owner_failover: bool = False,
    prepared_recovery: bool = False,
    release_after_recovery: bool = False,
    initialization_source: Path | None = None,
    coordinated_archive: bool = False,
    archive_sql_fault: bool = False,
) -> dict[str, Any]:
    """Retain exact binaries, native waits, complete SQL and independently verified bytes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    paths = [
        supervisor,
        controller,
        storage,
        Path(__file__),
        Path(__file__).with_name("runnable_prepared_recovery.py"),
        Path(__file__).with_name("runnable_coordinated_archive.py"),
    ]
    for owner in (
        "core/domain",
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
    source_policy = None
    source_input = None
    node_tls = None
    operator_tls = None
    artifact_tls = None
    transport_context = None
    if initialization_source is not None:
        retained = json.loads((initialization_source / "result.json").read_bytes())
        waits = json.loads((initialization_source / "owned-process-exits.json").read_bytes())
        assert retained["passed"] and waits["all_owned_processes_reaped"]
        source_input = {
            "source_sql": str(initialization_source / "controller.sqlite3"),
            "source_cas": str(initialization_source / "cas"),
        }
        path = output / "original-source-input.json"
        save(path, source_input)
        source = json.loads(
            subprocess.check_output(
                [str(supervisor), "--inspect-initialization-source", str(path)], timeout=30
            )
        )
        save(output / "original-source-observation.json", source)
        source_policy = {
            "schema": "roboguide.content-initialization-policy/v0.1",
            "source_state_digest": source["source_state_digest"],
            "source_owner": source["source_owner"],
            "source_member": 307,
            "command_id": "actual-runnable-original-source-initialization",
            "budget_ms": 120000,
        }
        result["source_contains_original_software_node_history"] = True
        with closing(
            sqlite3.connect(Path(source_input["source_sql"]).as_uri() + "?mode=ro", uri=True)
        ) as sql:
            checkpoint = json.loads(
                sql.execute(
                    "SELECT checkpoint_json FROM controller_checkpoint WHERE singleton=1"
                ).fetchone()[0]
            )
        registry = json.loads(checkpoint["integration_json"])["control"].get(
            "node_identity_registry"
        )
        if registry is not None:
            deployed = output / "node-tls"
            deployed.mkdir(mode=0o700)
            for name in ("server.crt", "server.key", "ca.crt", "admin.crt", "admin.key"):
                shutil.copyfile(initialization_source / "pki" / name, deployed / name)
            save(deployed / "identity-profile.json", registry)
            node_tls = {
                "schema": "roboguide.controller-transport-tls-inputs/v0.1",
                "server_cert": str(deployed / "server.crt"),
                "server_key": str(deployed / "server.key"),
                "client_ca": str(deployed / "ca.crt"),
                "profile": str(deployed / "identity-profile.json"),
            }
            for plane in ("operator", "artifact"):
                shutil.copyfile(
                    initialization_source / f"{plane}-authorization.json",
                    deployed / f"{plane}-authorization.json",
                )
            operator_tls = dict(node_tls, profile=str(deployed / "operator-authorization.json"))
            artifact_tls = dict(node_tls, profile=str(deployed / "artifact-authorization.json"))
            transport_context = ssl.create_default_context(cafile=str(deployed / "ca.crt"))
            transport_context.load_cert_chain(deployed / "admin.crt", deployed / "admin.key")

    def request_http(
        origin: str,
        path: str,
        method: str = "GET",
        body: bytes = b"",
        *,
        timeout_seconds: float = 60,
    ) -> tuple[int, bytes]:
        """Verify actual listener TLS using only the original declared client credentials."""
        secured = any(
            origin == config[address] and config.get(plane) is not None
            for config in configs.values()
            for plane, address in (
                ("operator_tls", "http_address"),
                ("artifact_tls", "artifact_address"),
            )
        )
        return http(
            "https://" + origin if secured else origin,
            path,
            method,
            body,
            timeout_seconds=timeout_seconds,
            tls_context=transport_context if secured else None,
        )

    def request_recovery_http(
        origin: str, path: str, method: str, body: bytes
    ) -> tuple[int, bytes]:
        """Bound the original recovery plus response I/O on its actual authenticated listener."""
        return request_http(
            origin,
            path,
            method,
            body,
            timeout_seconds=315 if source_policy is not None else 135,
        )

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
                if source_policy is not None:
                    config["schema"] = "roboguide.controller-replica-process/v0.7"
                    config["content"]["child_budget_ms"] = 120000
                    config["content"]["initialization"] = source_policy
                    config["content"]["initialization_source"] = (
                        source_input if member == source_policy["source_member"] else None
                    )
                    config["node_tls"] = node_tls
                    config["operator_tls"] = operator_tls
                    config["artifact_tls"] = artifact_tls
                    if operator_tls is not None:
                        config["controller_environment"][
                            "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN"
                        ] = "0"
                if coordinated_archive:
                    config["coordinated_archive_base"] = (
                        "roboguide.controller-replica-process/v0.7"
                        if source_policy is not None
                        else "roboguide.controller-replica-process/v0.6"
                    )
                    config["schema"] = "roboguide.controller-replica-process/v0.9"
                    config["content"]["child_budget_ms"] = 120000
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
                            "staging_gc": source_policy is not None or coordinated_archive,
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
                        if record.get("planned_shutdown"):
                            if process.poll() != 0:
                                raise ValueError(
                                    "original planned source supervisor did not exit cleanly"
                                )
                            continue
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
                                code, _ = request_http(configs[member]["http_address"], "/healthz")
                                if code == 200:
                                    return member, status
                        except (OSError, ValueError, http_client.HTTPException):
                            pass
                    time.sleep(0.05)
                raise ValueError(
                    "actual runnable Controller did not become healthy within its bound"
                )

            def restart_original_supervisor(
                member: int, previous_pid: int
            ) -> tuple[int, dict[str, Any]]:
                """Recover explicitly after the original application failure latch."""
                process, record = next(
                    (p, r)
                    for p, r in reversed(owned)
                    if int(r["member"]) == member and not r.get("planned_shutdown")
                )
                os.killpg(process.pid, signal.SIGINT)
                record["returncode"] = process.wait(timeout=20)
                assert record["returncode"] == 0
                record["planned_shutdown"] = True
                record["explicit_supervisor_restart"] = True
                path = output / f"configuration-{member}.json"
                log = scope.enter_context(
                    (output / f"supervisor-{member}-restart-{len(owned)}.log").open("xb")
                )
                replacement = subprocess.Popen(
                    [str(supervisor), str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                    start_new_session=True,
                )
                owned.append(
                    (
                        replacement,
                        {
                            "pid": replacement.pid,
                            "member": member,
                            "configuration": str(path),
                            "binary": str(supervisor),
                        },
                    )
                )
                host, port = configs[member]["replica"]["members"][str(member)].rsplit(":", 1)
                listening = time.monotonic() + 20
                while True:
                    if replacement.poll() is not None:
                        raise ValueError("explicit original supervisor replacement exited")
                    try:
                        with socket.create_connection((host, int(port)), timeout=0.2):
                            break
                    except OSError:
                        if time.monotonic() >= listening:
                            raise ValueError(
                                "explicit original supervisor listener stayed unavailable"
                            ) from None
                        time.sleep(0.02)
                winner, status = current(previous_pid)
                if winner != member:
                    transfer = subprocess.run(
                        [
                            str(supervisor),
                            "--admin",
                            str(output / f"configuration-{winner}.json"),
                            "--leader",
                            str(winner),
                            "--transfer-leader",
                            str(member),
                            "--transfer-id",
                            f"actual-explicit-original-restart-{len(owned)}",
                        ],
                        capture_output=True,
                        timeout=45,
                        check=False,
                    )
                    assert transfer.returncode == 0, transfer.stderr
                    winner, status = current(previous_pid)
                assert winner == member
                observed(
                    "actual-explicit-supervisor-restart",
                    member=member,
                    original_supervisor_wait=record["returncode"],
                    replacement_supervisor_pid=replacement.pid,
                    actual_status=status,
                )
                return winner, status

            member, first = current()
            observed("original-controller-healthy", member=member, status=first)
            original_streams = (
                None
                if source_policy is None
                else source_streams(configs, source_policy["source_owner"])
            )
            if idle_timer_check:
                directory = Path(configs[member]["storage_directory"])
                before = idle_snapshot(directory)
                started = time.monotonic()
                time.sleep(5)
                after = idle_snapshot(directory)
                observed(
                    "actual-idle-timer",
                    before=before,
                    after=after,
                    actual_seconds=time.monotonic() - started,
                )
                assert before == after, (
                    "idle timer changed original revision/checkpoint without work"
                )
                result["actual_idle_timer"] = {"before": before, "after": after}
            origin = configs[member]["artifact_address"]
            payload = os.urandom(128 * 1024 + 29)
            checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
            upload = "actual-runnable-original-upload"
            admission_code, admission_raw = request_http(
                origin,
                "/v1/artifact-uploads",
                "POST",
                json.dumps({"upload_id": upload}).encode(),
            )
            observed(
                "original-upload-admission-response",
                status=admission_code,
                body=admission_raw.decode(errors="replace")[:4096],
            )
            assert admission_code == 201, admission_raw
            if original_streams is not None and source_policy is not None:
                after_streams = source_streams(configs, source_policy["source_owner"])
                reused = [
                    peer
                    for peer in configs
                    if peer != member and after_streams[peer] == original_streams[peer]
                ]
                assert reused, "fresh complete peer bytes were retransferred by every receiver"
                observed(
                    "actual-prior-byte-reuse",
                    actual_members=reused,
                    before_counts={str(peer): len(rows) for peer, rows in original_streams.items()},
                    after_counts={str(peer): len(rows) for peer, rows in after_streams.items()},
                )
            observed("original-upload-started", upload_id=upload)
            assert (
                request_http(origin, f"/v1/artifact-uploads/{upload}/content", "POST", payload)[0]
                == 202
            )
            observed("original-content-appended", upload_id=upload, actual_bytes=len(payload))
            code, raw = request_http(
                origin,
                f"/v1/artifact-uploads/{upload}/finalize",
                "POST",
                json.dumps({"content_digest": checksum, "byte_size": len(payload)}).encode(),
            )
            assert code == 201, raw
            completed = json.loads(raw)
            assert completed["receipt"]["phase"] == "completed"
            if idle_timer_check:
                assert idle_snapshot(directory)["journal_revision"] > before["journal_revision"]
            observed("original-upload-completed", receipt=completed)
            assert request_http(origin, "/v1/artifacts/" + checksum[7:]) == (200, payload)
            observed("original-http-content-read", actual_digest=checksum)
            originals: dict[int, Any] = {}
            for replica, config in configs.items():
                parent = Path(config["storage_directory"]) / "replica.sqlite3"
                with closing(sqlite3.connect(parent.as_uri() + "?mode=ro", uri=True)) as sql:
                    assert sql.execute("PRAGMA user_version").fetchone()[0] == (
                        11 if coordinated_archive else 9 if source_policy is not None else 8
                    )
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
            if coordinated_archive:
                from runnable_coordinated_archive import exercise

                result["actual_coordinated_archive"] = exercise(request_http, configs, member)
                observed(
                    "actual-online-original-archive", proof=result["actual_coordinated_archive"]
                )
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
            successor, restarted = restart_original_supervisor(member, old_pid)
            assert successor == member
            assert restarted["epoch"]["term"] >= first["epoch"]["term"]
            observed("actual-successor-healthy", member=successor, status=restarted)
            assert request_http(origin, "/v1/artifacts/" + checksum[7:]) == (200, payload)
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
            prepared_case = None
            if prepared_recovery:
                assert owner_failover, "Prepared recovery requires actual owner failover"
                prepared_case = stage_prepared(
                    request_http, origin, Path(configs[member]["storage_directory"])
                )
                prepared_key = {
                    "source_owner": source_writer["owner"],
                    "content_digest": prepared_case[0]["content_digest"],
                    "byte_size": prepared_case[0]["byte_size"],
                }
                for config in configs.values():
                    assert prepared_key in actual_bytes(Path(config["content"]["root"]))
                observed(
                    "actual-source-remains-prepared",
                    record=prepared_case[0],
                    independently_verified_member_bytes=sorted(configs),
                )
            if owner_failover:
                original_process, original_record = next(
                    (process, record)
                    for process, record in reversed(owned)
                    if record["member"] == member and not record.get("planned_shutdown")
                )
                os.killpg(original_process.pid, signal.SIGINT)
                original_record["returncode"] = original_process.wait(timeout=20)
                assert original_record["returncode"] == 0
                original_record["planned_shutdown"] = True
                next_member, next_status = current()
                assert (
                    next_member != member and next_status["epoch"]["term"] > first["epoch"]["term"]
                )
                next_origin = configs[next_member]["artifact_address"]
                next_cas = Path(configs[next_member]["storage_directory"]) / "artifacts"
                next_blob = next_cas / "objects-v1" / "sha256" / checksum[7:9] / checksum[7:]
                assert not next_blob.exists()
                store_code, store_raw = request_http(next_origin, "/v1/artifact-store")
                assert store_code == 200
                next_owner = json.loads(store_raw)["owner_id"]
                assert next_owner != source_writer["owner"]
                assert request_http(next_origin, "/v1/artifacts/" + checksum[7:]) == (200, payload)
                assert not next_blob.exists()
                with closing(
                    sqlite3.connect(
                        (Path(configs[next_member]["content"]["root"]) / "content.sqlite3").as_uri()
                        + "?mode=ro",
                        uri=True,
                    )
                ) as sql:
                    rows = sql.execute("SELECT record FROM content_readers LIMIT 4097").fetchall()
                assert len(rows) <= 4096
                original_reads = [
                    json.loads(row[0])
                    for row in rows
                    if json.loads(row[0])["intent"]["reader_id"].startswith("child-read:")
                ]
                assert len(original_reads) == 1
                original_read = original_reads[0]
                assert original_read["intent"]["content"]["source_owner"] == source_writer["owner"]
                assert original_read["receipt"]["complete"]
                assert original_read["receipt"]["observed_digest"] == checksum
                assert original_read["receipt"]["bytes_read"] == len(payload)
                observed(
                    "actual-cross-owner-http-read",
                    source_member=member,
                    next_member=next_member,
                    source_owner=source_writer["owner"],
                    actual_local_owner=next_owner,
                    status=next_status,
                    copied_to_local_cas=False,
                )
                result["actual_cross_owner_read"] = {
                    "source_member": member,
                    "target_member": next_member,
                    "source_owner": source_writer["owner"],
                    "target_local_owner": next_owner,
                    "target_status": next_status,
                    "copied_to_local_cas": False,
                    "actual_source_supervisor_wait": original_record["returncode"],
                    "actual_root_reader": original_read,
                }
                if prepared_case is not None:
                    result["actual_prepared_recovery"] = verify_prepared(
                        request_recovery_http,
                        next_origin,
                        Path(configs[next_member]["storage_directory"]),
                        prepared_case[0],
                        prepared_case[1],
                        source_writer["owner"],
                        next_owner,
                        budget_ms=300000 if source_policy is not None else 120000,
                    )
                    observed(
                        "actual-prepared-owner-recovery", proof=result["actual_prepared_recovery"]
                    )
                    if release_after_recovery:
                        failed_pid = next_status["child_pid"]
                        os.kill(failed_pid, signal.SIGKILL)
                        restored_member, restored_status = restart_original_supervisor(
                            next_member, failed_pid
                        )
                        assert restored_member == next_member
                        assert restored_status["epoch"]["term"] >= next_status["epoch"]["term"]
                        directory = Path(configs[next_member]["storage_directory"])
                        with closing(
                            sqlite3.connect(
                                (directory / "replica.sqlite3").as_uri() + "?mode=ro", uri=True
                            )
                        ) as sql:
                            processes = json.loads(
                                sql.execute(
                                    "SELECT value FROM replica_v1_meta "
                                    "WHERE key='controller_processes_v1'"
                                ).fetchone()[0]
                            )
                        reaped = [
                            row
                            for row in processes
                            if row["process"] and row["process"]["pid"] == failed_pid
                        ]
                        assert len(reaped) == 1 and reaped[0]["outcome"]["signal"] == 9
                        result["actual_prepared_release"] = release_prepared(
                            request_recovery_http,
                            next_origin,
                            directory,
                            prepared_case[0],
                            source_writer["owner"],
                            next_owner,
                            result["actual_prepared_recovery"]["unknown"],
                        )
                        for config in configs.values():
                            assert prepared_key in actual_bytes(Path(config["content"]["root"]))
                        observed(
                            "actual-cross-owner-prepared-release",
                            proof=result["actual_prepared_release"],
                            original_wait=reaped[0],
                            actual_restarted_controller=restored_status,
                            original_root_bytes_retained=True,
                        )
                        result["actual_completed_retirement"] = retire_completed(
                            request_recovery_http,
                            next_origin,
                            directory,
                            completed["receipt"],
                            source_writer["owner"],
                            next_owner,
                        )
                        completed_key = {
                            "source_owner": source_writer["owner"],
                            "content_digest": checksum,
                            "byte_size": len(payload),
                        }
                        for config in configs.values():
                            assert completed_key in actual_bytes(Path(config["content"]["root"]))
                        observed(
                            "actual-cross-owner-completed-retirement",
                            proof=result["actual_completed_retirement"],
                            original_root_bytes_retained=True,
                        )
            if archive_sql_fault:
                assert coordinated_archive and owner_failover
                from runnable_coordinated_archive import sql_failure_and_transfer

                result["actual_archive_sql_fault"] = sql_failure_and_transfer(
                    request_http,
                    configs,
                    current,
                    supervisor,
                    [
                        int(record["member"])
                        for _, record in owned
                        if not record.get("planned_shutdown")
                    ],
                )
                observed(
                    "actual-archive-sql-unknown-transfer", proof=result["actual_archive_sql_fault"]
                )
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
    parser.add_argument("--idle-timer-check", action="store_true")
    parser.add_argument("--owner-failover", action="store_true")
    parser.add_argument("--prepared-recovery", action="store_true")
    parser.add_argument("--release-after-recovery", action="store_true")
    parser.add_argument("--initialization-source", type=Path)
    parser.add_argument("--coordinated-archive", action="store_true")
    parser.add_argument("--archive-sql-fault", action="store_true")
    args = parser.parse_args()
    if args.release_after_recovery and not args.prepared_recovery:
        parser.error("--release-after-recovery requires --prepared-recovery")
    result = run(
        args.output,
        args.supervisor.resolve(strict=True),
        args.controller.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
        args.idle_timer_check,
        args.owner_failover,
        args.prepared_recovery,
        args.release_after_recovery,
        None
        if args.initialization_source is None
        else args.initialization_source.resolve(strict=True),
        args.coordinated_archive,
        args.archive_sql_fault,
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
