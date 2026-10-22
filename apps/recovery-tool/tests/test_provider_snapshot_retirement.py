"""Check original provider Snapshot closure across actual cold-restored files, never devices."""

from __future__ import annotations

import copy
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import bundle
from roboguide_recovery.provider_snapshot_retirement import run
from roboguide_recovery.snapshot_references import canonical, digest
from test_snapshot_references import source as source
from test_snapshot_references import states


def test_current_memory_bundle_retains_only_declared_nested_sql_parent(
    source: tuple[Path, dict[str, Any], str], tmp_path: Path
) -> None:
    """Keep the actual SQL parent while refusing an added sibling even after manifest repinning."""
    selected = source[1] | {"schema": "roboguide.recovery-profile/v0.16"}
    destination = tmp_path / "current-bundle"
    result = bundle.backup(selected, destination, "current")
    manifest = bundle.verify(destination, result["manifest_digest"])
    assert "stores/provider/active-map" in manifest["directories"]
    relative = "stores/provider/active-map/unselected-child"
    (destination / relative).mkdir()
    changed = copy.deepcopy(manifest)
    changed["directories"] = sorted(changed["directories"] + [relative])
    raw = canonical(changed)
    (destination / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="outside selected content trees"):
        bundle.verify(destination, digest(raw))


def test_profile_change_in_same_root_preserves_original_abort_compatibility(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deduplicate original writers and let the unchanged abort read its own exact receipt."""
    root, original, _ = source

    def interrupt(incoming: Path, target: Path, budget: bundle.Budget) -> tuple[str, int]:
        """Stop actual copying after the original Snapshot authority transaction."""
        raise OSError("original same-root copy interrupted")

    monkeypatch.setattr(bundle, "_copy", interrupt)
    destination = tmp_path / "unpublished-same-root"
    with pytest.raises(OSError, match="same-root copy interrupted"):
        bundle.backup(original, destination, "original")
    operation = states(root)[0][0]
    current = original | {"schema": "roboguide.recovery-profile/v0.16", "revision": 2}
    retired = run(current, original, "provider", operation[0], digest(operation[1]))
    original_tool = bundle.abort_snapshots(original, destination, "original")
    assert original_tool["receipts"] == retired["receipts"]
    assert states(root)[0][0][1] == operation[1] and states(root)[1][0][3] == 0


def relocated(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, Any], dict[str, Any], str, str]:
    """Interrupt copying, then cold-restore a complete bundle containing that original pin."""
    root, original, _ = source
    copy_file = bundle._copy

    def interrupt(incoming: Path, target: Path, budget: bundle.Budget) -> tuple[str, int]:
        """Refuse one actual copy boundary after the original SQL intent and pin commit."""
        raise OSError("original provider copy interrupted")

    monkeypatch.setattr(bundle, "_copy", interrupt)
    with pytest.raises(OSError, match="copy interrupted"):
        bundle.backup(original, tmp_path / "unpublished", "original")
    monkeypatch.setattr(bundle, "_copy", copy_file)
    saved = bundle.backup(original, tmp_path / "transfer-bundle", "transfer")
    bundle.restore(
        tmp_path / "transfer-bundle", saved["manifest_digest"], tmp_path / "restored", "restore"
    )
    current = copy.deepcopy(original)
    current["schema"] = "roboguide.recovery-profile/v0.16"
    recovered = tmp_path / "restored/stores/provider"
    current["components"][0]["root"] = str(recovered)
    bundle.reconcile_snapshots(current, tmp_path / "transfer-bundle", saved["manifest_digest"])
    operation = next(value for value in states(root)[0] if value[2] == "active")
    return recovered, current, original, operation[0], digest(operation[1])


def test_relocated_abort_reuses_original_receipt_and_preserves_historical_source(
    source: tuple[Path, dict[str, Any], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retire only the current original pin, with no second copy or source history mutation."""
    recovered, current, original, operation, checksum = relocated(source, tmp_path, monkeypatch)
    before = states(source[0])
    with pytest.raises(ValueError, match="intent is unavailable"):
        bundle.abort_snapshots(current, tmp_path / "unpublished", "original")
    result = run(current, original, "provider", operation, checksum)
    assert result["schema"] == "roboguide.provider-snapshot-retirement/v0.1"
    assert result["copied_files"] == 0 and not result["replayed"]
    replayed = run(current, original, "provider", operation, checksum)
    assert replayed["replayed"] and replayed["receipts"] == result["receipts"]
    assert states(source[0]) == before
    assert all(value[2] != "active" for value in states(recovered)[0])
    assert all(value[3] == 0 for value in states(recovered)[1])
    assert (tmp_path / "unpublished.partial-original").is_dir()
    with pytest.raises(ValueError, match="without another copy"):
        bundle.backup(original, tmp_path / "unpublished", "original")


@pytest.mark.parametrize("fault", ["profile", "digest", "instance", "pin", "published", "sql"])
def test_incomplete_original_authority_cannot_release_restored_pin(
    source: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Refuse changed original identity/publication or SQL failure without losing current pins."""
    recovered, current, original, operation, checksum = relocated(source, tmp_path, monkeypatch)
    if fault == "profile":
        original = original | {"revision": original["revision"] + 1}
    elif fault == "digest":
        checksum = "sha256:" + "0" * 64
    elif fault == "published":
        (tmp_path / "unpublished").mkdir()
    else:
        with closing(sqlite3.connect(recovered / "memory.sqlite3")) as connection:
            if fault == "instance":
                connection.execute("UPDATE identity SET instance='unit-foreign-provider'")
            elif fault == "pin":
                connection.execute("UPDATE content_references SET active=0 WHERE active=1")
            else:
                connection.execute(
                    "CREATE TRIGGER refuse_original_abort BEFORE UPDATE ON snapshot_operations "
                    "WHEN NEW.state='aborted' BEGIN "
                    "SELECT RAISE(ABORT,'actual abort SQL fault'); END"
                )
            connection.commit()
    before, source_before = states(recovered), states(source[0])
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        run(current, original, "provider", operation, checksum)
    assert states(recovered) == before and states(source[0]) == source_before
