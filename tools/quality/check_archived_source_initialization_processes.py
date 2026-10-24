"""Exercise actual archived-source initialization over independent real mTLS/SQL/CAS processes."""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from archived_initialization_fault_processes import interrupt, miss_initialization
from check_network_content_processes import Actor, actual_bytes, sql_identity
from check_replica_certificate_processes import create_ca, issue
from check_replica_content_processes import Processes, digest, save

ROOT = Path(__file__).resolve().parents[2]


def original_rows(path: Path) -> dict[str, str]:
    """Independently read the exact original local operations without renewing or filling."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        return dict(sql.execute("SELECT command_id,record FROM content_initializations"))


def run(
    output: Path,
    binary: Path,
    storage: Path,
    old: Path,
    source: Path,
    interrupt_content: Path | None = None,
    catchup: str | None = None,
) -> dict[str, Any]:
    """Retain every original failure/PID/wait while composing actual old-voter refusal and first."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    prior = json.loads((source / "result.json").read_bytes())
    exits = json.loads((source / "owned-process-exits.json").read_bytes())
    if prior.get("passed") is not True or exits.get("all_owned_processes_reaped") is not True:
        raise ValueError("actual original source has no complete native wait/evidence")
    paths = [binary, storage, old, Path(__file__)]
    paths.append(Path(__file__).with_name("archived_initialization_fault_processes.py"))
    paths.extend((ROOT / "core/controller-replication").rglob("*.rs"))
    paths.extend((ROOT / "core/state").rglob("*.rs"))
    paths.extend((ROOT / "core/artifact-store").rglob("*.rs"))
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    input_value = {
        "source_sql": str(source / "controller.sqlite3"),
        "source_cas": str(source / "cas"),
    }
    input_path = output / "original-source-input.json"
    save(input_path, input_value)
    observed = json.loads(
        subprocess.check_output(
            [str(binary), "inspect-initialization-source", str(input_path)], timeout=30
        )
    )
    save(output / "original-source-observation.json", observed)
    members = [401, 409, 419]
    policy: dict[str, Any] = {
        "schema": "roboguide.content-initialization-policy/v0.1",
        "source_state_digest": observed["source_state_digest"],
        "source_owner": observed["source_owner"],
        "source_member": members[0],
        "command_id": "actual-original-archived-source-initialization",
        "budget_ms": 120_000,
    }
    calls = Processes(storage, output)
    actors: dict[int, Actor] = {}
    incarnations: list[Actor] = []
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes_created": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "source_contains_original_software_node_history": True,
    }
    with ExitStack() as scope:
        try:
            ipc = Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="rg-source-init-")))
            tls = output / "tls"
            create_ca(tls)
            leaves = {member: issue(tls, f"member-{member}") for member in members}
            pins = {str(member): leaf for member, (_, leaf) in leaves.items()}
            listeners: dict[int, socket.socket] = {}
            endpoints = {}
            for member in members:
                listener = scope.enter_context(socket.socket())
                listener.bind(("127.0.0.1", 0))
                endpoints[str(member)] = "127.0.0.1:" + str(listener.getsockname()[1])
                listeners[member] = listener
            schema = json.loads(subprocess.check_output([str(binary), "schema"], timeout=10))[
                "schema"
            ]
            configs: dict[int, dict[str, Any]] = {}
            owners = {}
            profiles = set()
            for member in members:
                directory = output / f"member-{member}"
                directory.mkdir(mode=0o700)
                bundle = str(leaves[member][0])
                config: dict[str, Any] = {
                    "replica": {
                        "schema": "roboguide.controller-replica/v0.4",
                        "cluster_id": "actual-archived-source-initialization",
                        "replica_id": member,
                        "members": endpoints,
                        "initial_voters": members,
                        "application_schema": schema,
                        "application_policy_digest": "actual-original-source:" + digest(input_path),
                        "tls": {
                            "ca_cert_path": str(tls / "ca.crt"),
                            "cert_path": bundle,
                            "key_path": bundle,
                            "server_name": "localhost",
                            "peer_certificate_digests": pins,
                        },
                    },
                    "content_root": str(directory / "content"),
                    "parent_sql": str(directory / "replica.sqlite3"),
                    "member_stores": {},
                    "staging_gc": True,
                    "initialization_parent": True,
                    "initialization": policy,
                }
                path = output / f"configuration-{member}.json"
                save(path, config)
                profile = json.loads(
                    subprocess.check_output([str(binary), "profile", str(path)], timeout=10)
                )["profile"]
                profiles.add(profile)
                root = calls.run(
                    {
                        "action": "create",
                        "store": {
                            "root": config["content_root"],
                            "profile": profile,
                            "member": member,
                            "staging_gc": True,
                        },
                    },
                    "actual-original-root",
                )["identity"]
                owners[str(member)] = root["store_owner"]
                configs[member] = config
            assert len(profiles) == 1

            def start(member: int, *, legacy: bool = False) -> Actor:
                """Start only the exact declared real member, retaining every original."""
                config = dict(configs[member], member_stores=owners)
                if legacy:
                    config.pop("initialization_parent")
                    config.pop("initialization")
                    config.update(owned_child=True, child_socket=str(ipc / f"member-{member}.sock"))
                path = output / f"configuration-{member}-incarnation-{len(incarnations)}.json"
                save(path, config)
                listeners[member].close()
                log = scope.enter_context(
                    (output / f"member-{member}-incarnation-{len(incarnations)}.log").open("xb")
                )
                actor = Actor(old if legacy else binary, path, log, incarnations)
                actors[member] = actor
                return actor

            def leader(required: int | None = None) -> int:
                """Observe the actual current leader and metadata quorum."""
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    for member, actor in actors.items():
                        if actor.process.poll() is None:
                            status = actor.ask({"action": "status"})
                            if status["quorum"] and (required is None or required == member):
                                return member
                    time.sleep(0.05)
                raise ValueError("actual original source leader did not become available")

            for member in members:
                start(member, legacy=member == members[-1])
            current = leader()
            if current != policy["source_member"]:
                actors[current].ask(
                    {
                        "action": "transfer-to",
                        "member": policy["source_member"],
                        "transfer_id": "actual-original-empty-source-handoff",
                    }
                )
                current = leader(policy["source_member"])
            request = {"action": "initialize-source", "input": input_value}
            rejected = actors[current].ask(request, required=False)
            assert "error" in rejected
            assert not original_rows(Path(configs[current]["parent_sql"]))
            assert all(
                sql_identity(Path(configs[member]["parent_sql"]))[1]["content_state"] is None
                for member in members
            )
            result["actual_old_voter_refusal_before_intent"] = rejected
            legacy = members[-1]
            actors[legacy].stop()
            path = output / f"configuration-{legacy}-upgrade.json"
            save(path, dict(configs[legacy], member_stores=owners))
            inode = Path(configs[legacy]["parent_sql"]).stat().st_ino
            upgraded = json.loads(
                subprocess.check_output(
                    [str(binary), "initialization-upgrade", str(path)], timeout=20
                )
            )
            assert (
                upgraded["revision"] == 0
                and Path(configs[legacy]["parent_sql"]).stat().st_ino == inode
            )
            old_configuration = actors[legacy].record["config"]
            refused_writer = subprocess.run(
                [str(old), "serve", old_configuration],
                input=b"",
                capture_output=True,
                timeout=15,
                check=False,
            )
            assert refused_writer.returncode != 0
            save(
                output / "actual-old-parent-writer-refusal.json",
                {
                    "returncode": refused_writer.returncode,
                    "stdout": refused_writer.stdout.decode(),
                    "stderr": refused_writer.stderr.decode(),
                },
            )
            assert not original_rows(Path(configs[legacy]["parent_sql"]))
            start(legacy)
            leader(current)
            if interrupt_content is not None and catchup is None:
                result["actual_source_interruption"] = interrupt(
                    actors[current],
                    lambda: start(current),
                    request,
                    Path(configs[current]["parent_sql"]),
                    interrupt_content,
                    [Path(configs[member]["parent_sql"]) for member in members],
                )
                for filename, expected in identities.items():
                    assert digest(Path(filename)) == expected
                result["passed"] = True
                return result
            if interrupt_content is not None:
                initialized, missed = miss_initialization(
                    actors[current],
                    actors[legacy],
                    request,
                    Path(configs[current]["parent_sql"]),
                    interrupt_content,
                )
                if catchup == "snapshot":
                    missed["actual_purge"] = actors[current].ask({"action": "compact-snapshot"})
                start(legacy)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    target = actors[current].ask({"action": "quorum"})["target"]
                    observed_receiver = actors[legacy].ask(
                        {"action": "observe", "target": target}, required=False
                    )
                    if "error" not in observed_receiver:
                        missed["actual_receiver_observation"] = observed_receiver
                        break
                    time.sleep(0.05)
                else:
                    raise ValueError(
                        "original empty receiver did not complete archived-source catch-up"
                    )
                result["actual_archived_source_" + str(catchup) + "_catchup"] = missed
            else:
                initialized = actors[current].ask(request)
            status = initialized["original_status"]
            assert status["original_receipt"]["accepted"] is True
            assert (
                status["current_local_anchor"]["source_state_digest"]
                == observed["source_state_digest"]
            )
            retained = original_rows(Path(configs[current]["parent_sql"]))
            assert len(retained) == 1
            assert actors[current].ask(request) == initialized
            changed = {
                "action": "initialize-source",
                "input": dict(
                    input_value, source_sql=str(source / "nonexistent-replacement.sqlite3")
                ),
            }
            assert "error" in actors[current].ask(changed, required=False)
            assert original_rows(Path(configs[current]["parent_sql"])) == retained
            complete = actors[current].ask({"action": "quorum"})
            assert len(complete["complete_members"]) > len(members) // 2
            content = sql_identity(Path(configs[current]["parent_sql"]))[1]["content_state"]
            assert content["candidate"]["batch"]["previous_event_sequence"] == 0
            assert (
                int(content["candidate"]["recovery"]["metadata"]["history_floor"])
                == observed["history_floor"]
                > 0
            )
            result["actual_original_initialization"] = initialized
            result["actual_complete_quorum"] = complete
            for member in members:
                assert sql_identity(Path(configs[member]["parent_sql"]))[0] == 9
                result[f"actual_member_{member}_bytes"] = actual_bytes(
                    Path(configs[member]["content_root"])
                )
            actors[current].stop(kill=True)
            replacement = leader()
            result["actual_native_failover"] = {"original": current, "replacement": replacement}
            start(current)
            assert actors[current].ask(request) == initialized
            assert original_rows(Path(configs[current]["parent_sql"])) == retained
            result["original_operation_unchanged_after_native_restart"] = True
            for filename, expected in identities.items():
                assert digest(Path(filename)) == expected
            result["passed"] = True
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            cleanup = []
            for actor in incarnations:
                try:
                    actor.stop()
                except BaseException as error:
                    cleanup.append(str(error))
            save(output / "native-processes.json", [actor.record for actor in incarnations])
            save(output / "storage-processes.json", [record for _, record in calls.owned])
            if cleanup:
                result.update(passed=False, cleanup_errors=cleanup)
            save(output / "result.json", result)
    return result


def main() -> int:
    """Require explicit existing source and binaries; no model, simulator or device is started."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--storage-probe", type=Path, required=True)
    parser.add_argument("--old-probe", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--interrupt-source-content", type=Path)
    parser.add_argument("--catchup", choices=("log", "snapshot"))
    args = parser.parse_args()
    if args.catchup is not None and args.interrupt_source_content is None:
        parser.error("--catchup requires the explicit native source payload barrier")
    return (
        0
        if run(
            args.output,
            args.probe.resolve(strict=True),
            args.storage_probe.resolve(strict=True),
            args.old_probe.resolve(strict=True),
            args.source.resolve(strict=True),
            None
            if args.interrupt_source_content is None
            else args.interrupt_source_content.resolve(strict=True),
            args.catchup,
        )["passed"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
