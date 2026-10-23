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


def test_explicit_backing_preserves_unknown_and_immutable_prior_restore_chain(
    tmp_path: Path,
) -> None:
    """Repeated relocation appends backing provenance and keeps original operations."""
    source = tmp_path / "source"
    source.mkdir()
    stores = source / "stores"
    stores.mkdir()
    component = fixture(stores / "root")
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
            "profile": {"schema": content.PROFILE, "components": components},
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
        current = content.inventory(
            target / "stores/root",
            component | {"storage_schema": content.LAYOUT5},
            budget(),
            live=True,
        )
        assert (
            current["identity"] == original["identity"] and current["tables"] == original["tables"]
        )
        assert current["history"][:-1] == prior
        prior = list(current["history"])
        source = target
        component = copy.deepcopy(component) | {"storage_schema": content.LAYOUT5}
        components[0] = component
