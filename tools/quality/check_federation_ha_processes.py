"""Compose actual Federation resources, independent HA domains and original complete recovery."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import ssl
import subprocess
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from check_execution_coordination_processes import complete
from check_federation_execution_processes import configure
from check_replica_certificate_processes import issue, replace_bundle
from check_replica_content_processes import digest, save
from check_state_verifier_processes import ROOT, wait_for
from federation_ha_processes import ReplicatedCase


def original_state(case: ReplicatedCase) -> dict[str, Any]:
    """Read only the actual currently owned child's original complete checkpoint."""
    member, _status = case.current()
    path = Path(case.configurations[member]["storage_directory"]) / "controller.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        result: dict[str, Any] = json.loads(
            sql.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
        return result


def next_manifest(original: dict[str, Any]) -> dict[str, Any]:
    """Create a separately approved test input without changing any prior transaction or goal."""
    result = copy.deepcopy(original)
    result["transaction_id"] = uuid.uuid4().hex
    for contribution in result["participants"].values():
        contribution["plan"]["mission"]["id"] = "actual-file-" + uuid.uuid4().hex[:12]
    return result


def previous_refusal(case: ReplicatedCase, previous: Path) -> dict[str, Any]:
    """Require genuine old-code refusal before any owned storage changes."""
    before = {
        str(path): digest(path)
        for member in case.configurations
        for path in (case.root / ("member-" + str(member))).rglob("*")
        if path.is_file()
    }
    member = next(iter(case.configurations))
    arguments = [str(previous), str(case.root / f"configuration-{member}.json")]
    actual = subprocess.run(arguments, capture_output=True, timeout=15, check=False)
    record: dict[str, Any] = {
        "binary": str(previous),
        "binary_sha256": digest(previous),
        "arguments": arguments,
        "actual_exit": actual.returncode,
        "stdout": actual.stdout.decode(),
        "stderr": actual.stderr.decode(),
        "original_storage_sha256": before,
        "software_nodes": 0,
        "devices": 0,
    }
    save(case.root / "previous-supervisor-refusal.json", record)
    assert actual.returncode != 0 and "peer profile changed its declared domain/schema" in (
        record["stdout"] + record["stderr"]
    ), record
    after = {
        str(path): digest(path)
        for member in case.configurations
        for path in (case.root / ("member-" + str(member))).rglob("*")
        if path.is_file()
    }
    assert after == before
    record["storage_unchanged"] = True
    save(case.root / "previous-supervisor-refusal.json", record)
    return record


def transfer(case: ReplicatedCase, label: str, target_member: int | None = None) -> dict[str, Any]:
    """Send one actual original V3 transfer and retain native source drain and real newer epoch."""
    source, before = case.current()
    target = (
        target_member
        if target_member is not None
        else next(member for member in case.configurations if member != source)
    )
    assert target in case.configurations and target != source
    path = case.root / f"configuration-{source}.json"
    operation = uuid.uuid4().hex
    arguments = [
        str(case.supervisor),
        "--admin",
        str(path),
        "--leader",
        str(source),
        "--transfer-leader",
        str(target),
        "--transfer-id",
        operation,
    ]
    actual = subprocess.run(arguments, capture_output=True, timeout=45, check=False)
    record = {
        "arguments": arguments,
        "returncode": actual.returncode,
        "stdout": actual.stdout.decode(),
        "stderr": actual.stderr.decode(),
        "original_source_status": before,
        "original_operation_id": operation,
    }
    save(case.root / (label + "-transfer.json"), record)
    assert actual.returncode == 0, record

    def newer() -> tuple[int, dict[str, Any]] | None:
        """Observe the actual target process/epoch without repeating a transfer command."""
        member, status = case.current()
        return (
            (member, status)
            if member == target and status["epoch"]["term"] > before["epoch"]["term"]
            else None
        )

    member, status = wait_for(newer, "actual original target HA child", 45)
    assert member == target and status["child_pid"] != before["child_pid"]
    record["actual_target_status"] = status
    wait_for(lambda: case.exchange("/healthz"), "actual new original child HTTP readiness", 30)
    save(case.root / (label + "-transfer.json"), record)
    return record


def finish(
    cases: list[ReplicatedCase],
    manifest: dict[str, Any],
    counts: list[int],
) -> dict[str, Any]:
    """Finish only actual new CPU children, then independently prove each original contribution."""
    root = cases[0]
    path = "/v1/federation/transactions/" + manifest["transaction_id"]
    wait_for(
        lambda: len(root.exchange(path)["released"]) == len(cases),
        "actual complete HA Commit/release proofs",
        90,
    )
    for case, count in zip(cases, counts, strict=True):
        wait_for(
            lambda case=case, count=count: len(case.backend.jobs) == count + 1,
            "actual new owned domain CPU child",
            45,
        )
        job = list(case.backend.jobs.values())[-1]
        complete(case, job)
    for case, domain in reversed(list(zip(cases, manifest["participants"], strict=True))):
        case.finish_goal(manifest["participants"][domain]["plan"]["mission"]["id"])
    wait_for(
        lambda: root.exchange(path)["global_completed"], "actual independent HA global goals", 60
    )
    progress = root.exchange(path)
    for case, domain in zip(cases, manifest["participants"], strict=True):
        _member, actual = case.current()
        assert progress["prepared"][domain]["epoch"].startswith(actual["profile"] + ":")
    return progress


def archive(case: ReplicatedCase, label: str) -> dict[str, Any]:
    """Archive the actual covered prefix under its original complete-content quorum."""
    member, _ = case.current()
    parent = Path(case.configurations[member]["storage_directory"])
    with closing(
        sqlite3.connect((parent / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        sequence = sql.execute("SELECT event_sequence FROM controller_checkpoint").fetchone()[0]
    assert sequence > 0
    result = case.exchange(
        "/v1/maintenance/coordinated-archive",
        {
            "schema": "roboguide.coordinated-archive-command/v0.1",
            "command_id": uuid.uuid4().hex,
            "end_sequence": sequence,
            "budget_ms": 120000,
        },
    )
    assert result["completed_ms"] is not None
    save(case.root / (label + "-archive.json"), result)
    return result


def rotate_replica_leaf(case: ReplicatedCase) -> dict[str, Any]:
    """Stage, replace and retire one actual member leaf during the original transaction."""
    leader, _ = case.current()
    target = next(member for member in case.configurations if member != leader)
    original = case.configurations[target]["replica"]["tls"]["peer_certificate_digests"][
        str(target)
    ]
    replacement, leaf = issue(case.root / "tls", "actual-live-rotation-" + str(target))

    def revision() -> int:
        """Read only the current leader's original durable trust revision."""
        current, _ = case.current()
        path = Path(case.configurations[current]["storage_directory"]) / "replica.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
            return int(
                json.loads(
                    sql.execute(
                        "SELECT value FROM replica_v1_meta WHERE key='certificate_trust'"
                    ).fetchone()[0]
                )["revision"]
            )

    def send(caller: int, command: dict[str, Any]) -> dict[str, Any]:
        """Send one original mTLS certificate command without retrying any uncertain effect."""
        current, _ = case.current()
        path = case.root / (command["command_id"] + ".json")
        save(path, command)
        arguments = [
            str(case.supervisor),
            "--admin",
            str(case.root / f"configuration-{caller}.json"),
            "--leader",
            str(current),
            "--rotate-certificate",
            str(path),
        ]
        reply = subprocess.run(arguments, capture_output=True, timeout=30, check=False)
        record = {
            "arguments": arguments,
            "returncode": reply.returncode,
            "stdout": reply.stdout.decode(),
            "stderr": reply.stderr.decode(),
        }
        save(case.root / (command["command_id"] + "-actual.json"), record)
        assert reply.returncode == 0, record
        return record

    previous = revision()
    stage = "actual-stage-" + uuid.uuid4().hex
    staged = send(
        leader,
        {
            "command_id": stage,
            "expected_revision": previous,
            "member": target,
            "action": {"kind": "stage", "previous_leaf": original, "new_leaf": leaf},
        },
    )
    replace_bundle(replacement, Path(case.configurations[target]["replica"]["tls"]["cert_path"]))
    retired = send(
        target,
        {
            "command_id": "actual-retire-" + uuid.uuid4().hex,
            "expected_revision": previous + 1,
            "member": target,
            "action": {
                "kind": "retire",
                "stage_command_id": stage,
                "previous_leaf": original,
                "new_leaf": leaf,
            },
        },
    )
    assert revision() == previous + 2
    return {
        "member": target,
        "original_leaf": original,
        "actual_new_leaf": leaf,
        "stage": staged,
        "retire": retired,
    }


def run(
    output: Path,
    supervisor: Path,
    storage: Path,
    controller: Path,
    node: Path,
    previous_supervisor: Path,
) -> dict[str, Any]:
    """Retain actual independent domains through normal, partition, epoch and content cases."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    members = [int(uuid.uuid4().hex[:8], 16) for _ in range(3)]
    cases = [
        ReplicatedCase(output / name, supervisor, storage, members, controller, node)
        for name in ["root", "peer"]
    ]
    manifest, relay = configure(
        cases,
        False,
        request_budget_ms=30000,
        transaction_budget_ms=300000,
        max_clock_uncertainty_ms=10000,
        peer_schema="roboguide.federation-peers/v0.2",
    )
    assert relay is not None
    result: dict[str, Any] = {
        "passed": False,
        "software_nodes": 2,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "independent_control_domains": 2,
        "shared_member_ids": members,
    }
    paths = [
        supervisor,
        previous_supervisor,
        storage,
        cases[0].binary,
        cases[0].node_binary,
        Path(__file__),
        Path(__file__).with_name("federation_ha_processes.py"),
        Path(__file__).with_name("coordination_processes.py"),
        Path(__file__).with_name("check_federation_execution_processes.py"),
    ]
    paths += [
        Path(__file__).with_name(name)
        for name in (
            "federation_ha_recovery_processes.py",
            "cohort_process_profile.py",
            "memory_preparation_profile.py",
        )
    ]
    paths += list((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py"))
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    cleanup_errors: list[str] = []
    try:
        for case in cases:
            case.prepare_cluster()
        result["previous_budget_profile_refusal"] = previous_refusal(cases[0], previous_supervisor)
        for case in reversed(cases):
            case.boot()
        root = cases[0]
        path = "/v1/federation/transactions/" + manifest["transaction_id"]
        root.exchange("/v1/federation/transactions", manifest)
        wait_for(relay.commit_held.is_set, "actual HA peer Commit with original reply held", 180)
        assert all(not case.backend.jobs for case in cases)
        assert not root.exchange(path)["global_completed"]
        relay.release_reply.set()
        result["normal"] = finish(cases, manifest, [0, 0])
        save(output / "normal-global-proof.json", result["normal"])
        counts = [len(case.backend.jobs) for case in cases]
        result["completed_private_archives"] = [archive(case, "completed-prefix") for case in cases]
        # Both actual domains retain original authority after independent real source drains.
        result["completed_transfers"] = [transfer(case, "completed") for case in cases]
        assert root.exchange(path)["global_completed"]
        assert [len(case.backend.jobs) for case in cases] == counts
        result["completed_intents_retained"] = [
            original_state(case)["federation"] for case in cases
        ]
        # A real transport partition after remote Prepare cannot authorize either CPU worker.
        interrupted = next_manifest(manifest)
        interrupted_path = "/v1/federation/transactions/" + interrupted["transaction_id"]
        relay.interrupt = True
        relay.prepare_held.clear()
        relay.release_reply.clear()
        root.exchange("/v1/federation/transactions", interrupted)
        wait_for(relay.prepare_held.is_set, "actual original remote Prepared before partition", 180)
        before = original_state(root)["federation"]["coordinations"]["transactions"][
            interrupted["transaction_id"]
        ]
        relay.partition.set()
        assert [len(case.backend.jobs) for case in cases] == counts
        result["actual_replica_rotation_while_partitioned"] = rotate_replica_leaf(root)
        result["partition_root_transfer"] = transfer(root, "partition")
        relay.release_reply.set()
        after = original_state(root)["federation"]["coordinations"]["transactions"][
            interrupted["transaction_id"]
        ]
        assert before["budget"] == after["budget"] and before["nonces"] == after["nonces"]
        assert [len(case.backend.jobs) for case in cases] == counts
        relay.partition.clear()
        wait_for(
            lambda: len(root.exchange(interrupted_path)["stopped"]) == len(cases),
            "actual HA original stop after partition repair",
            60,
        )
        progress = root.exchange(interrupted_path)
        assert progress["decision"] is None and not progress["global_completed"]
        assert [len(case.backend.jobs) for case in cases] == counts
        assert (
            sum(
                row["path"].endswith("/prepare") and row.get("forwarded", True)
                for row in relay.records
                if (row.get("body") or {}).get("manifest", {}).get("transaction_id")
                == interrupted["transaction_id"]
            )
            == 1
        )
        result["partition_original"] = progress
        # Current initial pins reject an actual different CA-signed TLS leaf before HTTP.
        replacement = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        replacement.load_cert_chain(
            cases[1].certificates / "server.crt", cases[1].certificates / "server.key"
        )
        replacement.load_verify_locations(str(cases[0].certificates / "ca.crt"))
        replacement.verify_mode = ssl.CERT_REQUIRED
        # The actual wrong leaf uses another declared certificate from the same real CA.
        replacement.load_cert_chain(
            cases[1].certificates / "admin-next.crt", cases[1].certificates / "admin-next.key"
        )
        original_context = relay.replace_tls(replacement)
        wrong = next_manifest(manifest)
        wrong_path = "/v1/federation/transactions/" + wrong["transaction_id"]
        root.exchange("/v1/federation/transactions", wrong)

        def pin_refused() -> dict[str, Any] | None:
            """Observe the real original TLS failure without reissuing a probe or transaction."""
            view = root.exchange(wrong_path)
            for phases in view["original_intents"].values():
                intent = phases.get("probe", {})
                if "leaf pin refused" in (intent.get("error") or ""):
                    return view
            return None

        result["actual_leaf_pin_refusal"] = wait_for(
            pin_refused, "actual Federation TLS pin refusal", 30
        )
        assert [len(case.backend.jobs) for case in cases] == counts
        relay.replace_tls(original_context)
        result["private_archives"] = [archive(case, "original") for case in cases]
        for case in cases:
            save(case.root / "final-original-application.json", original_state(case))
        assert identities == {str(path): digest(path) for path in paths}
        result["online_cases_passed"] = True
    finally:
        relay.release_reply.set()
        relay.partition.clear()
        for case in cases:
            try:
                case.close()
            except Exception as error:
                cleanup_errors.append(str(error))
        relay.shutdown()
        relay.server_close()
        save(output / "actual-relay.json", relay.records)
        result["cleanup_errors"] = cleanup_errors
        save(output / "result.json", result)
    if cleanup_errors:
        raise ValueError("actual owned process cleanup failed: " + str(cleanup_errors))
    from federation_ha_recovery_processes import recover

    result["complete_recovery"] = recover(cases, output / "complete-recovery")
    result["passed"] = result["online_cases_passed"] and result["complete_recovery"]["passed"]
    save(output / "result.json", result)
    return result


def main() -> None:
    """Require current real products and preserve every original actual failure/effect evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "output",
        "supervisor",
        "storage_probe",
        "controller",
        "node",
        "previous_supervisor",
    ]:
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    if not run(
        args.output.absolute(),
        args.supervisor.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
        args.controller.resolve(strict=True),
        args.node.resolve(strict=True),
        args.previous_supervisor.resolve(strict=True),
    )["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
