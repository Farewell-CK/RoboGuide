"""Check actual Snapshot intents, content, recovery and nonrenewing failure."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import bundle
from roboguide_recovery.bundle import abort_snapshots, backup, reconcile_snapshots, restore, verify
from test_provider_recovery import owners, profile


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any], str]:
    """Upgrade actual four-store data explicitly, retaining one independently hashable CAS file."""
    store, uses, jobs, maps = owners(tmp_path, monkeypatch)
    identity = store.instance_id
    payload = Path(__file__).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    (store._content / checksum).write_bytes(payload)
    maps.close()
    jobs.close()
    uses.close()
    store.close()
    upgraded = importlib.import_module("roboguide_memory_provider.store").MemoryStore(
        store.root, "files", (tmp_path,), 1024 * 1024, enable_snapshot_references=True
    )
    assert upgraded.instance_id == identity
    assert upgraded._db.execute("PRAGMA user_version").fetchone() == (4,)
    upgraded.close()
    selected = profile(store.root) | {"schema": "roboguide.recovery-profile/v0.8"}
    return store.root, selected, checksum


def states(root: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Observe actual source authority independently from all recovery return values."""
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
        return (
            connection.execute(
                "SELECT operation_id,request_json,state,receipt_json "
                "FROM snapshot_operations ORDER BY operation_id"
            ).fetchall(),
            connection.execute(
                "SELECT reference_id,content_digest,generation,active "
                "FROM content_references WHERE kind='snapshot' ORDER BY reference_id"
            ).fetchall(),
        )


def test_snapshot_intent_precedes_copy_and_restore_retains_original_active_history(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual copying sees committed pins; restored SQL requires explicit reconciliation."""
    root, selected, checksum = source
    original_copy = bundle._copy
    inspected = []

    def inspect_before_copy(incoming: Path, target: Path, budget: bundle.Budget) -> tuple[str, int]:
        """Read independent original SQL before the actual CAS bytes leave their owner."""
        operations, pins = states(root)
        assert len(operations) == len(pins) == 1
        assert operations[0][2:] == ("active", None)
        assert pins[0][1:] == ("sha256:" + checksum, 1, 1)
        inspected.append(True)
        return original_copy(incoming, target, budget)

    monkeypatch.setattr(bundle, "_copy", inspect_before_copy)
    destination = tmp_path / "bundle"
    result = backup(selected, destination, "original")
    assert inspected
    operations, pins = states(root)
    assert operations[0][2] == "released" and pins[0][3] == 0
    monkeypatch.setattr(bundle, "_copy", original_copy)
    manifest = verify(destination, result["manifest_digest"])
    assert manifest["schema"] == "roboguide.recovery-bundle/v0.2"
    assert len(manifest["snapshot_operations"]) == 1
    receipt = restore(destination, result["manifest_digest"], tmp_path / "restored", "restore")
    assert receipt["schema"] == "roboguide.restore-receipt/v0.8"
    recovered = tmp_path / "restored/stores/provider"
    assert (recovered / "content" / checksum).read_bytes() == (
        root / "content" / checksum
    ).read_bytes()
    original_restored_operations, original_restored_pins = states(recovered)
    assert original_restored_operations[0][2:] == ("active", None)
    assert original_restored_pins[0][3] == 1
    recovery_profile = copy.deepcopy(selected)
    recovery_profile["components"][0]["root"] = str(recovered)
    reconciled = reconcile_snapshots(recovery_profile, destination, result["manifest_digest"])
    assert reconciled["copied_files"] == 0
    assert states(recovered)[0][0][1] == original_restored_operations[0][1]
    assert states(recovered)[0][0][2] == "released" and states(recovered)[1][0][3] == 0
    assert (
        reconcile_snapshots(recovery_profile, destination, result["manifest_digest"]) == reconciled
    )


def test_interrupted_copy_retains_pins_and_original_command_cannot_copy_again(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real copy boundary interruption remains Unknown until an explicit original writer fence."""
    root, selected, _ = source
    original_copy = bundle._copy

    def interrupt(incoming: Path, target: Path, budget: bundle.Budget) -> tuple[str, int]:
        """Interrupt before content copying while preserving original intent and partial SQL."""
        raise OSError("actual snapshot copy boundary interrupted")

    monkeypatch.setattr(bundle, "_copy", interrupt)
    destination = tmp_path / "interrupted"
    with pytest.raises(OSError, match="copy boundary interrupted"):
        backup(selected, destination, "original")
    before = states(root)
    assert before[0][0][2] == "active" and before[1][0][3] == 1
    assert (tmp_path / "interrupted.partial-original").is_dir()
    monkeypatch.setattr(bundle, "_copy", original_copy)
    with pytest.raises(ValueError, match="without another copy"):
        backup(selected, destination, "original")
    assert states(root) == before
    aborted = abort_snapshots(selected, destination, "original")
    assert aborted["copied_files"] == 0 and aborted["partial_evidence_preserved"] is True
    assert states(root)[0][0][2] == "aborted" and states(root)[1][0][3] == 0
    assert abort_snapshots(selected, destination, "original") == aborted
    with pytest.raises(ValueError, match="without another copy"):
        backup(selected, destination, "original")
    assert (tmp_path / "interrupted.partial-original").is_dir()


def test_published_copy_reconciles_sql_failure_without_recopy_or_budget_change(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Final SQL failure retains the published bundle and pins for original reconciliation."""
    root, selected, _ = source
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
        connection.execute(
            "CREATE TRIGGER fail_completion BEFORE UPDATE ON snapshot_operations "
            "WHEN NEW.state='released' BEGIN SELECT RAISE(ABORT,'snapshot SQL fault'); END"
        )
        connection.commit()
    destination = tmp_path / "published"
    with pytest.raises(sqlite3.IntegrityError, match="snapshot SQL fault"):
        backup(selected, destination, "original")
    before = states(root)
    assert before[0][0][2] == "active" and before[1][0][3] == 1
    captured = {
        path: (path.stat().st_ino, path.stat().st_mtime_ns)
        for path in destination.rglob("*")
        if path.is_file()
    }
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
        connection.execute("DROP TRIGGER fail_completion")
        connection.commit()
    result = backup(selected, destination, "original")
    verify(destination, result["manifest_digest"])
    assert states(root)[0][0][1] == before[0][0][1]
    assert states(root)[0][0][2] == "released" and states(root)[1][0][3] == 0
    assert all(
        (path.stat().st_ino, path.stat().st_mtime_ns) == identity
        for path, identity in captured.items()
    )


def test_legacy_profile_and_default_provider_cannot_open_snapshot_authority(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Actual v4 authority cannot be silently read through a legacy writer or recovery profile."""
    root, selected, _ = source
    with pytest.raises(ValueError, match="explicit snapshot references"):
        importlib.import_module("roboguide_memory_provider.store").MemoryStore(
            root, "files", (tmp_path,), 1024 * 1024
        )
    legacy = selected | {"schema": "roboguide.recovery-profile/v0.7"}
    with pytest.raises(ValueError, match="v3 composite writer marker"):
        backup(legacy, tmp_path / "legacy", "legacy")
    assert not (tmp_path / "legacy").exists()


def test_snapshot_descriptor_cannot_be_relabelled_to_another_owner(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Even a newly pinned manifest cannot change the original provider stored in bundled SQL."""
    _, selected, _ = source
    destination = tmp_path / "bundle"
    backup(selected, destination, "original")
    path = destination / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["snapshot_operations"][0]["provider_instance_id"] = "another-instance"
    raw = bundle.canonical(manifest)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="different actual provider"):
        verify(destination, bundle.digest(raw))


def test_snapshot_original_command_cannot_change_destination_or_profile(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """The actual journal's unique command prevents a new operation with changed parameters."""
    root, selected, _ = source
    backup(selected, tmp_path / "original-bundle", "original")
    before = states(root)
    with pytest.raises(ValueError, match="command identity conflicts"):
        backup(selected, tmp_path / "different-bundle", "original")
    changed = selected | {"revision": 2}
    with pytest.raises(ValueError, match="command identity conflicts"):
        backup(changed, tmp_path / "changed-profile", "original")
    assert states(root) == before
    assert not (tmp_path / "different-bundle").exists()
    assert not (tmp_path / "changed-profile").exists()


def test_missing_snapshot_authority_columns_refuse_before_copy(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """A renamed mandatory column is corruption rather than an empty original operation set."""
    root, selected, _ = source
    with closing(sqlite3.connect(root / "memory.sqlite3")) as connection:
        connection.execute(
            "ALTER TABLE snapshot_operations RENAME COLUMN command_id TO old_command_id"
        )
        connection.commit()
    with pytest.raises(ValueError, match="authority columns are missing"):
        backup(selected, tmp_path / "invalid", "original")
    with pytest.raises(ValueError, match="authority columns are unavailable"):
        importlib.import_module("roboguide_memory_provider.store").MemoryStore(
            root, "files", (tmp_path,), 1024 * 1024, enable_snapshot_references=True
        )
    assert not (tmp_path / "invalid").exists()
