"""Reject corrupt Root DTO/file fixtures; native gates prove managed processes."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import replica_content as content
from roboguide_recovery.bundle import Budget
from roboguide_recovery.replica_content_bundle import _require_bytes, _required, bind_restored


def budget() -> Budget:
    """Bound this actual file checker; synthetic stored budget values confer no process evidence."""
    return Budget({"max_files": 10000, "max_bytes": 64 * 1024**2, "budget_seconds": 30})


def fixture(root: Path) -> dict[str, Any]:
    """Create files/SQL with synthetic copy/Unknown DTOs and original clocks."""
    root.mkdir()
    (root / "content-v1").mkdir()
    (root / "staging-v1").mkdir()
    (root / ".writer.lock").touch()
    sql_path = root / "content.sqlite3"
    with closing(sqlite3.connect(sql_path)) as sql:
        for head, (table, column) in content.HEADS.items():
            extra = "request_digest TEXT NOT NULL," if head == "stream_ids" else ""
            sql.execute(
                f"CREATE TABLE {table}({column} TEXT PRIMARY KEY,{extra}record TEXT NOT NULL)"
            )
        sql.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        sql.execute("PRAGMA user_version=4")
        sql.commit()
    actual = content._backing(root)
    identity: dict[str, Any] = {
        "schema": "roboguide.replica-content-storage/v0.1",
        "profile_identity": "a" * 64,
        "member": 1,
        "store_owner": "replica-content:" + "c" * 64,
        **actual,
    }
    for name in (".content-creation.json", ".retention-owner.json", "blobs"):
        (root / name).write_text(json.dumps(identity))
    payload = b"explicit contract-only file bytes; not a robot or process outcome"
    key: dict[str, Any] = {
        "source_owner": "artifact-store:" + "b" * 64,
        "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }
    path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
    path.parent.mkdir()
    path.write_bytes(payload)
    clock = {
        "boot_id": "00000000-0000-0000-0000-000000000000",
        "namespace": [1, 1],
        "pid_namespace": [1, 2],
        "admitted_ms": 100,
        "duration_ms": 1000,
        "pid": 1,
        "start_ticks": 1,
    }
    intent = {
        "schema": "roboguide.replica-content-copy/v0.1",
        "command_id": "contract-copy",
        "receiver": identity,
        "content": key,
        "source": {"owner": key["source_owner"], "device": 1, "inode": 1},
        "authority_digest": "d" * 64,
        "budget": clock,
    }
    receipt = {
        "schema": "roboguide.replica-content-receipt/v0.1",
        "request_digest": content._digest(intent),
        "command_id": intent["command_id"],
        "content": key,
        "store_owner": identity["store_owner"],
        "member": 1,
        "profile_identity": identity["profile_identity"],
        "file_device": path.stat().st_dev,
        "file_inode": path.stat().st_ino,
        "completed_ms": 200,
    }
    reader = {
        "intent": {
            "schema": "roboguide.replica-content-reader/v0.1",
            "reader_id": "contract-unknown",
            "receiver": identity,
            "content": key,
            "authority_digest": "e" * 64,
            "budget": clock,
        },
        "receipt": None,
        "process_end": None,
    }
    tables: dict[str, dict[str, Any]] = {table: {} for table, _ in content.HEADS.values()}
    tables["content_operations"][content._digest(intent["command_id"])] = {
        "intent": intent,
        "receipt": receipt,
        "process_end": None,
    }
    tables["content_objects"][content._digest(key)] = receipt
    tables["content_readers"][content._digest("contract-unknown")] = reader
    for kind, command, authority in (
        ("receive", "contract-copy", "d" * 64),
        ("reader", "contract-unknown", "e" * 64),
    ):
        identifier = kind + ":" + content._digest(command)
        tables["content_references"][identifier] = {
            "reference": {
                "reference_id": identifier,
                "kind": kind,
                "content": key,
                "authority_digest": authority,
                "generation": 1,
            },
            "active": True,
            "release_digest": None,
        }
    with closing(sqlite3.connect(sql_path)) as sql:
        with sql:
            for head, (table, _) in content.HEADS.items():
                for identifier, record in tables[table].items():
                    sql.execute(
                        f"INSERT INTO {table} VALUES (?,?)", (identifier, json.dumps(record))
                    )
                sql.execute(
                    "INSERT INTO metadata VALUES (?,?)", (head, json.dumps(sorted(tables[table])))
                )
            for name, value in {
                "layout": content.LAYOUT4,
                "identity": json.dumps(identity),
                "legacy_producer_ids": "[]",
            }.items():
                sql.execute("INSERT INTO metadata VALUES (?,?)", (name, value))
    return {
        "storage_schema": content.LAYOUT4,
        "component_id": "root",
        "kind": "replica-content",
        "depends_on": ["parent"],
        "database": None,
        "trees": [],
        "root": str(root),
    }


@pytest.mark.parametrize("corrupt", ["head", "reader", "pin", "receiver", "clock", "metadata"])
def test_original_unknown_cannot_lose_inventory_pin_identity_or_clock(
    tmp_path: Path, corrupt: str
) -> None:
    """Corruption cannot turn an original unknown reader into permission or an empty history."""
    root = tmp_path / "root"
    component = fixture(root)
    original = content.inventory(root, component, budget(), live=True)
    assert next(iter(original["tables"]["content_readers"].values()))["receipt"] is None
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            if corrupt == "head":
                sql.execute("DELETE FROM metadata WHERE key='reader_ids'")
            elif corrupt == "reader":
                sql.execute("DELETE FROM content_readers")
            elif corrupt == "pin":
                sql.execute("DELETE FROM content_references WHERE reference_id LIKE 'reader:%'")
            elif corrupt == "metadata":
                sql.execute("ALTER TABLE metadata RENAME TO old_metadata")
                sql.execute("CREATE TABLE metadata(key TEXT,value TEXT NOT NULL)")
                sql.execute("INSERT INTO metadata SELECT * FROM old_metadata")
                sql.execute("DROP TABLE old_metadata")
            else:
                key, raw = sql.execute("SELECT * FROM content_readers").fetchone()
                value = json.loads(raw)
                if corrupt == "receiver":
                    value["intent"]["receiver"]["root_inode"] += 1
                else:
                    value["intent"]["budget"]["duration_ms"] = 0
                sql.execute(
                    "UPDATE content_readers SET record=? WHERE reader_id=?",
                    (json.dumps(value), key),
                )
    with pytest.raises(ValueError):
        content.inventory(root, component, budget())


def test_original_delete_journal_cannot_be_implicitly_repaired_by_backup(tmp_path: Path) -> None:
    """A complete-looking WAL Root is refused rather than silently relabelled as native DELETE."""
    root = tmp_path / "root"
    component = fixture(root)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        assert sql.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    with pytest.raises(ValueError, match="native DELETE"):
        content.inventory(root, component, budget(), live=True)


def test_active_bytes_require_same_root_exact_qualified_owner(tmp_path: Path) -> None:
    """Equal digest in another owner/member cannot satisfy an original active pin."""
    component = fixture(tmp_path / "root")
    original = content.inventory(tmp_path / "root", component, budget())
    required = _required(original)
    owner, checksum, size = next(iter(required))
    expected = "stores/root/content-v1/" + owner[15:] + "/" + checksum[7:]
    files = {expected: {"content_digest": checksum, "byte_size": size}}
    objects = original["tables"]["content_objects"]
    _require_bytes("root", required, files, objects)
    with pytest.raises(ValueError, match="object receipt"):
        _require_bytes("root", required, files, {})
    for changed in (
        expected.replace(owner[15:], "f" * 64),
        expected.replace("stores/root/", "stores/other/"),
    ):
        with pytest.raises(ValueError, match="same-Root"):
            _require_bytes("root", required, {changed: files[expected]}, objects)


@pytest.mark.parametrize("generation", [4, 6, 7, 8])
def test_explicit_backing_preserves_unknown_and_immutable_prior_restore_chain(
    tmp_path: Path,
    generation: int,
) -> None:
    """Repeated relocation appends backing provenance and keeps original operations."""
    source = tmp_path / "source"
    source.mkdir()
    stores = source / "stores"
    stores.mkdir()
    component = (
        staging_fixture(stores / "root")
        if generation == 8
        else gc_fixture(stores / "root", delete_bytes=True)
        if generation == 7
        else snapshot_fixture(stores / "root", ready=True)
        if generation == 6
        else fixture(stores / "root")
    )
    parent = stores / "parent"
    parent.mkdir()
    (parent / "replica.sqlite3").write_bytes(b"explicit storage binding contract fixture")
    components = [
        component,
        {"component_id": "parent", "kind": "replica", "database": "replica.sqlite3"},
    ]
    original = content.inventory(stores / "root", component, budget())
    prior: list[str] = []
    for iteration in range(2):
        target = tmp_path / f"target-{iteration}"
        shutil.copytree(source, target)
        parent_info = (source / "stores/parent/replica.sqlite3").stat()
        manifest = {
            "profile": {
                "schema": content.STAGING_PROFILE
                if generation == 8
                else content.GC_PROFILE
                if generation == 7
                else content.SNAPSHOT_PROFILE
                if generation == 6
                else content.PROFILE,
                "components": components,
            },
            "replica_content_sources": [
                {
                    "component_id": "root",
                    "parent_id": "parent",
                    "source_backing": content._backing(source / "stores/root")
                    | {"parent_device": parent_info.st_dev, "parent_inode": parent_info.st_ino},
                }
            ],
            "files": [
                {
                    "path": "stores/root/content.sqlite3",
                    "content_digest": "sha256:"
                    + hashlib.sha256(
                        (source / "stores/root/content.sqlite3").read_bytes()
                    ).hexdigest(),
                }
            ],
        }
        receipts = bind_restored(
            target, manifest, "sha256:" + "a" * 64, f"restore-{iteration}", budget()
        )
        assert len(receipts) == 1
        restored_component = component | (
            {
                "storage_schema": f"roboguide.replica-content-layout/v0.{generation}",
                "restore_binding_digest": receipts[0]["binding_digest"],
            }
            if generation >= 6
            else {"storage_schema": content.LAYOUT5}
        )
        current = content.inventory(
            target / "stores/root",
            restored_component,
            budget(),
            live=True,
        )
        assert (
            current["identity"] == original["identity"] and current["tables"] == original["tables"]
        )
        assert current["history"][:-1] == prior
        prior = list(current["history"])
        source = target
        component = copy.deepcopy(restored_component)
        components[0] = component


def snapshot_fixture(root: Path, *, ready: bool) -> dict[str, Any]:
    """Extend actual local SQL with explicitly synthetic Root6 source/receive evidence."""
    component = fixture(root)
    original = content.inventory(root, component, budget(), live=True)
    identity = original["identity"]
    receipt = next(iter(original["tables"]["content_objects"].values()))
    key = (
        receipt["content"]
        if ready
        else {
            "source_owner": receipt["content"]["source_owner"],
            "content_digest": "sha256:" + "f" * 64,
            "byte_size": 1,
        }
    )
    membership = {
        "log_id": {"leader_id": {"term": 3, "node_id": 2}, "index": 8},
        "membership": {
            "configs": [[2]],
            "nodes": {
                "1": {"addr": "127.0.0.1:1", "content_store_owner": identity["store_owner"]},
                "2": {"addr": "127.0.0.1:2", "content_store_owner": "replica-content:" + "b" * 64},
            },
        },
    }
    header: dict[str, Any] = {
        "schema": "roboguide.snapshot-content-header/v0.1",
        "source_member": 2,
        "source_store_owner": "replica-content:" + "b" * 64,
        "source_leaf": "sha256:" + "d" * 64,
        "meta": {
            "last_log_id": {"leader_id": {"term": 3, "node_id": 2}, "index": 8},
            "last_membership": membership,
            "snapshot_id": "8-1-" + "f" * 64,
        },
        "payload_digest": "f" * 64,
        "target": {
            "profile_identity": identity["profile_identity"],
            "epoch": {"term": 3, "replica_id": 2},
            "revision": 1,
            "checkpoint_digest": "e" * 64,
            "batch_digest": "f" * 64,
            "membership": membership,
            "member_stores": {"2": "replica-content:" + "b" * 64},
            "inputs": [key],
        },
    }
    command = "snapshot-receive:" + content._digest(
        [identity["profile_identity"], 2, 1, header["meta"]["snapshot_id"], "f" * 64]
    )
    intent = {
        "command_id": command,
        "receiver": identity,
        "header": header,
        "budget": next(iter(original["tables"]["content_operations"].values()))["intent"]["budget"],
    }
    identifier = content._digest(command)
    row = {
        "intent": intent,
        "ready": {
            "intent_digest": content._digest(intent),
            "inputs": [receipt],
            "verified_files": [[receipt["file_device"], receipt["file_inode"]]],
            "completed_ms": 250,
        }
        if ready
        else None,
        "retirement": None,
    }
    pin = "snapshot-receive:" + identifier + ":" + content._digest(key)
    reference = {
        "reference": {
            "reference_id": pin,
            "kind": "snapshot-receive",
            "content": key,
            "authority_digest": content._digest(intent),
            "generation": 1,
        },
        "active": True,
        "release_digest": None,
    }
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            sql.execute(
                "CREATE TABLE content_snapshot_receives("
                "operation_id TEXT PRIMARY KEY,record TEXT NOT NULL)"
            )
            sql.execute(
                "INSERT INTO content_snapshot_receives VALUES (?,?)", (identifier, json.dumps(row))
            )
            sql.execute("INSERT INTO content_references VALUES (?,?)", (pin, json.dumps(reference)))
            heads = json.loads(
                sql.execute("SELECT value FROM metadata WHERE key='reference_ids'").fetchone()[0]
            )
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='reference_ids'",
                (json.dumps(sorted([*heads, pin])),),
            )
            sql.execute(
                "INSERT INTO metadata VALUES ('snapshot_receive_ids',?)",
                (json.dumps([identifier]),),
            )
            sql.execute("INSERT INTO metadata VALUES ('backing_mode','original')")
            sql.execute("UPDATE metadata SET value=? WHERE key='layout'", (content.LAYOUT6,))
            sql.execute("PRAGMA user_version=6")
    return component | {"storage_schema": content.LAYOUT6, "restore_binding_digest": None}


@pytest.mark.parametrize("ready", [False, True])
def test_snapshot_receive_required_bytes_follow_original_readiness(
    tmp_path: Path, ready: bool
) -> None:
    """Incomplete Snapshot pins retain Unknown; completed prerequisites require same-Root bytes."""
    root = tmp_path / "root"
    component = snapshot_fixture(root, ready=ready)
    original = content.inventory(root, component, budget(), live=True)
    row = next(iter(original["tables"]["content_snapshot_receives"].values()))
    key = content._content(row["intent"]["header"]["target"]["inputs"][0])
    assert (key in _required(original)) is ready
    if ready:
        with pytest.raises(ValueError, match="same-Root"):
            _require_bytes("root", _required(original), {}, original["tables"]["content_objects"])


@pytest.mark.parametrize("corrupt", ["head", "pin", "source", "clock", "receipt", "mode"])
def test_snapshot_receive_original_corruption_refuses(tmp_path: Path, corrupt: str) -> None:
    """Independent receive provenance and mandatory heads cannot disappear through recovery."""
    root = tmp_path / "root"
    component = snapshot_fixture(root, ready=True)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            if corrupt == "head":
                sql.execute("DELETE FROM metadata WHERE key='snapshot_receive_ids'")
            elif corrupt == "mode":
                sql.execute("DELETE FROM metadata WHERE key='backing_mode'")
            elif corrupt == "pin":
                sql.execute(
                    "DELETE FROM content_references WHERE reference_id LIKE 'snapshot-receive:%'"
                )
            else:
                identifier, raw = sql.execute(
                    "SELECT operation_id,record FROM content_snapshot_receives"
                ).fetchone()
                row = json.loads(raw)
                if corrupt == "source":
                    row["intent"]["header"]["source_member"] = 1
                elif corrupt == "clock":
                    row["intent"]["budget"]["duration_ms"] = 0
                else:
                    row["ready"]["inputs"][0]["file_inode"] += 1
                sql.execute(
                    "UPDATE content_snapshot_receives SET record=? WHERE operation_id=?",
                    (json.dumps(row), identifier),
                )
    with pytest.raises(ValueError):
        content.inventory(root, component, budget(), live=True)


def root7_fixture(root: Path) -> dict[str, Any]:
    """Extend the explicit contract fixture with empty complete Root7 inventories."""
    component = fixture(root)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            for table, column, head in (
                ("content_snapshot_receives", "operation_id", "snapshot_receive_ids"),
                ("content_gc_commands", "operation_id", "gc_ids"),
                ("content_tombstones", "content_key", "tombstone_keys"),
                ("content_stream_retirements", "stream_id", "stream_retirement_ids"),
            ):
                sql.execute(f"CREATE TABLE {table}({column} TEXT PRIMARY KEY,record TEXT NOT NULL)")
                sql.execute("INSERT INTO metadata VALUES (?,?)", (head, "[]"))
            sql.execute("INSERT INTO metadata VALUES ('backing_mode','original')")
            sql.execute("UPDATE metadata SET value=? WHERE key='layout'", (content.LAYOUT7,))
            sql.execute("PRAGMA user_version=7")
    return component | {"storage_schema": content.LAYOUT7, "restore_binding_digest": None}


def gc_fixture(root: Path, *, delete_bytes: bool) -> dict[str, Any]:
    """Create explicitly synthetic closed reference/Root7 Unknown DTOs over actual test files."""
    component = root7_fixture(root)
    original = content.inventory(root, component, budget(), live=True)
    receipt = next(iter(original["tables"]["content_objects"].values()))
    key = receipt["content"]
    path = root / "content-v1" / key["source_owner"][15:] / key["content_digest"][7:]
    refs = {
        identifier: value | {"active": False, "release_digest": "f" * 64}
        for identifier, value in original["tables"]["content_references"].items()
        if value["reference"]["kind"] == "receive"
    }
    intent = {
        "schema": "roboguide.replica-content-gc/v0.1",
        "command_id": "contract-gc",
        "receiver": original["identity"],
        "budget": next(iter(original["tables"]["content_operations"].values()))["intent"]["budget"],
        "items": [
            {
                "receipt": receipt,
                "reference_digest": content._digest(refs),
                "file": {"device": path.stat().st_dev, "inode": path.stat().st_ino},
                "prior_tombstone": None,
            }
        ],
    }
    command = content._digest(intent["command_id"])
    tombstone = content._digest(key)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            sql.execute("DELETE FROM content_readers")
            sql.execute("DELETE FROM content_references")
            for identifier, value in refs.items():
                sql.execute(
                    "INSERT INTO content_references VALUES (?,?)", (identifier, json.dumps(value))
                )
            sql.execute("UPDATE metadata SET value='[]' WHERE key='reader_ids'")
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='reference_ids'", (json.dumps(sorted(refs)),)
            )
            sql.execute(
                "INSERT INTO content_gc_commands VALUES (?,?)",
                (command, json.dumps({"intent": intent, "outcomes": [None]})),
            )
            sql.execute(
                "INSERT INTO content_tombstones VALUES (?,?)",
                (
                    tombstone,
                    json.dumps(
                        {
                            "content": key,
                            "command_id": intent["command_id"],
                            "intent_digest": content._digest(intent),
                        }
                    ),
                ),
            )
            sql.execute("UPDATE metadata SET value=? WHERE key='gc_ids'", (json.dumps([command]),))
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='tombstone_keys'", (json.dumps([tombstone]),)
            )
    if delete_bytes:
        path.unlink()
    return component


@pytest.mark.parametrize("delete_bytes", [False, True])
def test_gc_original_unknown_never_requires_deleted_historical_bytes(
    tmp_path: Path, delete_bytes: bool
) -> None:
    """Original Unknown survives either real file state without requiring retired history bytes."""
    root = tmp_path / "root"
    component = gc_fixture(root, delete_bytes=delete_bytes)
    original = content.inventory(root, component, budget(), live=True)
    assert next(iter(original["tables"]["content_gc_commands"].values()))["outcomes"] == [None]
    assert _required(original) == set()


@pytest.mark.parametrize(
    "corrupt", ["gc-head", "fence-head", "fence", "clock", "receipt", "reference", "outcome"]
)
def test_gc_missing_originals_or_active_reference_conflict_refuses(
    tmp_path: Path, corrupt: str
) -> None:
    """Require each original command, tombstone, reference and exact effect bound independently."""
    root = tmp_path / "root"
    component = gc_fixture(root, delete_bytes=True)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            if corrupt in {"gc-head", "fence-head"}:
                sql.execute(
                    "DELETE FROM metadata WHERE key=?",
                    ("gc_ids" if corrupt == "gc-head" else "tombstone_keys",),
                )
            elif corrupt == "fence":
                sql.execute("DELETE FROM content_tombstones")
            elif corrupt == "reference":
                identifier, raw = sql.execute("SELECT * FROM content_references").fetchone()
                value = json.loads(raw) | {"active": True, "release_digest": None}
                sql.execute(
                    "UPDATE content_references SET record=? WHERE reference_id=?",
                    (json.dumps(value), identifier),
                )
            else:
                identifier, raw = sql.execute("SELECT * FROM content_gc_commands").fetchone()
                value = json.loads(raw)
                if corrupt == "clock":
                    value["intent"]["budget"]["duration_ms"] = 0
                elif corrupt == "receipt":
                    value["intent"]["items"][0]["receipt"]["file_inode"] += 1
                else:
                    value["outcomes"] = [{"state": "deleted", "observed_ms": 1101}]
                sql.execute(
                    "UPDATE content_gc_commands SET record=? WHERE operation_id=?",
                    (json.dumps(value), identifier),
                )
    with pytest.raises(ValueError):
        content.inventory(root, component, budget(), live=True)


def stream_retirement_fixture(root: Path, *, closed: bool) -> dict[str, Any]:
    """Build synthetic stream-close/end DTOs; native gates separately prove actual workers."""
    component = root7_fixture(root)
    original = content.inventory(root, component, budget(), live=True)
    identity = original["identity"]
    copied = next(iter(original["tables"]["content_operations"].values()))
    key = copied["intent"]["content"]
    target = {
        "profile_identity": identity["profile_identity"],
        "epoch": {"term": 1, "replica_id": 1},
        "revision": 1,
        "checkpoint_digest": "a" * 64,
        "batch_digest": "b" * 64,
        "membership": {
            "log_id": {"leader_id": {"term": 1, "node_id": 1}, "index": 1},
            "membership": {
                "configs": [[1]],
                "nodes": {
                    "1": {"addr": "127.0.0.1:1", "content_store_owner": identity["store_owner"]}
                },
            },
        },
        "member_stores": {"1": identity["store_owner"]},
        "inputs": [key],
    }
    intent = {
        "schema": "roboguide.replica-content-stream/v0.1",
        "command_id": "contract-stream",
        "receiver": identity,
        "target": target,
        "content": key,
        "origin": {"kind": "artifact", "writer": copied["intent"]["source"]},
        "budget": copied["intent"]["budget"],
    }
    stream = {
        "intent": intent,
        "request_digest": content._digest(intent),
        "receipt": None,
        "worker_closed_ms": 200 if closed else None,
    }
    clock = copied["intent"]["budget"] | {"pid": 2, "start_ticks": 2, "admitted_ms": 300}
    retirement = {
        "schema": "roboguide.replica-content-stream-retirement/v0.1",
        "receiver": identity,
        "original": stream,
        "budget": clock,
        "retired_ms": 350,
        "process_end": None
        if closed
        else {
            "original_boot_id": intent["budget"]["boot_id"],
            "original_pid": intent["budget"]["pid"],
            "original_start_ticks": intent["budget"]["start_ticks"],
            "observer": clock | {"admitted_ms": 301},
            "reason": "original-process-absent",
            "replacement_start_ticks": None,
        },
    }
    identifier = content._digest(intent["command_id"])
    pin = "stream:" + identifier
    reference = {
        "reference": {
            "reference_id": pin,
            "kind": "stream",
            "content": key,
            "authority_digest": target["batch_digest"],
            "generation": 1,
        },
        "active": False,
        "release_digest": content._digest(retirement),
    }
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            sql.execute(
                "INSERT INTO content_streams VALUES (?,?,?)",
                (identifier, stream["request_digest"], json.dumps(stream)),
            )
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='stream_ids'", (json.dumps([identifier]),)
            )
            sql.execute(
                "INSERT INTO content_stream_retirements VALUES (?,?)",
                (identifier, json.dumps(retirement)),
            )
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='stream_retirement_ids'",
                (json.dumps([identifier]),),
            )
            sql.execute("INSERT INTO content_references VALUES (?,?)", (pin, json.dumps(reference)))
            sql.execute(
                "UPDATE metadata SET value=? WHERE key='reference_ids'",
                (json.dumps(sorted([*original["tables"]["content_references"], pin])),),
            )
    return component


@pytest.mark.parametrize("closed", [False, True])
def test_stream_retirement_keeps_original_unknown_and_independent_close_or_end(
    tmp_path: Path, closed: bool
) -> None:
    """Exact original closure is independent of byte completion and survives recovery inspection."""
    root = tmp_path / "root"
    component = stream_retirement_fixture(root, closed=closed)
    original = content.inventory(root, component, budget(), live=True)
    assert next(iter(original["tables"]["content_streams"].values()))["receipt"] is None


@pytest.mark.parametrize("corrupt", ["head", "original", "observer", "clock", "pin"])
def test_stream_retirement_cannot_borrow_another_original_or_process(
    tmp_path: Path, corrupt: str
) -> None:
    """Missing original rows, different observers or renewed decisions cannot authorize release."""
    root = tmp_path / "root"
    component = stream_retirement_fixture(root, closed=False)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            if corrupt == "head":
                sql.execute("DELETE FROM metadata WHERE key='stream_retirement_ids'")
            elif corrupt == "pin":
                sql.execute("DELETE FROM content_stream_retirements")
                sql.execute("UPDATE metadata SET value='[]' WHERE key='stream_retirement_ids'")
            else:
                identifier, raw = sql.execute("SELECT * FROM content_stream_retirements").fetchone()
                row = json.loads(raw)
                if corrupt == "original":
                    row["original"]["intent"]["budget"]["duration_ms"] += 1
                elif corrupt == "observer":
                    row["process_end"]["observer"]["pid"] += 1
                else:
                    row["retired_ms"] = 1301
                sql.execute(
                    "UPDATE content_stream_retirements SET record=? WHERE stream_id=?",
                    (json.dumps(row), identifier),
                )
                reference_id, raw_pin = sql.execute(
                    "SELECT * FROM content_references WHERE reference_id LIKE 'stream:%'"
                ).fetchone()
                reference = json.loads(raw_pin) | {"release_digest": content._digest(row)}
                sql.execute(
                    "UPDATE content_references SET record=? WHERE reference_id=?",
                    (json.dumps(reference), reference_id),
                )
    with pytest.raises(ValueError):
        content.inventory(root, component, budget(), live=True)


def staging_fixture(root: Path) -> dict[str, Any]:
    """Add synthetic closed Root8 DTOs over actual partial files; this proves no process facts."""
    component = stream_retirement_fixture(root, closed=False)
    original = content.inventory(root, component, budget(), live=True)
    stream_id, stream = next(iter(original["tables"]["content_streams"].items()))
    retirement = original["tables"]["content_stream_retirements"][stream_id]
    staging = root / "staging-v1" / ("stream-" + stream_id)
    staging.write_bytes(b"actual partial bytes")
    file = staging.stat()
    intent = {
        "schema": "roboguide.replica-staging-gc-intent/v0.1",
        "command_id": "contract-staging-cleanup",
        "receiver": original["identity"],
        "budget": retirement["budget"] | {"admitted_ms": 500},
        "items": [
            {
                "target": {"kind": "stream", "command_id": stream["intent"]["command_id"]},
                "ownership": {
                    "content": stream["intent"]["content"],
                    "source_digest": content._digest(stream),
                    "release_digest": content._digest(retirement),
                    "leaf": "stream-" + stream_id,
                },
                "file": {"device": file.st_dev, "inode": file.st_ino, "byte_size": file.st_size},
            }
        ],
    }
    command = content._digest(intent["command_id"])
    item_id = content._digest([command, 0])
    item = {
        "operation_id": command,
        "ordinal": 0,
        "intent_digest": content._digest(intent),
        "outcome": None,
    }
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            for table, column, head, key, row in (
                ("content_staging_gc_commands", "operation_id", "staging_gc_ids", command, intent),
                ("content_staging_gc_items", "item_id", "staging_gc_item_ids", item_id, item),
            ):
                sql.execute(f"CREATE TABLE {table}({column} TEXT PRIMARY KEY,record TEXT NOT NULL)")
                sql.execute(f"INSERT INTO {table} VALUES (?,?)", (key, json.dumps(row)))
                sql.execute("INSERT INTO metadata VALUES (?,?)", (head, json.dumps([key])))
            sql.execute("UPDATE metadata SET value=? WHERE key='layout'", (content.LAYOUT8,))
            sql.execute("PRAGMA user_version=8")
    return component | {"storage_schema": content.LAYOUT8}


@pytest.mark.parametrize("missing", [False, True])
def test_staging_original_unknown_ignores_current_partial_absence(
    tmp_path: Path, missing: bool
) -> None:
    """Recovery neither copies a private partial nor invents an outcome from restored absence."""
    root = tmp_path / "root"
    component = staging_fixture(root)
    before = content.inventory(root, component, budget(), live=True)
    if missing:
        next((root / "staging-v1").iterdir()).unlink()
    after = content.inventory(root, component, budget(), live=True)
    assert after == before
    assert next(iter(after["tables"]["content_staging_gc_items"].values()))["outcome"] is None
    assert next(iter(after["tables"]["content_streams"].values()))["receipt"] is None
    assert _required(after), "independent live reader must still retain complete CAS"


@pytest.mark.parametrize(
    "corrupt", ["commands-head", "items-head", "item", "source", "release", "ordinal", "clock"]
)
def test_staging_missing_or_borrowed_original_evidence_refuses(
    tmp_path: Path, corrupt: str
) -> None:
    """Exact inventory, source ownership and per-item clock are mandatory even after an unlink."""
    root = tmp_path / "root"
    component = staging_fixture(root)
    with closing(sqlite3.connect(root / "content.sqlite3")) as sql:
        with sql:
            if corrupt in {"commands-head", "items-head"}:
                head = "staging_gc_ids" if corrupt == "commands-head" else "staging_gc_item_ids"
                sql.execute("DELETE FROM metadata WHERE key=?", (head,))
            elif corrupt == "item":
                sql.execute("DELETE FROM content_staging_gc_items")
                sql.execute("UPDATE metadata SET value='[]' WHERE key='staging_gc_item_ids'")
            elif corrupt in {"source", "release"}:
                key, raw = sql.execute("SELECT * FROM content_staging_gc_commands").fetchone()
                row = json.loads(raw)
                row["items"][0]["ownership"][corrupt + "_digest"] = "0" * 64
                sql.execute(
                    "UPDATE content_staging_gc_commands SET record=? WHERE operation_id=?",
                    (json.dumps(row), key),
                )
            else:
                key, raw = sql.execute("SELECT * FROM content_staging_gc_items").fetchone()
                row = json.loads(raw)
                if corrupt == "ordinal":
                    row["ordinal"] = True
                else:
                    row["outcome"] = {
                        "state": "deleted",
                        "observed_ms": 1501,
                        "observed_digest": "sha256:" + "a" * 64,
                    }
                sql.execute(
                    "UPDATE content_staging_gc_items SET record=? WHERE item_id=?",
                    (json.dumps(row), key),
                )
    with pytest.raises(ValueError):
        content.inventory(root, component, budget(), live=True)
