"""Verify actual three-replica same-member leaf rotation with zero Nodes or devices."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sqlite3
import ssl
import struct
import subprocess
import time
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, cast

from check_controller_membership_processes import free_address, save, wait_for
from check_identity_processes import openssl

ROOT = Path(__file__).resolve().parents[2]


def create_ca(directory: Path) -> None:
    """Generate a private actual CA in a new directory, without installing system trust."""
    directory.mkdir(mode=0o700)
    openssl(
        directory,
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-days",
        "1",
        "-subj",
        "/CN=replica-process-ca-" + directory.name,
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    (directory / "leaf.ext").write_text(
        "subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth,clientAuth\n"
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
    )


def issue(directory: Path, identity: str) -> tuple[Path, str]:
    """Generate an actual new private key and CA-signed leaf, including after startup."""
    openssl(
        directory,
        "req",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-keyout",
        identity + ".key",
        "-out",
        identity + ".csr",
        "-subj",
        "/CN=localhost",
    )
    openssl(
        directory,
        "x509",
        "-req",
        "-in",
        identity + ".csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-out",
        identity + ".crt",
        "-days",
        "1",
        "-extfile",
        "leaf.ext",
    )
    certificate = directory / (identity + ".crt")
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
    bundle = directory / (identity + ".pem")
    bundle.write_bytes(certificate.read_bytes() + (directory / (identity + ".key")).read_bytes())
    bundle.chmod(0o600)
    return bundle, "sha256:" + hashlib.sha256(der).hexdigest()


def replace_bundle(source: Path, target: Path) -> None:
    """Atomically replace one private credential inode without mismatching certificate/key files."""
    temporary = target.with_suffix(".pending")
    with temporary.open("xb") as output:
        output.write(source.read_bytes())
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    temporary.replace(target)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_trust(storage: Path) -> dict[str, Any]:
    """Read the original real WAL-aware replica authority without opening a second writer."""
    with sqlite3.connect(
        storage.joinpath("replica.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as database:
        row = database.execute(
            "SELECT value FROM replica_meta WHERE key='certificate_trust'"
        ).fetchone()
    if row is None:
        raise ValueError("original trust authority is absent")
    return cast(dict[str, Any], json.loads(row[0]))


def application_identity(storage: Path) -> list[tuple[str, str]]:
    """Observe complete application checkpoint bytes independently of normal timer revisions."""
    with sqlite3.connect(
        storage.joinpath("replica.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as database:
        return database.execute(
            "SELECT key,value FROM replica_meta "
            "WHERE key IN ('checkpoint','checkpoint_digest') ORDER BY key"
        ).fetchall()


def exact_read(stream: ssl.SSLSocket, size: int) -> bytes:
    """Read a bounded complete actual TLS frame, rejecting EOF instead of inventing a response."""
    chunks = bytearray()
    while len(chunks) < size:
        block = stream.recv(size - len(chunks))
        if not block:
            raise ValueError("replica closed the actual TLS connection before its response")
        chunks.extend(block)
    return bytes(chunks)


def old_leaf_request(
    output: Path, member: int, leader: int, members: dict[str, str], old_bundle: Path
) -> dict[str, Any]:
    """Send the retired real private key over actual mTLS, bypassing only client-side refusal."""
    with sqlite3.connect(
        (output / f"replica-{member}/replica.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as database:
        identity = json.loads(
            database.execute("SELECT value FROM replica_meta WHERE key='identity'").fetchone()[0]
        )
    context = ssl.create_default_context(cafile=str(output / f"pki/roots-member-{member}.pem"))
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(old_bundle))
    host, port = members[str(leader)].rsplit(":", 1)
    envelope = json.dumps(
        {
            "profile": identity[0],
            "target": leader,
            "sender": member,
            "auth_digest": None,
            "command": "AdminEpoch",
        },
        separators=(",", ":"),
    ).encode()
    try:
        with socket.create_connection((host, int(port)), timeout=4) as connection:
            with context.wrap_socket(connection, server_hostname="localhost") as stream:
                actual = stream.getpeercert(binary_form=True)
                if not isinstance(actual, bytes):
                    raise ValueError("actual TLS server leaf is absent")
                trust = read_trust(output / f"replica-{leader}")
                assert (
                    "sha256:" + hashlib.sha256(actual).hexdigest()
                    == trust["members"][str(leader)]["current"]
                )
                stream.sendall(struct.pack(">Q", len(envelope)) + envelope)
                size = struct.unpack(">Q", exact_read(stream, 8))[0]
                if not 0 < size <= 4096:
                    raise ValueError("retired-key reply exceeds explicit bound")
                return cast(dict[str, Any], json.loads(exact_read(stream, size)))
    except ssl.SSLError as error:
        if error.reason not in {
            "TLSV1_ALERT_UNKNOWN_CA",
            "SSLV3_ALERT_BAD_CERTIFICATE",
            "SSLV3_ALERT_CERTIFICATE_UNKNOWN",
        }:
            raise
        return {"TLSRejected": str(error)}


def run(
    output: Path,
    skip_build: bool,
    old_binary: Path | None,
    async_workers: int | None = None,
    artifact_retained: bool = False,
) -> dict[str, Any]:
    """Exercise new-key proof, missing peers, irreversible revoke, leader rotation and restart."""
    if async_workers is not None and not 1 <= async_workers <= 64:
        raise ValueError("async workers must be an explicit integer in 1..64")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "controller-replica", "-p", "integration-server"],
            cwd=ROOT,
            check=True,
        )
    replica_binary = ROOT / "target/debug/controller-replica"
    controller_binary = ROOT / "target/debug/integration-server"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    processes: dict[int, subprocess.Popen[bytes]] = {}
    owned: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    shutdown_codes: list[int] = []
    configurations: dict[int, Path] = {}
    members = {str(member): free_address() for member in range(1, 4)}
    bundles: dict[int, Path] = {}
    roots: dict[int, Path] = {}
    pins: dict[str, str] = {}
    result: dict[str, Any] = {
        "schema": "roboguide.replica-certificate-process-check/v0.1",
        "passed": False,
        "software_replicas": 3,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "controller_async_workers": async_workers,
    }

    def status(member: int) -> dict[str, Any]:
        """Read one supervisor's actual atomically published status."""
        return cast(
            dict[str, Any], json.loads((output / f"replica-{member}/status.json").read_text())
        )

    def start(member: int, label: str) -> None:
        """Start only an owned supervisor process group using its original store and config."""
        log = (output / f"member-{member}-{label}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [str(replica_binary), str(configurations[member])],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        processes[member] = process
        owned.append(process)

    def stop(member: int) -> None:
        """Gracefully stop and reap this check's exact owned process group."""
        process = processes.pop(member)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        shutdown_codes.append(process.wait(timeout=0))

    def leader() -> int | None:
        """Observe an actual current owned child and confirm its public health endpoint."""
        import urllib.request

        for member, process in processes.items():
            if process.poll() is not None:
                continue
            try:
                current = status(member)
                if current.get("epoch") is not None and current.get("child_pid") is not None:
                    config = json.loads(configurations[member].read_text())
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(
                        "http://" + config["http_address"] + "/healthz", timeout=2
                    ) as response:  # noqa: S310
                        if response.status == 200:
                            return member
            except (OSError, ValueError):
                continue
        return None

    def admin(caller: int, current_leader: int, *arguments: str, accepted: bool = True) -> Any:
        """Issue a single actual authenticated command; mutating failures are never auto-retried."""
        completed = subprocess.run(
            [
                str(replica_binary),
                "--admin",
                str(configurations[caller]),
                "--leader",
                str(current_leader),
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if accepted:
            if completed.returncode != 0:
                raise ValueError("actual administration failed: " + completed.stderr[-2048:])
            return json.loads(completed.stdout)
        if completed.returncode == 0:
            raise ValueError("negative rotation unexpectedly succeeded")
        return {"returncode": completed.returncode, "stderr": completed.stderr[-2048:]}

    def rotate(
        caller: int, current_leader: int, operation: dict[str, Any], accepted: bool = True
    ) -> Any:
        """Retain the exact immutable command file before invoking its actual RPC once."""
        path = output / (str(operation["command_id"]) + ".json")
        save(path, operation)
        if accepted:
            wait_for(
                lambda: admin(caller, current_leader, "--certificate-trust"),
                "actual readonly current quorum before the one original mutation",
            )
        return admin(caller, current_leader, "--rotate-certificate", str(path), accepted=accepted)

    def all_at(revision: int) -> bool:
        """Require all three original stores to expose identical committed trust evidence."""
        views = [read_trust(output / f"replica-{member}") for member in processes]
        return len(views) == 3 and all(
            view["revision"] == revision and view == views[0] for view in views
        )

    try:
        pki = output / "pki"
        create_ca(pki)
        for member in range(1, 4):
            original, pins[str(member)] = issue(pki, f"member-{member}-original")
            bundles[member] = pki / f"member-{member}-current.pem"
            replace_bundle(original, bundles[member])
            roots[member] = pki / f"roots-member-{member}.pem"
            replace_bundle(pki / "ca.crt", roots[member])
        schema = subprocess.check_output(
            [str(replica_binary), "--application-schema"], text=True, timeout=5, env=environment
        ).strip()
        for member in range(1, 4):
            config: dict[str, Any] = {
                "schema": "roboguide.controller-replica-process/v0.1",
                "replica": {
                    "schema": "roboguide.controller-replica/v0.3",
                    "cluster_id": "actual-leaf-rotation-" + output.name,
                    "replica_id": member,
                    "members": members,
                    "initial_voters": [1, 2, 3],
                    "application_schema": schema,
                    "application_policy_digest": "pending",
                    "tls": {
                        "ca_cert_path": str(roots[member]),
                        "cert_path": str(bundles[member]),
                        "key_path": str(bundles[member]),
                        "server_name": "localhost",
                        "peer_certificate_digests": pins,
                    },
                },
                "storage_directory": str(output / f"replica-{member}"),
                "controller_binary": str(controller_binary),
                "grpc_address": free_address(),
                "http_address": free_address(),
                "artifact_address": free_address(),
                "controller_environment": {"ROBOGUIDE_CHECKPOINT_STORAGE": "full"},
            }
            path = output / f"member-{member}.json"
            if async_workers is not None:
                config["controller_environment"]["TOKIO_WORKER_THREADS"] = str(async_workers)
            if artifact_retained:
                config["controller_environment"]["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = (
                    "roboguide.artifact-storage/v1"
                )
            save(path, config)
            config["replica"]["application_policy_digest"] = subprocess.check_output(
                [str(replica_binary), "--policy-digest", str(path)],
                text=True,
                timeout=10,
                env=environment,
            ).strip()
            save(path, config)
            configurations[member] = path
            start(member, "first")
        current_leader = int(wait_for(leader, "initial real quorum child"))
        wait_for(lambda: all_at(0), "initial exact committed trust")
        target = next(member for member in range(1, 4) if member != current_leader)
        peer = next(member for member in range(1, 4) if member not in (current_leader, target))
        original_application = application_identity(output / f"replica-{current_leader}")
        save(output / "original-application-checkpoint.json", original_application)
        result["initial_leader"] = current_leader
        result["rotated_member"] = target
        next_pki = output / "pki-next"
        create_ca(next_pki)
        overlapping_roots = output / "overlapping-roots.pem"
        overlapping_roots.write_bytes(
            (pki / "ca.crt").read_bytes() + (next_pki / "ca.crt").read_bytes()
        )
        for member in (current_leader, target):
            replace_bundle(overlapping_roots, roots[member])
        replacement, new_leaf = issue(next_pki, f"member-{target}-issued-after-start")
        stage = {
            "command_id": "stage-first",
            "expected_revision": 0,
            "member": target,
            "action": {"kind": "stage", "previous_leaf": pins[str(target)], "new_leaf": new_leaf},
        }
        stage_receipt = rotate(target, current_leader, stage)
        wait_for(lambda: all_at(1), "stage on every original replica")
        retire = {
            "command_id": "retire-first",
            "expected_revision": 1,
            "member": target,
            "action": {
                "kind": "retire",
                "stage_command_id": "stage-first",
                "previous_leaf": pins[str(target)],
                "new_leaf": new_leaf,
            },
        }
        result["old_target_key_rejected_for_retirement"] = rotate(
            current_leader, current_leader, retire, accepted=False
        )
        assert all_at(1)
        replace_bundle(replacement, bundles[target])
        stop(peer)
        result["missing_peer_rejected_for_retirement"] = rotate(
            current_leader, current_leader, retire, accepted=False
        )
        assert read_trust(output / f"replica-{current_leader}")["revision"] == 1
        start(peer, "catchup")
        wait_for(lambda: all_at(1), "original missing peer catchup")
        current_leader = int(wait_for(leader, "current quorum after peer restart"))
        result["missing_new_ca_rejected_for_retirement"] = rotate(
            current_leader, current_leader, retire, accepted=False
        )
        assert all_at(1)
        replace_bundle(overlapping_roots, roots[peer])
        first_receipt = rotate(current_leader, current_leader, retire)
        wait_for(lambda: all_at(2), "irreversible retirement on every replica")
        assert len(first_receipt["probes"]) == 3
        assert rotate(current_leader, current_leader, retire) == first_receipt
        rejected = old_leaf_request(
            output, target, current_leader, members, pki / f"member-{target}-original.pem"
        )
        assert "Rejected" in rejected
        result["actual_retired_tls_leaf_rejected"] = rejected
        current_application = application_identity(output / f"replica-{current_leader}")
        save(output / "after-first-rotation-application-checkpoint.json", current_application)
        assert current_application == original_application
        result["first_complete_application_checkpoint_unchanged"] = True
        original_source_child = status(current_leader)["child_pid"]
        admin(
            current_leader,
            current_leader,
            "--transfer-leader",
            str(target),
            "--transfer-id",
            "rotate-leader-handoff",
        )
        wait_for(lambda: leader() == target, "rotated member becomes actual Controller leader")
        original_transfer = admin(
            target, target, "--observe-leader-transfer", "rotate-leader-handoff"
        )
        source_drain = original_transfer["source_drain"]
        assert source_drain["kind"] == "process-exit"
        original_wait = source_drain["outcome"]
        assert original_wait["process"]["pid"] == original_source_child
        assert original_wait["process"]["epoch"] == original_transfer["source_epoch"]
        assert original_wait["exit_code"] is not None or original_wait["signal"] is not None
        connection = sqlite3.connect(
            (output / f"replica-{current_leader}/replica.sqlite3").as_uri() + "?mode=ro", uri=True
        )
        try:
            original_processes = json.loads(
                connection.execute(
                    "SELECT value FROM replica_v1_meta WHERE key='controller_processes_v1'"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        assert any(record["outcome"] == original_wait for record in original_processes)
        assert not Path(f"/proc/{original_source_child}").exists()
        result["original_source_child_wait_before_target_admission"] = original_wait
        before = status(target)
        replacement_again, final_leaf = issue(next_pki, f"member-{target}-issued-as-leader")
        second_stage = {
            "command_id": "stage-leader",
            "expected_revision": 2,
            "member": target,
            "action": {"kind": "stage", "previous_leaf": new_leaf, "new_leaf": final_leaf},
        }
        rotate(current_leader, target, second_stage)
        wait_for(lambda: all_at(3), "leader stage on all original stores")
        replace_bundle(replacement_again, bundles[target])
        second_retire = {
            "command_id": "retire-leader",
            "expected_revision": 3,
            "member": target,
            "action": {
                "kind": "retire",
                "stage_command_id": "stage-leader",
                "previous_leaf": new_leaf,
                "new_leaf": final_leaf,
            },
        }
        second_receipt = rotate(current_leader, target, second_retire)
        wait_for(lambda: all_at(4), "leader original leaf retirement")
        wait_for(lambda: leader() == target, "same live Controller under new member credentials")
        after = status(target)
        assert before["epoch"] == after["epoch"] and before["child_pid"] == after["child_pid"]
        observed_at = time.monotonic()
        while time.monotonic() - observed_at < 5:
            current = status(target)
            assert (
                current["epoch"] == before["epoch"] and current["child_pid"] == before["child_pid"]
            )
            assert leader() == target
            time.sleep(0.1)
        result["leader_child_retained_original_epoch_and_pid"] = True
        result["leader_child_observed_seconds"] = time.monotonic() - observed_at
        rejected_previous = old_leaf_request(output, target, target, members, replacement)
        assert "Rejected" in rejected_previous
        result["actual_previous_leader_tls_leaf_rejected"] = rejected_previous
        unused_bundle, unused_leaf = issue(next_pki, "unused-key-to-abort")
        unused_stage = {
            "command_id": "stage-unused-key",
            "expected_revision": 4,
            "member": peer,
            "action": {"kind": "stage", "previous_leaf": pins[str(peer)], "new_leaf": unused_leaf},
        }
        rotate(current_leader, target, unused_stage)
        wait_for(lambda: all_at(5), "original unused credential stage")
        unused_bundle.unlink()
        (next_pki / "unused-key-to-abort.key").unlink()
        abort = {
            "command_id": "abort-lost-key",
            "expected_revision": 5,
            "member": peer,
            "action": {
                "kind": "abort",
                "stage_command_id": "stage-unused-key",
                "previous_leaf": pins[str(peer)],
                "new_leaf": unused_leaf,
            },
        }
        abort_receipt = rotate(current_leader, target, abort)
        wait_for(lambda: all_at(6), "explicit lost-key abort on every original store")
        assert (
            read_trust(output / f"replica-{peer}")["members"][str(peer)]["current"]
            == pins[str(peer)]
        )
        reused = {
            "command_id": "reuse-aborted-key",
            "expected_revision": 6,
            "member": peer,
            "action": {"kind": "stage", "previous_leaf": pins[str(peer)], "new_leaf": unused_leaf},
        }
        result["aborted_leaf_cannot_be_reused"] = rotate(
            current_leader, target, reused, accepted=False
        )
        result["lost_key_abort_receipt"] = abort_receipt
        final_revision = 6
        root_receipts = []
        for member in range(1, 4):
            if member == target:
                continue
            new_bundle, root_leaf = issue(next_pki, f"root-migration-member-{member}")
            root_stage = {
                "command_id": f"root-stage-{member}",
                "expected_revision": final_revision,
                "member": member,
                "action": {
                    "kind": "stage",
                    "previous_leaf": pins[str(member)],
                    "new_leaf": root_leaf,
                },
            }
            root_receipts.append(rotate(current_leader, target, root_stage))
            final_revision += 1
            wait_for(partial(all_at, final_revision), "new-root member original stage")
            replace_bundle(new_bundle, bundles[member])
            root_retire = {
                "command_id": f"root-retire-{member}",
                "expected_revision": final_revision,
                "member": member,
                "action": {
                    "kind": "retire",
                    "stage_command_id": root_stage["command_id"],
                    "previous_leaf": pins[str(member)],
                    "new_leaf": root_leaf,
                },
            }
            root_receipts.append(rotate(current_leader, target, root_retire))
            final_revision += 1
            wait_for(partial(all_at, final_revision), "new-root member original leaf retirement")
        for member in range(1, 4):
            replace_bundle(next_pki / "ca.crt", roots[member])
        root_der = ssl.PEM_cert_to_DER_cert((next_pki / "ca.crt").read_text())
        root_digest = (
            "sha256:" + hashlib.sha256(struct.pack(">Q", len(root_der)) + root_der).hexdigest()
        )
        root_observations = [
            admin(current_leader, member, "--certificate-observation") for member in range(1, 4)
        ]
        assert all(
            observation["ca_bundle_digest"] == root_digest
            and observation["revision"] == final_revision
            for observation in root_observations
        )
        result["old_ca_removed_from_every_actual_member"] = root_observations
        result["new_ca_member_receipts"] = root_receipts
        rejected_old_ca = old_leaf_request(
            output, target, target, members, pki / f"member-{target}-original.pem"
        )
        assert "TLSRejected" in rejected_old_ca
        result["actual_retired_ca_rejected"] = rejected_old_ca
        original_receipts = read_trust(output / f"replica-{target}")["receipts"]
        for member in list(processes):
            stop(member)
        if old_binary is not None:
            before_old = read_trust(output / f"replica-{target}")
            completed = subprocess.run(
                [str(old_binary), str(configurations[target])],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert completed.returncode != 0
            assert read_trust(output / f"replica-{target}") == before_old
            result["actual_old_binary_rejected"] = {
                "sha256": hashlib.sha256(old_binary.read_bytes()).hexdigest(),
                "stderr": completed.stderr[-1024:],
            }
        for member in range(1, 4):
            start(member, "restored")
        restored_leader = int(wait_for(leader, "full original cluster restart"))
        wait_for(lambda: all_at(final_revision), "original current trust after restart")
        restored = admin(current_leader, restored_leader, "--certificate-trust")
        assert restored["receipts"] == original_receipts
        assert rotate(target, restored_leader, stage) == stage_receipt
        assert rotate(current_leader, restored_leader, retire) == first_receipt
        assert rotate(current_leader, restored_leader, second_retire) == second_receipt
        assert rotate(current_leader, restored_leader, abort) == abort_receipt
        result.update(
            passed=True,
            original_stage_receipt=stage_receipt,
            first_retire_receipt=first_receipt,
            leader_retire_receipt=second_receipt,
            restored_trust=restored,
            controller_binary_sha256=hashlib.sha256(controller_binary.read_bytes()).hexdigest(),
            replica_binary_sha256=hashlib.sha256(replica_binary.read_bytes()).hexdigest(),
        )
        return result
    finally:
        for member in list(processes):
            stop(member)
        for log in logs:
            log.close()
        result["owned_processes_reaped"] = all(process.poll() is not None for process in owned)
        result["owned_supervisor_exit_codes"] = shutdown_codes
        result["owned_supervisors_stopped_gracefully"] = all(code == 0 for code in shutdown_codes)
        result["passed"] = bool(result["passed"]) and result["owned_supervisors_stopped_gracefully"]
        save(output / "result.json", result)


def main() -> None:
    """Require an explicit new evidence directory and optional actual old binary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--old-binary", type=Path)
    parser.add_argument("--async-workers", type=int, choices=range(1, 65))
    parser.add_argument("--artifact-retained", action="store_true")
    arguments = parser.parse_args()
    result = run(
        arguments.output.resolve(),
        arguments.skip_build,
        arguments.old_binary,
        arguments.async_workers,
        arguments.artifact_retained,
    )
    print(
        json.dumps(
            {key: result[key] for key in ("passed", "nodes", "devices", "owned_processes_reaped")}
        )
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
