"""Extend the zero-Node Archive case with actual complete State capture and Controller reopen."""

from __future__ import annotations

import hashlib
import json
import signal
import ssl
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_identity_processes import stop
from check_state_verifier_processes import save

Command = Callable[[list[str], bool, dict[str, str] | None], dict[str, Any]]
Launch = Callable[[Path, Path, Path], subprocess.Popen[bytes]]
HttpCall = Callable[
    [str, ssl.SSLContext, str, str, dict[str, Any] | bytes | None], tuple[int, bytes]
]


def run(
    directory: Path,
    probe: Path,
    database: Path,
    source: Path,
    archive: Path,
    launch: Launch,
    execute: Command,
    call: HttpCall,
    artifact_origin: str,
    operator_origin: str,
    admin: ssl.SSLContext,
    upload: str,
    checksum: str,
    payload: bytes,
    projection_probe: Path | None = None,
    projection_storage: str = "legacy",
) -> dict[str, Any]:
    """Keep original raw State/Catalog/Archive and bytes across an owned metadata relocation."""
    if not probe.is_file():
        raise ValueError("the actual maintained complete State process probe must already be built")
    evidence = directory / "complete-replica-state"
    evidence.mkdir(mode=0o700)
    capture = evidence / "original.json"
    original = execute([str(probe), "export", str(database), str(capture)], True, None)
    data = json.loads(capture.read_bytes())
    if not data["recovery"]["archive_operations"] or not data["recovery"]["archive_manifests"]:
        raise ValueError("actual Archive source lacks its original complete retained publication")
    projection_commands = None
    if projection_probe is None:
        target = evidence / "materialized.sqlite3"
        restored = execute([str(probe), "import", str(capture), str(target)], True, None)
    else:
        target_directory = evidence / "owned-controller"
        target_directory.mkdir()
        target = target_directory / "controller.sqlite3"
        command = [
            str(projection_probe),
            "--complete-original",
            str(target_directory),
            str(capture),
            projection_storage,
        ]
        projection_commands = [execute(command, True, None), execute(command, True, None)]
        installed = evidence / "actual-installed-original.json"
        restored = execute([str(probe), "export", str(target), str(installed)], True, None)
        if json.loads(installed.read_bytes()) != data:
            raise ValueError(
                "actual supervisor installation changed original complete recovery data"
            )
    if {key: value for key, value in restored.items() if key != "pid"} != {
        key: value for key, value in original.items() if key != "pid"
    }:
        raise ValueError("actual complete original metadata changed during materialization")
    damaged = json.loads(capture.read_bytes())
    del damaged["recovery"]["metadata"]["archive_authority_id"]
    damaged_path = evidence / "missing-original-authority.json"
    save(damaged_path, damaged)
    refused_target = evidence / "refused.sqlite3"
    execute([str(probe), "import", str(damaged_path), str(refused_target)], False, None)
    if refused_target.exists():
        raise ValueError("missing original authority created a replacement target")
    controller = launch(target, source, archive)
    try:
        status, raw = call(artifact_origin, admin, "GET", "/v1/artifact-uploads/" + upload, None)
        if status != 200 or not json.loads(raw):
            raise ValueError("actual materialized Controller lost its original upload")
        if call(artifact_origin, admin, "GET", "/v1/artifacts/" + checksum, None) != (200, payload):
            raise ValueError("actual materialized Controller cannot serve original real CAS bytes")
        status, raw = call(operator_origin, admin, "GET", "/v1/inventory", None)
        if status != 200 or json.loads(raw)["nodes"]:
            raise ValueError("actual metadata relocation introduced a Node inventory")
        live_capture = evidence / "live-refused.json"
        execute([str(probe), "export", str(target), str(live_capture)], False, None)
        if live_capture.exists():
            raise ValueError("an original active Controller writer permitted quiescent capture")
    finally:
        stop(controller)
    if controller.returncode not in (0, -signal.SIGTERM):
        raise ValueError("actual restored Controller lacks its expected native stop/wait result")
    after_path = evidence / "after-actual-controller.json"
    after = execute([str(probe), "export", str(target), str(after_path)], True, None)
    after_data = json.loads(after_path.read_bytes())
    if after_data["recovery"] != data["recovery"]:
        raise ValueError(
            "actual Controller reopen changed original Archive authority/receipts/budgets"
        )
    result = {
        "schema": "roboguide.complete-state-process-check/v0.1",
        "scope": "quiescent original metadata only; actual same CAS; no online quorum claim",
        "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
        "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "original": original,
        "materialized": restored,
        "after_actual_controller": after,
        "controller_pid": controller.pid,
        "controller_wait": controller.returncode,
        "original_source_owner": json.loads((source / ".retention-owner.json").read_bytes()),
        "registered_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "passed": True,
        "actual_supervisor_projection": projection_commands,
        "projection_storage": projection_storage if projection_probe is not None else None,
    }
    save(evidence / "result.json", result)
    return result
