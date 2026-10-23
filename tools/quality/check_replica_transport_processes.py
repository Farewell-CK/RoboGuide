"""Check actual single-worker replica TCP/TLS capacities, with no Controller or embodied Nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

from check_controller_membership_processes import free_address, save, wait_for
from check_replica_certificate_processes import create_ca, exact_read, issue

ROOT = Path(__file__).resolve().parents[2]
LIMITS = {
    "inbound_connections": 16,
    "outbound_connections": 8,
    "small_frame_bytes": 1024 * 1024,
    "large_frame_bytes": 8 * 1024 * 1024,
}


class ActualReplicas:
    """Own only the actual infrastructure children and sockets created by this invocation."""

    def __init__(self, output: Path, binary: Path) -> None:
        """Generate real CA/leaf identities and declare isolated infrastructure members."""
        self.output = output
        self.binary = binary
        self.cleanup = ExitStack()
        self.processes: dict[int, subprocess.Popen[bytes]] = {}
        self.waits: list[dict[str, Any]] = []
        self.members = {str(member): free_address() for member in range(1, 4)}
        self.ca = output / "tls"
        create_ca(self.ca)
        identities = {member: issue(self.ca, f"member-{member}") for member in range(1, 4)}
        self.pins = {str(member): digest for member, (_, digest) in identities.items()}
        self.contexts: dict[int, ssl.SSLContext] = {}
        for member, (bundle, _) in identities.items():
            context = ssl.create_default_context(cafile=str(self.ca / "ca.crt"))
            context.load_cert_chain(bundle)
            self.contexts[member] = context
            configuration = {
                "schema": "roboguide.controller-replica/v0.2",
                "cluster_id": "actual-transport-capacity",
                "replica_id": member,
                "members": self.members,
                "initial_voters": [1, 2, 3],
                "application_schema": "transport-only-no-controller",
                "application_policy_digest": "no-task-or-device-authority",
                "tls": {
                    "ca_cert_path": str(self.ca / "ca.crt"),
                    "cert_path": str(bundle),
                    "key_path": str(bundle),
                    "server_name": "localhost",
                    "peer_certificate_digests": self.pins,
                },
            }
            save(output / f"member-{member}.json", configuration)
            save(
                output / f"controls-{member}.json",
                {
                    "limits": LIMITS,
                    "stop_listener_path": str(output / f"stop-listener-{member}"),
                },
            )

    def start(self, member: int, *, bounded: bool = True) -> None:
        """Start the real library listener without supervisor or application children."""
        ordinal = len(self.waits)
        log = self.cleanup.enter_context(
            (self.output / f"member-{member}-{ordinal}.log").open("xb")
        )
        observation = self.output / f"observation-{member}.json"
        observation.unlink(missing_ok=True)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        }
        command = [
            str(self.binary),
            str(self.output / f"member-{member}.json"),
            str(self.output / f"member-{member}.sqlite3"),
            str(observation),
        ]
        if bounded:
            command.append(str(self.output / f"controls-{member}.json"))
        self.processes[member] = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=environment,
        )
        wait_for(lambda: self.observation(member), f"actual member {member} startup")

    def stop(self, member: int) -> None:
        """Terminate and actually wait only the original owned infrastructure process."""
        process = self.processes.pop(member)
        if process.poll() is None:
            process.terminate()
        code = process.wait(timeout=10)
        self.waits.append({"member": member, "pid": process.pid, "returncode": code})

    def close(self) -> None:
        """Reap every actual owned child before releasing log descriptors."""
        try:
            for member in tuple(self.processes):
                self.stop(member)
        finally:
            self.cleanup.close()

    def observation(self, member: int) -> dict[str, Any]:
        """Read an original atomic status and verify the actual process has not exited."""
        process = self.processes[member]
        if process.poll() is not None:
            raise ValueError(f"actual member {member} exited: {process.returncode}")
        value = cast(
            dict[str, Any], json.loads((self.output / f"observation-{member}.json").read_text())
        )
        assert value["pid"] == process.pid
        return value

    def budget(self, member: int) -> dict[str, Any]:
        """Check each original actual semaphore observation against its declared bound."""
        result = cast(dict[str, Any], self.observation(member)["transport"])
        assert result["limits"] == LIMITS
        for active, limit in [
            ("inbound_active", "inbound_connections"),
            ("outbound_active", "outbound_connections"),
            ("small_frame_active_bytes", "small_frame_bytes"),
            ("large_frame_active_bytes", "large_frame_bytes"),
        ]:
            assert 0 <= result[active] <= LIMITS[limit], result
        return result

    def connect(self, member: int, *, tls: bool = True, sender: int = 2) -> socket.socket:
        """Open an actual owned socket, checking the actual served leaf before sending any frame."""
        host, port = self.members[str(member)].split(":")
        stream = socket.create_connection((host, int(port)), timeout=3)
        if not tls:
            return stream
        try:
            secured = self.contexts[sender].wrap_socket(stream, server_hostname="localhost")
            leaf = secured.getpeercert(binary_form=True)
            assert isinstance(leaf, bytes)
            assert "sha256:" + hashlib.sha256(leaf).hexdigest() == self.pins[str(member)]
            return secured
        except BaseException:
            stream.close()
            raise

    def exchange(
        self,
        member: int,
        command: Any,
        *,
        sender: int = 2,
        claimed_sender: int | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Send one authenticated real RPC without repeating any mutation on failure."""
        envelope = json.dumps(
            {
                "profile": profile or self.observation(member)["profile"],
                "target": member,
                "sender": sender if claimed_sender is None else claimed_sender,
                "auth_digest": None,
                "command": command,
            },
            separators=(",", ":"),
        ).encode()
        with self.connect(member, sender=sender) as connection:
            assert isinstance(connection, ssl.SSLSocket)
            connection.sendall(struct.pack(">Q", len(envelope)) + envelope)
            length = struct.unpack(">Q", exact_read(connection, 8))[0]
            assert 0 < length <= 1024 * 1024
            return cast(dict[str, Any], json.loads(exact_read(connection, length)))

    def leader(self) -> tuple[int, dict[str, Any]]:
        """Require an actual quorum fence after observing the current leader."""
        for member in self.processes:
            epoch = self.exchange(member, "AdminEpoch").get("Epoch")
            if epoch is not None:
                if self.exchange(member, {"Fence": epoch}) == {"Fence": {"Ok": None}}:
                    return member, cast(dict[str, Any], epoch)
        raise ValueError("no current actual quorum leader")


def require_closed(stream: socket.socket) -> bool:
    """Accept only actual EOF/reset, never a timeout or a synthetic close acknowledgement."""
    try:
        return stream.recv(1) == b""
    except (ConnectionResetError, BrokenPipeError, ssl.SSLEOFError):
        return True


def connection_pressure(replicas: ActualReplicas, member: int) -> dict[str, Any]:
    """Exceed pre-TLS capacity with actual incomplete handshakes and verify later release."""
    with ExitStack() as sockets:
        for _ in range(48):
            sockets.enter_context(replicas.connect(member, tls=False))
        wait_for(
            lambda: replicas.budget(member)["inbound_rejected_total"] >= 32,
            "actual pre-TLS capacity rejections",
        )
        result = replicas.budget(member)
        assert result["inbound_active"] == LIMITS["inbound_connections"]
        assert result["inbound_high_water"] == LIMITS["inbound_connections"]
        save(replicas.output / "pre-tls-saturated.json", result)
    wait_for(lambda: replicas.budget(member)["inbound_active"] == 0, "actual EOF permit release")
    return result


def frame_pressure(replicas: ActualReplicas) -> dict[str, Any]:
    """Hold incomplete large frames while small authenticated quorum checks complete."""
    member, epoch = wait_for(replicas.leader, "quorum after connection pressure")
    with ExitStack() as sockets:
        for _ in range(2):
            stream = sockets.enter_context(replicas.connect(member))
            stream.sendall(struct.pack(">Q", 4 * 1024 * 1024))
        wait_for(
            lambda: (
                replicas.budget(member)["large_frame_active_bytes"] == LIMITS["large_frame_bytes"]
            ),
            "actual large frame reservations",
        )
        previous = replicas.budget(member)["frame_rejected_total"]
        with replicas.connect(member) as refused:
            refused.sendall(struct.pack(">Q", 1024 * 1024))
            assert require_closed(refused)
        wait_for(
            lambda: replicas.budget(member)["frame_rejected_total"] > previous,
            "actual rejected-frame counter publication",
        )
        latencies = []
        for _ in range(5):
            started = time.monotonic()
            assert replicas.exchange(member, {"Fence": epoch}) == {"Fence": {"Ok": None}}
            latencies.append(time.monotonic() - started)
        result = replicas.budget(member)
        assert result["frame_rejected_total"] > previous
        assert result["large_frame_active_bytes"] == LIMITS["large_frame_bytes"]
        result["actual_small_quorum_read_seconds"] = latencies
        save(replicas.output / "large-frame-saturated.json", result)
    wait_for(
        lambda: replicas.budget(member)["large_frame_active_bytes"] == 0, "large frame EOF release"
    )
    return result


def framing_and_identity(replicas: ActualReplicas, member: int) -> dict[str, Any]:
    """Check real frame limits, invalid JSON and original member/profile authorization."""
    before = replicas.budget(member)["frame_rejected_total"]
    for length in (0, 256 * 1024 * 1024 + 1):
        with replicas.connect(member) as stream:
            stream.sendall(struct.pack(">Q", length))
            assert require_closed(stream)
    with replicas.connect(member) as stream:
        stream.sendall(struct.pack(">Q", 1) + b"{")
        assert require_closed(stream)
    with replicas.connect(member) as stream:
        stream.sendall(struct.pack(">Q", 32 * 1024))
        wait_for(
            lambda: replicas.budget(member)["small_frame_active_bytes"] >= 32 * 1024,
            "small read reservation",
        )
    wait_for(lambda: replicas.budget(member)["small_frame_active_bytes"] == 0, "small EOF release")
    assert "Rejected" in replicas.exchange(member, "AdminEpoch", profile="incorrect-profile")
    assert "Rejected" in replicas.exchange(member, "AdminEpoch", claimed_sender=3)
    wait_for(
        lambda: replicas.budget(member)["frame_rejected_total"] >= before + 2,
        "actual malformed-frame counter publication",
    )
    result = replicas.budget(member)
    assert result["frame_rejected_total"] >= before + 2
    save(replicas.output / "framing-and-identity.json", result)
    return result


def timeout_and_drop(replicas: ActualReplicas, member: int) -> dict[str, Any]:
    """Wait the original TLS deadline, then drop the listener and its pending children."""
    before = replicas.budget(member)["inbound_timeout_total"]
    with replicas.connect(member, tls=False) as stream:
        wait_for(
            lambda: replicas.budget(member)["inbound_timeout_total"] > before,
            "actual original fifteen-second TLS timeout",
            seconds=20,
        )
        assert require_closed(stream)
    with ExitStack() as sockets:
        for _ in range(2):
            stream = sockets.enter_context(replicas.connect(member))
            stream.sendall(struct.pack(">Q", 1024 * 1024))
        wait_for(
            lambda: replicas.budget(member)["large_frame_active_bytes"] == 2 * 1024 * 1024,
            "pending owned frames",
        )
        sockets.enter_context(replicas.connect(member, tls=False))
        (replicas.output / f"stop-listener-{member}").write_text(
            "explicit owned listener shutdown\n"
        )
        wait_for(
            lambda: (
                not replicas.observation(member)["listener_owned"]
                and replicas.budget(member)["inbound_active"] == 0
                and replicas.budget(member)["large_frame_active_bytes"] == 0
            ),
            "owned listener and handler teardown",
        )
        result = replicas.observation(member)
        assert replicas.processes[member].poll() is None
        save(replicas.output / "listener-dropped-process-still-running.json", result)
    replicas.stop(member)
    (replicas.output / f"stop-listener-{member}").unlink()
    replicas.start(member)
    wait_for(replicas.leader, "actual original-store restart quorum")
    return result


def legacy_baseline(output: Path, binary: Path) -> dict[str, Any]:
    """Observe actual historical pre-TLS socket growth with its exact separately frozen binary."""
    output.mkdir()
    replicas = ActualReplicas(output, binary)
    result: dict[str, Any] = {"passed": False}
    try:
        for member in range(1, 4):
            replicas.start(member, bounded=False)
        wait_for(replicas.leader, "historical actual mTLS quorum")
        descriptors = Path(f"/proc/{replicas.processes[1].pid}/fd")
        original = len(list(descriptors.iterdir()))
        with ExitStack() as sockets:
            for _ in range(192):
                sockets.enter_context(replicas.connect(1, tls=False))
            actual = wait_for(
                lambda: (
                    count if (count := len(list(descriptors.iterdir()))) >= original + 180 else None
                ),
                "historical actual simultaneous pre-TLS sockets",
                seconds=5,
            )
            result.update(
                passed=True,
                original_fd_count=original,
                saturated_fd_count=actual,
                actual_client_sockets=192,
                binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                criterion="180+ actual additional FDs; transient Raft sockets may turn over",
                nodes=0,
                devices=0,
                controller_children=0,
            )
    finally:
        replicas.close()
        result["actual_process_waits"] = replicas.waits
        save(output / "result.json", result)
    return result


def run(output: Path, binary: Path, old_binary: Path | None) -> dict[str, Any]:
    """Run actual TCP/TLS adversarial capacity cases and retain every owned native process wait."""
    output.mkdir(parents=True, exist_ok=False)
    original_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    historical = legacy_baseline(output / "legacy", old_binary) if old_binary else None
    replicas = ActualReplicas(output, binary)
    result: dict[str, Any] = {"passed": False}
    try:
        for member in range(1, 4):
            replicas.start(member)
        wait_for(replicas.leader, "original actual quorum")
        result["pre_tls"] = connection_pressure(replicas, 1)
        result["large_frame"] = frame_pressure(replicas)
        result["framing_identity"] = framing_and_identity(replicas, 1)
        result["timeout_drop"] = timeout_and_drop(replicas, 1)
        result["final_quorum"] = wait_for(replicas.leader, "final actual quorum fence")
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == original_digest
        result.update(
            passed=True,
            binary_sha256=original_digest,
            async_workers_per_member=1,
            nodes=0,
            devices=0,
            controller_children=0,
            models=0,
            simulators=0,
            scope="local infrastructure transport capacities; no RSS/physical/complete-HA claim",
            legacy_baseline=historical,
            legacy_binary_available=old_binary is not None,
            checker_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
    finally:
        replicas.close()
        result["actual_owned_process_waits"] = replicas.waits
        save(output / "result.json", result)
    return result


def main() -> None:
    """Accept explicit isolated output and maintained probe binary, never discover live services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--probe", type=Path, default=ROOT / "target/debug/examples/replica_transport_probe"
    )
    parser.add_argument("--legacy-probe", type=Path)
    arguments = parser.parse_args()
    result = run(
        arguments.output.absolute(),
        arguments.probe.resolve(strict=True),
        arguments.legacy_probe.resolve(strict=True) if arguments.legacy_probe else None,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
