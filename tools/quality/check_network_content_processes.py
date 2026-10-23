"""Check independent Raft/mTLS processes, original Controller bytes and actual native failover.

No Controller child, embodied Node, device, model, simulator or physical continuity is created.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import sqlite3
import subprocess
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any, BinaryIO

from check_replica_certificate_processes import create_ca, issue
from check_replica_content_processes import Processes, digest, save
from check_state_verifier_processes import address

ROOT = Path(__file__).resolve().parents[2]


class Actor:
    """Own one actual infrastructure incarnation and retain its requests, responses and waits."""

    def __init__(self, binary: Path, config: Path, log: BinaryIO, owned: list[Actor]) -> None:
        """Spawn only the declared actual service, with a closed private local control pipe."""
        self.process = subprocess.Popen(
            [str(binary), "serve", str(config)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            close_fds=True,
        )
        self.record: dict[str, Any] = {
            "pid": self.process.pid,
            "binary": str(binary),
            "config": str(config),
            "exchanges": [],
        }
        self.buffer = bytearray()
        owned.append(self)
        ready = self.receive()
        assert ready["ready"] is True
        self.record["startup"] = ready

    def receive(self) -> dict[str, Any]:
        """Read a bounded actual response; EOF and timeout never imply success."""
        if self.process.stdout is None:
            raise ValueError("actual process output is unavailable")
        deadline = time.monotonic() + 45
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("actual network process response deadline expired")
                chunk = os.read(
                    self.process.stdout.fileno(),
                    min(65536, 16 * 1024 * 1024 + 1 - len(self.buffer)),
                )
                if not chunk:
                    raise ValueError(f"actual network process ended: {self.process.poll()}")
                self.buffer.extend(chunk)
                if len(self.buffer) > 16 * 1024 * 1024:
                    raise ValueError("actual network process response exceeds its bound")
        raw, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        value: dict[str, Any] = json.loads(raw)
        if value.get("pid") != self.process.pid:
            raise ValueError("response does not belong to the original actual process")
        return value

    def ask(self, request: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
        """Send one exact local command once; errors never automatically re-execute it."""
        if self.process.stdin is None or self.process.poll() is not None:
            raise ValueError("actual original control pipe is no longer live")
        self.process.stdin.write(json.dumps(request).encode() + b"\n")
        self.process.stdin.flush()
        response = self.receive()
        self.record["exchanges"].append({"request": request, "response": response})
        if required and "result" not in response:
            raise ValueError(f"actual original network command refused: {response}")
        value = response.get("result", response)
        if not isinstance(value, dict):
            raise ValueError("actual network process returned a non-object result")
        return value

    def stop(self, *, kill: bool = False) -> None:
        """Retain the original process wait; this never proves device stopping."""
        if "returncode" in self.record:
            return
        self.record["expected_native_kill"] = kill
        if self.process.poll() is None:
            if kill:
                self.process.kill()
            else:
                self.ask({"action": "shutdown"})
        try:
            self.record["returncode"] = self.process.wait(timeout=20)
            if self.record["returncode"] != (-signal.SIGKILL if kill else 0):
                raise ValueError(f"actual infrastructure exit differs: {self.record['returncode']}")
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.record["returncode"] = self.process.wait(timeout=10)
            raise
        finally:
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.process.stdout is not None:
                self.process.stdout.close()


def sql_identity(path: Path) -> tuple[int, dict[str, Any]]:
    """Read original parent SQL and close its readonly connection before restart."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as sql:
        version = sql.execute("PRAGMA user_version").fetchone()[0]
        values = {
            key: json.loads(raw)
            for key, raw in sql.execute(
                "SELECT key,value FROM replica_v1_meta WHERE key IN "
                "('identity','membership','content_root_identity','content_state')"
            )
        }
    return version, values


def actual_bytes(root: Path) -> list[dict[str, Any]]:
    """Independently hash every actual qualified receipt file, without trusting a passed flag."""
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        receipts = [json.loads(raw) for (raw,) in sql.execute("SELECT record FROM content_objects")]
    observed = []
    for receipt in receipts:
        key = receipt["content"]
        path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
        assert path.stat().st_size == key["byte_size"]
        assert "sha256:" + digest(path) == key["content_digest"]
        observed.append(key)
    assert observed
    return observed


def run(
    output: Path, binary: Path, storage: Path, source: Path, members: list[int]
) -> dict[str, Any]:
    """Check independent SQL, byte and TLS observations plus actual process waits."""
    if not 3 <= len(members) <= 9 or len(set(members)) != len(members) or min(members) <= 0:
        raise ValueError("declare three to nine distinct positive infrastructure members")
    assert json.loads((source / "result.json").read_bytes())["passed"] is True
    assert (
        json.loads((source / "owned-process-exits.json").read_bytes())["all_owned_processes_reaped"]
        is True
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    identities = {str(path): digest(path) for path in [binary, storage, Path(__file__)]}
    identities.update(
        {str(path): digest(path) for path in (ROOT / "core/controller-replication").rglob("*.rs")}
    )
    save(output / "source-identity.json", identities)
    calls = Processes(storage, output)
    actors: dict[int, Actor] = {}
    incarnations: list[Actor] = []
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "controller_children": 0,
        "declared_infrastructure_members": members,
    }
    with ExitStack() as scope:
        try:
            tls = output / "tls"
            create_ca(tls)
            leaves = {member: issue(tls, f"member-{member}") for member in members}
            pins = {str(member): leaf for member, (_, leaf) in leaves.items()}
            endpoints = {str(member): address() for member in members}
            schema = json.loads(subprocess.check_output([str(binary), "schema"], timeout=10))[
                "schema"
            ]
            configs: dict[int, dict[str, Any]] = {}
            profiles = set()
            owners = {}
            for member in members:
                owned = output / f"member-{member}"
                owned.mkdir()
                bundle = str(leaves[member][0])
                config = {
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": "actual-original-network-content",
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members,
                        "application_schema": schema,
                        "application_policy_digest": "actual-original-data-contract",
                        "tls": {
                            "ca_cert_path": str(tls / "ca.crt"),
                            "cert_path": bundle,
                            "key_path": bundle,
                            "server_name": "localhost",
                            "peer_certificate_digests": pins,
                        },
                    },
                    "content_root": str(owned / "content"),
                    "parent_sql": str(owned / "replica.sqlite3"),
                    "member_stores": {},
                }
                path = output / f"configuration-{member}.json"
                save(path, config)
                profile = json.loads(
                    subprocess.check_output([str(binary), "profile", str(path)], timeout=10)
                )["profile"]
                profiles.add(profile)
                identity = calls.run(
                    {
                        "action": "create",
                        "store": {
                            "root": config["content_root"],
                            "profile": profile,
                            "member": member,
                        },
                    },
                    "actual-original-root",
                )["identity"]
                owners[str(member)] = identity["store_owner"]
                configs[member] = config
            assert len(profiles) == 1

            def start(member: int) -> Actor:
                """Reopen the original declared member files without changing identity."""
                path = output / f"configuration-{member}.json"
                save(path, dict(configs[member], member_stores=owners))
                log = scope.enter_context(
                    (output / f"member-{member}-{len(incarnations)}.log").open("xb")
                )
                actor = Actor(binary, path, log, incarnations)
                actors[member] = actor
                return actor

            def leader() -> int:
                """Poll actual readonly quorum status and retain every response and epoch."""
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    for member, actor in actors.items():
                        if actor.process.poll() is None:
                            status = actor.ask({"action": "status"})
                            if status["quorum"]:
                                return member
                    time.sleep(0.05)
                raise ValueError("actual infrastructure quorum did not become available")

            for member in members:
                start(member)
            current = leader()
            captured = actors[current].ask(
                {
                    "action": "capture",
                    "source_sql": str(source / "controller.sqlite3"),
                    "source_cas": str(source / "cas"),
                    "operation_id": "actual-original-source-staging",
                }
            )
            assert actors[current].ask({"action": "commit"})["receipt"]["accepted"] is True
            first = actors[current].ask({"action": "quorum"})
            assert set(first["complete_members"]) == set(members)
            assert first["target"]["batch_digest"] == captured["candidate_digest"]
            inodes = {
                member: Path(configs[member]["parent_sql"]).stat().st_ino for member in members
            }
            originals = {
                member: sql_identity(Path(configs[member]["parent_sql"])) for member in members
            }
            for member in members:
                assert originals[member][0] == 7
                assert (
                    originals[member][1]["membership"]["membership"]["nodes"][str(member)][
                        "content_store_owner"
                    ]
                    == owners[str(member)]
                )
                actual_bytes(Path(configs[member]["content_root"]))
            hidden_inputs = []
            for member in [item for item in members if item != current]:
                root = Path(configs[member]["content_root"])
                key = actual_bytes(root)[0]
                path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
                hidden = path.with_suffix(".actual-original-withheld")
                path.rename(hidden)
                hidden_inputs.append((path, hidden))
                observed = actors[current].ask({"action": "quorum"}, required=False)
                available = len(members) - len(hidden_inputs)
                if available > len(members) // 2:
                    assert len(observed["complete_members"]) == available
                else:
                    assert "error" in observed, (
                        "metadata-only majority falsely granted full content ACK"
                    )
            for path, hidden in hidden_inputs:
                hidden.rename(path)
            assert set(actors[current].ask({"action": "quorum"})["complete_members"]) == set(
                members
            )
            result["actual_metadata_only_majority_refused"] = True
            actors[current].stop(kill=True)
            assert actors[current].record["returncode"] == -signal.SIGKILL
            successor = leader()
            assert successor != current
            failover = actors[successor].ask({"action": "quorum"})
            assert len(failover["complete_members"]) > len(members) // 2
            assert failover["target"]["epoch"]["term"] > first["target"]["epoch"]["term"]
            assert failover["target"]["batch_digest"] == first["target"]["batch_digest"]
            start(current)
            successor = leader()
            restored = actors[successor].ask({"action": "quorum"})
            assert set(restored["complete_members"]) == set(members)
            for member in members:
                assert Path(configs[member]["parent_sql"]).stat().st_ino == inodes[member]
                after = sql_identity(Path(configs[member]["parent_sql"]))
                assert after[1]["content_state"] == originals[member][1]["content_state"]
                actual_bytes(Path(configs[member]["content_root"]))
            assert all(digest(Path(path)) == expected for path, expected in identities.items())
            result.update(
                passed=True,
                original_quorum=first,
                actual_native_failover=failover,
                actual_reopened_quorum=restored,
                original_parent_inodes=inodes,
                original_complete_candidate_unchanged=True,
            )
        finally:
            try:
                cleanup_errors = []
                for actor in incarnations:
                    try:
                        actor.stop()
                    except BaseException as error:
                        cleanup_errors.append(str(error))
                        result["passed"] = False
                        if actor.process.poll() is None:
                            actor.process.kill()
                        actor.record["returncode"] = actor.process.wait(timeout=10)
                if cleanup_errors:
                    result["cleanup_errors"] = cleanup_errors
                    raise ValueError(
                        "actual infrastructure cleanup failed; original errors retained"
                    )
            finally:
                calls.cleanup()
                result["actual_incarnations"] = [actor.record for actor in incarnations]
                result["actual_storage_processes"] = [record for _, record in calls.owned]
                save(output / "result.json", result)
    return result


def main() -> None:
    """Require frozen executables, declared infrastructure IDs and original evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--storage-probe", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--members", required=True)
    args = parser.parse_args()
    result = run(
        args.output,
        args.probe.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
        args.source.resolve(strict=True),
        [int(member) for member in args.members.split(",")],
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
