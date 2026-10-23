"""Check actual legacy member encoding through mixed mTLS binaries and original SQL restarts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from functools import partial
from pathlib import Path
from typing import Any

from check_controller_membership_processes import wait_for
from check_replica_content_processes import digest, save
from check_replica_transport_processes import ActualReplicas


def membership(replicas: ActualReplicas, member: int) -> dict[str, str]:
    """Read exact original SQL member values without retaining an idle connection across restart."""
    path = replicas.output / f"member-{member}.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as sql:
        values = dict(
            sql.execute(
                "SELECT key,value FROM replica_v1_meta WHERE key IN ('identity','membership')"
            )
        )
    if set(values) != {"identity", "membership"}:
        raise ValueError("original consensus membership has not actually applied")
    stored = json.loads(values["membership"])
    nodes = stored["membership"]["nodes"]
    if set(nodes) != set(replicas.members):
        raise ValueError(
            "actual original membership differs from its explicit infrastructure profile"
        )
    if any(set(node) != {"addr"} for node in nodes.values()):
        raise ValueError("legacy stored member acquired hidden new authority fields")
    if any(node["addr"] != replicas.members[member] for member, node in nodes.items()):
        raise ValueError("actual stored member endpoints differ from their original declarations")
    return values


def run(output: Path, binary: Path, original_binary: Path | None) -> dict[str, Any]:
    """Roll actual infrastructure processes forward/backward without Controller children."""
    output.mkdir(parents=True, exist_ok=False)
    original = original_binary or binary
    frozen = {str(path): digest(path) for path in (binary, original)}
    if original_binary is not None and digest(original) == digest(binary):
        raise ValueError(
            "historical binary must differ; current-profile restart is a separate scope"
        )
    replicas = ActualReplicas(output, original)
    result: dict[str, Any] = {
        "schema": "roboguide.replica-member-compatibility-process-check/v0.1",
        "passed": False,
        "binaries": frozen,
        "historical_binary_available": original_binary is not None,
        "nodes": 0,
        "devices": 0,
        "controller_children": 0,
        "models": 0,
        "simulators": 0,
    }
    incarnations = []
    try:
        for member in range(1, 4):
            replicas.start(member)
            incarnations.append(
                {"member": member, "pid": replicas.processes[member].pid, "binary": str(original)}
            )
        wait_for(replicas.leader, "actual original mTLS quorum")
        originals = {
            member: wait_for(partial(membership, replicas, member), "actual original member SQL")
            for member in range(1, 4)
        }
        inodes = {
            member: (output / f"member-{member}.sqlite3").stat().st_ino for member in range(1, 4)
        }
        phases = []
        for label, replacement in (("forward", binary), ("rollback", original)):
            leader, _ = wait_for(replicas.leader, "current original leader before rolling change")
            order = [leader, *(member for member in range(1, 4) if member != leader)]
            for member in order:
                old_pid = replicas.processes[member].pid
                replicas.stop(member)
                successor, epoch = wait_for(replicas.leader, "actual surviving two-member quorum")
                replicas.binary = replacement
                replicas.start(member)
                incarnations.append(
                    {
                        "member": member,
                        "pid": replicas.processes[member].pid,
                        "binary": str(replacement),
                    }
                )
                wait_for(replicas.leader, "actual mixed-version quorum after original restart")
                actual = wait_for(
                    partial(membership, replicas, member), "actual reopened original SQL membership"
                )
                if (
                    actual != originals[member]
                    or (output / f"member-{member}.sqlite3").stat().st_ino != inodes[member]
                ):
                    raise ValueError(
                        "rolling member restart replaced its original SQL inode/identity/membership"
                    )
                for live in range(1, 4):
                    if membership(replicas, live) != originals[live]:
                        raise ValueError("mixed-version quorum rewrote original member data")
                phases.append(
                    {
                        "phase": label,
                        "member": member,
                        "old_pid": old_pid,
                        "new_pid": replicas.processes[member].pid,
                        "actual_surviving_leader": successor,
                        "actual_epoch": epoch,
                        "original_sql_inode": inodes[member],
                        "exact_original_member_rows": True,
                    }
                )
        final = wait_for(replicas.leader, "actual rolled-back original quorum")
        if any(digest(Path(path)) != expected for path, expected in frozen.items()):
            raise ValueError(
                "original/current binary changed during actual member compatibility check"
            )
        result.update(
            passed=True,
            phases=phases,
            final_quorum=final,
            scope="legacy member compatibility; no content-profile ACK or full HA claim",
            checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
    finally:
        replicas.close()
        result["actual_incarnations"] = incarnations
        result["actual_process_waits"] = replicas.waits
        save(output / "result.json", result)
    return result


def main() -> None:
    """Accept explicit owned output/binaries, never inspect or replace an external live service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--old-probe", type=Path)
    arguments = parser.parse_args()
    result = run(
        arguments.output.absolute(),
        arguments.probe.resolve(strict=True),
        arguments.old_probe.resolve(strict=True) if arguments.old_probe else None,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
