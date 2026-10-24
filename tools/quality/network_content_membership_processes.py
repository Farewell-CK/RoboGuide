"""Independently check actual Snapshot learner and joint voter content admission with zero Nodes."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from check_replica_content_processes import digest
from replica_parent_adoption_processes import PayloadReadBarrier

if TYPE_CHECKING:
    from check_network_content_processes import Actor


def hold_snapshot_read(
    barrier: PayloadReadBarrier, receiver_sql: Path, epoch: dict[str, int]
) -> dict[str, Any]:
    """Check a blocked source open and unchanged vote beyond the actual election timeout."""
    deadline = time.monotonic() + 8
    while not barrier.signals:
        if time.monotonic() >= deadline:
            raise ValueError("actual Snapshot source did not encounter the native read barrier")
        time.sleep(0.01)
    started = time.monotonic()
    samples = []
    while time.monotonic() - started < 3.5:
        with closing(sqlite3.connect(receiver_sql.as_uri() + "?mode=ro", uri=True)) as sql:
            row = sql.execute("SELECT value FROM replica_v1_meta WHERE key='vote'").fetchone()
        if row is None:
            raise ValueError("actual returning voter has no durable upstream vote")
        actual = json.loads(row[0])
        assert actual["committed"] is True
        assert actual["leader_id"] == {"term": epoch["term"], "node_id": epoch["replica_id"]}
        samples.append({"elapsed_seconds": time.monotonic() - started, "actual_vote": actual})
        time.sleep(0.1)
    return {
        "actual_sigio": list(barrier.signals),
        "held_seconds": time.monotonic() - started,
        "vote_samples": samples,
    }


def membership(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Read committed membership and actual Raft entries without writer authority."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        raw: str = sql.execute(
            "SELECT value FROM replica_v1_meta WHERE key='membership'"
        ).fetchone()[0]
        entries = [json.loads(value) for (value,) in sql.execute("SELECT data FROM replica_v1_log")]
    return raw, entries


def run_learner_joint(
    actors: dict[int, Actor],
    configs: dict[int, dict[str, Any]],
    members: list[int],
    leader: int,
) -> dict[str, Any]:
    """Refuse corrupt learner bytes before joint, then admit repaired bytes through actual Raft."""
    learner = members[-1]
    original_voters = set(members[:-1])
    assert leader in original_voters
    original = actors[leader].ask({"action": "quorum"})
    assert set(original["complete_members"]) == original_voters
    compact = actors[leader].ask({"action": "compact-snapshot"})
    assert compact["actual_snapshot"] == compact["actual_purged"] == compact["original_applied"]
    actors[leader].ask({"action": "add-learner", "member": learner})
    root = Path(configs[learner]["content_root"])
    deadline = time.monotonic() + 20
    learner_rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        with closing(
            sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            learner_rows = [
                json.loads(raw) for (raw,) in sql.execute("SELECT record FROM content_objects")
            ]
        if learner_rows and len(learner_rows) == len(original["target"]["inputs"]):
            observed = actors[learner].ask(
                {"action": "observe", "target": actors[leader].ask({"action": "quorum"})["target"]},
                required=False,
            )
            if "observation" in observed:
                break
        time.sleep(0.05)
    else:
        status = actors[learner].ask({"action": "status"})
        positions = actors[leader].ask(
            {"action": "observe", "target": original["target"]}, required=False
        )
        raise ValueError(f"actual Snapshot learner missed target: {status}; source: {positions}")
    assert {json.dumps(row["content"], sort_keys=True) for row in learner_rows} == {
        json.dumps(key, sort_keys=True) for key in original["target"]["inputs"]
    }
    key = learner_rows[0]["content"]
    path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
    assert "sha256:" + digest(path) == key["content_digest"]
    payload = path.read_bytes()
    mode = path.stat().st_mode & 0o777
    parent = Path(configs[leader]["parent_sql"])
    before, entries_before = membership(parent)
    removed = next(member for member in original_voters if member != leader)
    desired = (original_voters - {removed}) | {learner}
    request = {
        "action": "change-voters",
        "voters": sorted(desired),
        "retain_removed_as_learners": True,
    }
    try:
        path.chmod(0o600)
        path.write_bytes(b"actual independent corruption after complete Snapshot reception")
        refusal = actors[leader].ask(request, required=False)
        after, entries_after = membership(parent)
        assert "error" in refusal
        assert before == after, "failed byte admission changed original committed voters"
        assert entries_before == entries_after, "failed byte admission queued a joint Raft entry"
        assert (
            set(actors[leader].ask({"action": "status"})["readiness"]["voters"]) == original_voters
        )
        assert set(actors[leader].ask({"action": "quorum"})["complete_members"]) == original_voters
    finally:
        path.write_bytes(payload)
        path.chmod(mode)
    assert "sha256:" + digest(path) == key["content_digest"]
    accepted = actors[leader].ask(request)
    assert set(accepted["voters"]) == desired
    current = actors[leader].ask({"action": "quorum"})
    assert set(current["complete_members"]) == desired
    assert current["target"]["batch_digest"] == original["target"]["batch_digest"]
    raw, entries = membership(parent)
    actual = json.loads(raw)["membership"]
    assert [set(group) for group in actual["configs"]] == [desired]
    joint = [
        entry
        for entry in entries
        if isinstance(entry.get("payload"), dict)
        and "Membership" in entry["payload"]
        and len(entry["payload"]["Membership"]["configs"]) == 2
    ]
    assert any(
        [set(group) for group in entry["payload"]["Membership"]["configs"]]
        == [original_voters, desired]
        for entry in joint
    ), "actual retained Raft log lacks the original old/new joint configuration"
    assert str(removed) in actual["nodes"]
    actors[removed].stop()
    independent = actors[leader].ask({"action": "quorum"})
    assert set(independent["complete_members"]) == desired
    return {
        "original_voters": sorted(original_voters),
        "proposed_voters": sorted(desired),
        "actual_snapshot": compact,
        "actual_corrupt_learner_refusal": refusal,
        "original_membership_and_log_unchanged_on_refusal": True,
        "actual_joint_entries": joint,
        "actual_new_voter_quorum": independent,
        "removed_voter_actual_wait": actors[removed].record["returncode"],
        "original_complete_candidate_unchanged": True,
    }
