"""Artifact journal parser/copy faults; native producers have a separate process gate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery import artifact_snapshots, bundle
from roboguide_recovery.bundle import (
    abort_snapshots,
    backup,
    canonical,
    digest,
    reconcile_snapshots,
    restore,
    verify,
)
from test_retained_artifacts import retained as retained


@pytest.fixture
def journal(retained: tuple[Path, dict[str, Any], str]) -> tuple[Path, dict[str, Any], str]:
    """Extend storage parser fixtures; native migration separately proves actual identity."""
    root, profile, checksum = retained
    profile = copy.deepcopy(profile)
    profile["schema"] = artifact_snapshots.SCHEMA
    profile["max_files"] = 128
    owner = json.loads((root / ".retention-owner.json").read_bytes())
    owner["schema"] = "roboguide.artifact-storage/v2"
    (root / "maintenance-v1/operations").mkdir(parents=True)
    for name in ("blobs", ".retention-owner.json", "maintenance-v1/identity.json"):
        (root / name).write_bytes(canonical(owner))
    return root, profile, checksum


def test_original_snapshot_intent_precedes_copy_and_restore_keeps_active_original(
    journal: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe acquire before copy, original release, cold history and readonly retries."""
    root, profile, checksum = journal
    target = tmp_path / "bundle"
    original_copy = bundle._copy
    observed: list[dict[str, Any]] = []

    def inspect(source: Path, destination: Path, budget: bundle.Budget) -> tuple[str, int]:
        """Read actual original producer intent before the copier receives its first source file."""
        if not observed:
            rows = artifact_snapshots.operations(root, budget)
            assert len(rows) == 1 and rows[0][2] is None
            assert rows[0][1]["inputs"][0]["content_digest"] == checksum
            observed.append(copy.deepcopy(rows[0][1]))
        return original_copy(source, destination, budget)

    monkeypatch.setattr(bundle, "_copy", inspect)
    result = backup(profile, target, "original")
    assert observed
    rows = artifact_snapshots.operations(root, bundle.Budget(profile))
    assert rows[0][1] == observed[0]
    assert rows[0][2] is not None and rows[0][2]["state"] == "released"
    manifest = verify(target, result["manifest_digest"])
    assert manifest["schema"] == "roboguide.recovery-bundle/v0.3"
    assert len(manifest["artifact_snapshot_operations"]) == 1
    restored = tmp_path / "restored"
    receipt = restore(target, result["manifest_digest"], restored, "restore")
    assert receipt["schema"] == "roboguide.restore-receipt/v0.11"
    recovered = restored / "stores/artifact"
    original_rows = artifact_snapshots.operations(recovered, bundle.Budget(profile))
    assert original_rows[0][1] == observed[0] and original_rows[0][2] is None
    assert restore(target, result["manifest_digest"], restored, "restore") == receipt
    assert backup(profile, target, "original") == result
    assert reconcile_snapshots(profile, target, result["manifest_digest"])["copied_files"] == 0
    assert artifact_snapshots.operations(root, bundle.Budget(profile))[0] == rows[0]


def test_interrupted_original_copy_retains_pins_and_cannot_recopy(
    journal: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed copier leaves original budgets/bytes pinned until explicit lease-bound abort."""
    root, profile, _ = journal
    target = tmp_path / "interrupted"

    def interrupt(_source: Path, _destination: Path, _budget: bundle.Budget) -> tuple[str, int]:
        """Inject a storage failure after the real producer acquired its original pin."""
        raise OSError("original copier fault")

    monkeypatch.setattr(bundle, "_copy", interrupt)
    with pytest.raises(OSError, match="original copier"):
        backup(profile, target, "original")
    original = artifact_snapshots.operations(root, bundle.Budget(profile))
    assert len(original) == 1 and original[0][2] is None
    with pytest.raises(ValueError, match="readonly reconciliation"):
        backup(profile, target, "original")
    assert artifact_snapshots.operations(root, bundle.Budget(profile)) == original
    aborted = abort_snapshots(profile, target, "original")
    assert aborted["receipts"][0]["state"] == "aborted"
    assert artifact_snapshots.operations(root, bundle.Budget(profile))[0][1] == original[0][1]
    assert abort_snapshots(profile, target, "original") == aborted
    with pytest.raises(ValueError, match="readonly reconciliation"):
        backup(profile, target, "original")


@pytest.mark.parametrize("part", ["identity", "intent", "bytes", "descriptor"])
def test_repinned_incomplete_bundle_cannot_drop_original_retention(
    journal: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
    part: str,
) -> None:
    """Rehashing a modified manifest cannot make mandatory journal/content coverage optional."""
    _, profile, checksum = journal
    target = tmp_path / "bundle"
    result = backup(profile, target, "original")
    manifest = verify(target, result["manifest_digest"])
    if part == "descriptor":
        manifest["artifact_snapshot_operations"] = []
    else:
        suffix = {
            "identity": "maintenance-v1/identity.json",
            "intent": "intent.json",
            "bytes": checksum[7:],
        }[part]
        selected = next(value for value in manifest["files"] if value["path"].endswith(suffix))
        (target / selected["path"]).unlink()
        manifest["files"].remove(selected)
    raw = canonical(manifest)
    (target / "manifest.json").write_bytes(raw)
    with pytest.raises((ValueError, OSError)):
        verify(target, digest(raw))


def test_other_owner_or_original_kernel_change_cannot_reconcile(
    journal: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Reconciliation keeps the exact original owner, intent and first deadline."""
    root, profile, _ = journal
    target = tmp_path / "bundle"
    result = backup(profile, target, "original")
    rows = artifact_snapshots.operations(root, bundle.Budget(profile))
    original_path, request, _ = rows[0]
    request["kernel"]["deadline_ns"] += 1
    (original_path / "intent.json").write_bytes(canonical(request))
    with pytest.raises(ValueError, match="retirement|proof"):
        reconcile_snapshots(profile, target, result["manifest_digest"])


def test_old_profile_refuses_v2_without_creating_backup(
    journal: tuple[Path, dict[str, Any], str],
    tmp_path: Path,
) -> None:
    """Explicit older retained profile cannot omit a newly mandatory maintenance journal."""
    _, profile, _ = journal
    profile["schema"] = "roboguide.recovery-profile/v0.10"
    with pytest.raises(ValueError, match="owner metadata"):
        backup(profile, tmp_path / "refused", "old")
    assert not (tmp_path / "refused").exists()
