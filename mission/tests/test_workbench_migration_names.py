"""Generated migration names remain resumable without widening new-session identities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mission.models import JSONObject
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.persistence import LEGACY_SCHEMA, SCHEMA, SessionStore


@pytest.mark.parametrize("length", [76, 77, 78, 79, 80])
def test_generated_migration_name_resumes_exact_original_receipts(
    tmp_path: Path, length: int
) -> None:
    """Every maximum-length legacy name keeps source bytes and pending receipts after migration."""
    name = "s" * length
    scope: JSONObject = {"tenant": "original"}
    original = SessionStore(tmp_path / "sessions", name, scope, False)
    original.data["schema"] = LEGACY_SCHEMA
    original.data["commands"] = [{"state": "pending", "idempotency_key": "original-key"}]
    original.save()
    source = original.path
    source_bytes = source.read_bytes()
    original.close()
    migrated = SessionStore(source.parent, name, scope, True)
    copied = migrated.path
    try:
        assert copied.stem == name + "-v03"
        assert migrated.data["schema"] == SCHEMA
        assert migrated.data["commands"] == [
            {"state": "pending", "idempotency_key": "original-key"}
        ]
        assert source.read_bytes() == source_bytes
        with pytest.raises(OSError):
            SessionStore(source.parent, copied.stem, scope, True)
    finally:
        migrated.close()
    with pytest.raises(WorkbenchError, match="升级副本"):
        SessionStore(source.parent, name, scope, True)
    resumed = SessionStore(source.parent, copied.stem, scope, True)
    try:
        assert resumed.path == copied
        assert resumed.data["commands"] == [{"state": "pending", "idempotency_key": "original-key"}]
        assert source.read_bytes() == source_bytes
    finally:
        resumed.close()


def test_previously_generated_extended_name_is_recoverable(tmp_path: Path) -> None:
    """Existing 84-character v0.3 migration copies need no renaming or command-history rewrite."""
    store = SessionStore(tmp_path / "sessions", "seed", {}, False)
    path = store.path
    store.data["commands"] = [{"state": "pending", "idempotency_key": "frozen"}]
    store.save()
    store.close()
    generated = path.with_name("x" * 80 + "-v03.json")
    path.rename(generated)
    before = generated.read_bytes()
    resumed = SessionStore(generated.parent, generated.stem, {}, True)
    try:
        assert generated.read_bytes() == before
        assert resumed.data["commands"] == [{"state": "pending", "idempotency_key": "frozen"}]
    finally:
        resumed.close()


@pytest.mark.parametrize("name", ["x" * 81, "x" * 80 + "-v03", "x" * 81 + "-v03"])
def test_new_sessions_do_not_accept_extended_names(tmp_path: Path, name: str) -> None:
    """Only actual resumed copies receive the bounded migration-suffix exception."""
    with pytest.raises(WorkbenchError, match="1–80"):
        SessionStore(tmp_path / "sessions", name, {}, False)
    assert not (tmp_path / "sessions").exists()


def test_extended_name_cannot_migrate_a_forged_legacy_copy(tmp_path: Path) -> None:
    """An extended name is v0.3-only so repeated suffixes cannot grow outside the bound."""
    store = SessionStore(tmp_path / "sessions", "seed", {}, False)
    path = store.path
    store.data["schema"] = LEGACY_SCHEMA
    store.save()
    store.close()
    extended = path.with_name("x" * 80 + "-v03.json")
    path.rename(extended)
    before = extended.read_bytes()
    with pytest.raises(WorkbenchError, match="来源或身份"):
        SessionStore(extended.parent, extended.stem, {}, True)
    assert extended.read_bytes() == before
    assert json.loads(before)["schema"] == LEGACY_SCHEMA
    assert not extended.with_name(extended.stem + "-v03.json").exists()
