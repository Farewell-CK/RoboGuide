"""Verify actual unpublished provider copy, cold restore and both original writer fences."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from check_memory_reference_processes import request, save, stop


def run(
    root: Path,
    original_profile: Path,
    selected: dict[str, Any],
    unpublished: Path,
    runtime: Path,
    output: Path,
    launch: Callable[[Path], subprocess.Popen[bytes]],
    cli: Callable[..., dict[str, Any]],
    observe: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Retire the restored original while retaining native exits, SQL and source history."""
    bundle = output / "relocation-bundle"
    copied = cli(
        "backup",
        "--profile",
        str(original_profile),
        "--destination",
        str(bundle),
        "--command-id",
        "relocation-copy",
    )
    restored = runtime / "relocated-abort"
    cli(
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        copied["manifest_digest"],
        "--destination",
        str(restored),
        "--command-id",
        "relocated-abort-restore",
    )
    recovered = restored / "stores/provider"
    current = json.loads(json.dumps(selected))
    current["schema"] = "roboguide.recovery-profile/v0.16"
    current["components"][0]["root"] = str(recovered)
    profile = output / "relocated-abort-profile.json"
    save(profile, current)
    cli(
        "reconcile-snapshots",
        "--profile",
        str(profile),
        "--bundle",
        str(bundle),
        "--manifest-digest",
        copied["manifest_digest"],
    )
    original = next(
        value
        for value in observe(recovered)["operations"]
        if value["request"]["command_id"] == "interrupted"
    )
    assert original["state"] == "active"
    with closing(sqlite3.connect(recovered / "memory.sqlite3")) as connection:
        raw = connection.execute(
            "SELECT request_json FROM snapshot_operations WHERE operation_id=?",
            (original["operation_id"],),
        ).fetchone()[0]
    arguments = (
        "retire-provider-snapshot",
        "--profile",
        str(profile),
        "--original-profile",
        str(original_profile),
        "--provider",
        "provider",
        "--operation-id",
        original["operation_id"],
        "--original-intent-digest",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    cli(
        "abort-snapshots",
        "--profile",
        str(profile),
        "--destination",
        str(unpublished),
        "--command-id",
        "interrupted",
        passed=False,
    )
    source_before = observe(root)
    current_before = observe(recovered)
    for active_root in (root, recovered):
        process = launch(active_root)
        try:
            cli(*arguments, passed=False)
        finally:
            assert stop(process, active_root / "local.sock") == 0
        assert observe(root) == source_before and observe(recovered) == current_before
    with closing(sqlite3.connect(recovered / "memory.sqlite3")) as connection:
        connection.execute(
            "CREATE TRIGGER refuse_relocated_abort BEFORE UPDATE ON snapshot_operations "
            "WHEN NEW.state='aborted' BEGIN SELECT RAISE(ABORT,'native relocated abort fault'); END"
        )
        connection.commit()
    cli(*arguments, passed=False)
    assert observe(root) == source_before and observe(recovered) == current_before
    with closing(sqlite3.connect(recovered / "memory.sqlite3")) as connection:
        connection.execute("DROP TRIGGER refuse_relocated_abort")
        connection.commit()
    retired = cli(*arguments)
    assert retired["schema"] == "roboguide.provider-snapshot-retirement/v0.1"
    assert retired["copied_files"] == 0 and not retired["replayed"]
    replayed = cli(*arguments)
    assert replayed["replayed"] and replayed["receipts"] == retired["receipts"]
    assert observe(root) == source_before
    after = observe(recovered)
    chosen = next(
        value for value in after["operations"] if value["operation_id"] == original["operation_id"]
    )
    assert chosen["request"] == original["request"] and chosen["state"] == "aborted"
    assert not any(value[5] for value in after["references"] if value[2] == "snapshot")
    restarted = launch(recovered)
    try:
        status, identity = request(recovered / "local.sock", "/v1/instance", {}, "GET")
        assert (
            status == 200 and identity["instance_id"] == original["request"]["provider_instance_id"]
        )
    finally:
        assert stop(restarted, recovered / "local.sock") == 0
    cold = output / "relocated-retired-bundle"
    preserved = cli(
        "backup",
        "--profile",
        str(profile),
        "--destination",
        str(cold),
        "--command-id",
        "relocated-retired-copy",
    )
    cli("verify", "--bundle", str(cold), "--manifest-digest", preserved["manifest_digest"])
    cold_root = runtime / "relocated-retired"
    cli(
        "restore",
        "--bundle",
        str(cold),
        "--manifest-digest",
        preserved["manifest_digest"],
        "--destination",
        str(cold_root),
        "--command-id",
        "relocated-retired-restore",
    )
    restored_original = next(
        value
        for value in observe(cold_root / "stores/provider")["operations"]
        if value["operation_id"] == original["operation_id"]
    )
    assert restored_original == chosen and observe(root) == source_before
    save(output / "relocated-original-retirement.json", {"original": original, "retired": chosen})
    save(output / "relocated-historical-source.json", source_before)
    return {
        "actual_original_and_current_provider_writers_refused": True,
        "actual_sql_rollback": True,
        "actual_exact_original_retirement_and_retry": True,
        "actual_unchanged_v4_provider_restart": True,
        "actual_released_history_cold_restore": True,
        "historical_source_unchanged": True,
        "copied_by_retirement": 0,
        "retired_receipt": retired["receipts"][0],
    }
