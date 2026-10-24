"""Check independent Raft/mTLS processes, original Controller bytes and actual native failover.

Optional owned file-contract children exercise State/Unix ingress without a full Controller kernel.
No embodied Node, device, model, simulator or physical continuity is created.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
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
from check_state_verifier_processes import address
from network_content_membership_processes import hold_snapshot_read, run_learner_joint
from owned_content_source_processes import run_source
from replica_parent_adoption_processes import PayloadReadBarrier

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
        # Long retained histories may spend more than one election interval in local replay;
        # keep the startup wait bounded without mistaking a slow process for readiness.
        deadline = time.monotonic() + 120
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
    output: Path,
    binary: Path,
    storage: Path,
    source: Path | None,
    members: list[int],
    owned_source: Path | None = None,
    historical_log_catchup: bool = False,
    snapshot_catchup: bool = False,
    learner_joint: bool = False,
) -> dict[str, Any]:
    """Check independent SQL, byte and TLS observations plus actual process waits."""
    if not 3 <= len(members) <= 9 or len(set(members)) != len(members) or min(members) <= 0:
        raise ValueError("declare three to nine distinct positive infrastructure members")
    if (historical_log_catchup or snapshot_catchup or learner_joint) and owned_source is not None:
        raise ValueError(
            "historical Log gate requires its actual complete frozen Controller source"
        )
    if sum((historical_log_catchup, snapshot_catchup, learner_joint)) > 1:
        raise ValueError(
            "run retained Log, purged Snapshot and voter admission gates independently"
        )
    if owned_source is None:
        if source is None:
            raise ValueError("original capture gate requires explicit actual Controller evidence")
        assert json.loads((source / "result.json").read_bytes())["passed"] is True
        assert (
            json.loads((source / "owned-process-exits.json").read_bytes())[
                "all_owned_processes_reaped"
            ]
            is True
        )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    identities = {str(path): digest(path) for path in [binary, storage, Path(__file__)]}
    identities.update(
        {str(path): digest(path) for path in (ROOT / "core/controller-replication").rglob("*.rs")}
    )
    membership_gate = Path(__file__).with_name("network_content_membership_processes.py")
    identities[str(membership_gate)] = digest(membership_gate)
    barrier_source = Path(__file__).with_name("replica_parent_adoption_processes.py")
    identities[str(barrier_source)] = digest(barrier_source)
    if owned_source is not None:
        identities[str(owned_source)] = digest(owned_source)
        identities.update(
            {str(path): digest(path) for path in (ROOT / "core/artifact-store").rglob("*.rs")}
        )
        identities.update({str(path): digest(path) for path in (ROOT / "core/state").rglob("*.rs")})
        for name in ("owned_content_source_processes.py", "check_prepared_source_processes.py"):
            path = Path(__file__).with_name(name)
            identities[str(path)] = digest(path)
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
            ipc = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="rg-owned-content-")))
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
                config: dict[str, Any] = {
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": "actual-original-network-content",
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members[:-1] if learner_joint else members,
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
                if owned_source is not None:
                    config["owned_child"] = True
                    config["child_socket"] = str(ipc / f"member-{member}.sock")
                    config["replica"]["application_policy_digest"] = digest(owned_source)
                if snapshot_catchup:
                    config["snapshot_receives"] = True
                if learner_joint:
                    config["reclamation"] = True
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
                            "reclamation": learner_joint,
                        },
                    },
                    "actual-original-root",
                )["identity"]
                owners[str(member)] = identity["store_owner"]
                if snapshot_catchup:
                    upgraded = json.loads(
                        subprocess.check_output(
                            [str(binary), "snapshot-upgrade", str(path)], timeout=10
                        )
                    )
                    assert upgraded["identity"] == identity
                    assert upgraded["layout"] == "roboguide.replica-content-layout/v0.6"
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
                                if status.get("readiness", {}).get("metadata_ready") is not True:
                                    continue
                                return member
                    time.sleep(0.05)
                raise ValueError("actual infrastructure quorum did not become available")

            for member in members:
                start(member)
            current = leader()
            missed = None
            if historical_log_catchup or snapshot_catchup:
                missed = next(member for member in members if member != current)
                actors[missed].stop()
            if owned_source is not None:
                result["actual_owned_file_source"] = run_source(
                    actors[current], configs[current], owned_source, output, calls, members
                )
                captured = {
                    "candidate_digest": actors[current].ask({"action": "quorum"})["target"][
                        "batch_digest"
                    ]
                }
                result["actual_supervised_file_contract_children"] = 2
            else:
                assert source is not None
                captured = actors[current].ask(
                    {
                        "action": "capture",
                        "source_sql": str(source / "controller.sqlite3"),
                        "source_cas": str(source / "cas"),
                        "operation_id": "actual-original-source-staging",
                    }
                )
                assert actors[current].ask({"action": "commit"})["receipt"]["accepted"] is True
                ready_status = actors[current].ask({"action": "status"})["readiness"]
                assert ready_status["metadata_ready"] is True
                assert ready_status["content_ready"] is True
                assert (
                    len(ready_status["complete_content_members"])
                    > len(configs[current]["replica"]["initial_voters"]) // 2
                )
            if learner_joint:
                result["actual_learner_joint"] = run_learner_joint(
                    actors, configs, members, current
                )
                assert all(digest(Path(path)) == expected for path, expected in identities.items())
                result["passed"] = True
                return result
            if missed is not None:
                root = Path(configs[missed]["content_root"])
                with closing(
                    sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
                ) as sql:
                    assert sql.execute("SELECT COUNT(*) FROM content_objects").fetchone()[0] == 0
                compact = None
                if snapshot_catchup:
                    compact = actors[current].ask({"action": "compact-snapshot"})
                    assert (
                        compact["actual_snapshot"]
                        == compact["original_applied"]
                        == compact["actual_purged"]
                    )
                    with closing(
                        sqlite3.connect(
                            Path(configs[current]["parent_sql"]).as_uri() + "?mode=ro", uri=True
                        )
                    ) as sql:
                        assert sql.execute("SELECT COUNT(*) FROM replica_v1_log").fetchone()[0] == 0
                if snapshot_catchup:
                    assert compact is not None
                    target = actors[current].ask({"action": "quorum"})["target"]
                    first_key = target["inputs"][0]
                    source_root = Path(configs[current]["content_root"])
                    payload = (
                        source_root
                        / "content-v1"
                        / first_key["source_owner"][15:]
                        / first_key["content_digest"][7:]
                    )
                    assert "sha256:" + digest(payload) == first_key["content_digest"]
                    with PayloadReadBarrier(payload) as barrier:
                        start(missed)
                        result["actual_snapshot_heartbeat_hold"] = hold_snapshot_read(
                            barrier,
                            Path(configs[missed]["parent_sql"]),
                            compact["original_epoch"],
                        )
                else:
                    start(missed)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    _, state = sql_identity(Path(configs[missed]["parent_sql"]))
                    if (state.get("content_state") or {}).get("batch_digest") == captured[
                        "candidate_digest"
                    ]:
                        break
                    time.sleep(0.05)
                else:
                    raise ValueError(
                        "actual original catch-up did not supply missing bytes before apply"
                    )
                with closing(
                    sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
                ) as sql:
                    histories = [
                        json.loads(raw)
                        for (raw,) in sql.execute("SELECT record FROM content_streams")
                    ]
                original = [
                    row
                    for row in histories
                    if row["intent"]["command_id"].startswith(
                        "snapshot-content:" if snapshot_catchup else "historical-log:"
                    )
                ]
                assert original and all(row["receipt"] is not None for row in original)
                actual_sources = {row["intent"]["origin"]["member"] for row in original}
                assert actual_sources <= set(members) - {missed}
                assert all(
                    row["intent"]["origin"]["store_owner"]
                    == owners[str(row["intent"]["origin"]["member"])]
                    for row in original
                )
                evidence = {
                    "member": missed,
                    "original_source": current,
                    "actual_sources": sorted(actual_sources),
                    "streams": original,
                    "independent_actual_bytes": actual_bytes(root),
                }
                if snapshot_catchup:
                    while time.monotonic() < deadline:
                        with closing(
                            sqlite3.connect(
                                (root / "content.sqlite3").as_uri() + "?mode=ro", uri=True
                            )
                        ) as sql:
                            receives = [
                                json.loads(raw)
                                for (raw,) in sql.execute(
                                    "SELECT record FROM content_snapshot_receives"
                                )
                            ]
                        if len(receives) == 1 and receives[0]["retirement"] is not None:
                            break
                        time.sleep(0.02)
                    assert (
                        len(receives) == 1
                        and receives[0]["ready"] is not None
                        and receives[0]["retirement"] is not None
                    )
                    assert receives[0]["intent"]["header"]["source_member"] in actual_sources
                    assert len(receives[0]["ready"]["inputs"]) == len(actual_bytes(root))
                    evidence.update(original_receives=receives, original_source_compaction=compact)
                result[
                    "actual_snapshot_catchup"
                    if snapshot_catchup
                    else "actual_historical_log_catchup"
                ] = evidence
                current = leader()
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
                assert originals[member][0] == (8 if owned_source is not None else 7)
                assert (
                    originals[member][1]["membership"]["membership"]["nodes"][str(member)][
                        "content_store_owner"
                    ]
                    == owners[str(member)]
                )
                actual_bytes(Path(configs[member]["content_root"]))
            if not snapshot_catchup:
                route_source = next(member for member in members if member != current)
                source_key = actual_bytes(Path(configs[route_source]["content_root"]))[0]
                current_root = Path(configs[current]["content_root"])
                routed_path = (
                    current_root
                    / "content-v1"
                    / source_key["source_owner"][15:]
                    / source_key["content_digest"][7:]
                )
                withheld = routed_path.with_suffix(".actual-route-original")
                routed_path.rename(withheld)
                route_request = {
                    "action": "route-committed",
                    "target": first["target"],
                    "content": source_key,
                    "receiver": current,
                    "command_id": "actual-committed-follower-to-leader",
                }
                routed = actors[route_source].ask(route_request)["original"]
                assert routed["receipt"]["content"] == source_key
                assert routed["original"]["intent"]["origin"]["member"] == route_source
                assert (
                    routed["original"]["intent"]["origin"]["store_owner"]
                    == owners[str(route_source)]
                )
                assert routed_path.read_bytes() == withheld.read_bytes()
                source_path = (
                    Path(configs[route_source]["content_root"])
                    / "content-v1"
                    / source_key["source_owner"][15:]
                    / source_key["content_digest"][7:]
                )
                source_hidden = source_path.with_suffix(".actual-route-replay-source")
                source_path.rename(source_hidden)
                assert actors[route_source].ask(route_request)["original"] == routed
                source_hidden.rename(source_path)
                routed_path.unlink()
                withheld.rename(routed_path)
                changed_route = json.loads(json.dumps(route_request))
                changed_route["command_id"] = "actual-uncommitted-route-refused"
                changed_route["target"]["revision"] += 1
                assert "error" in actors[route_source].ask(changed_route, required=False)
                result["actual_committed_member_route"] = {
                    "source_member": route_source,
                    "receiver": current,
                    "original": routed,
                    "exact_replay_with_source_hidden": True,
                    "uncommitted_target_refused": True,
                }
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
                if "complete_members" not in observed:
                    assert "error" in observed, (
                        "metadata-only majority falsely granted full content ACK"
                    )
                elif available > len(members) // 2:
                    assert len(observed["complete_members"]) == available
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
            restored = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                candidate = actors[successor].ask({"action": "quorum"}, required=False)
                if "complete_members" in candidate and set(candidate["complete_members"]) == set(
                    members
                ):
                    restored = candidate
                    break
                successor = leader()
                time.sleep(0.05)
            if restored is None:
                raise ValueError("actual reopened leader did not expose complete content quorum")
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
    parser.add_argument("--source", type=Path)
    parser.add_argument("--owned-source-probe", type=Path)
    parser.add_argument("--members", required=True)
    parser.add_argument("--historical-log-catchup", action="store_true")
    parser.add_argument("--snapshot-catchup", action="store_true")
    parser.add_argument("--learner-joint", action="store_true")
    args = parser.parse_args()
    result = run(
        args.output,
        args.probe.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
        None if args.source is None else args.source.resolve(strict=True),
        [int(member) for member in args.members.split(",")],
        None if args.owned_source_probe is None else args.owned_source_probe.resolve(strict=True),
        args.historical_log_catchup,
        args.snapshot_catchup,
        args.learner_joint,
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
