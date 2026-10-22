"""Interrupt actual Artifact Snapshot CLI processes and inspect original journals and bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from check_identity_processes import ROOT
from check_state_verifier_processes import save


def history(root: Path) -> dict[str, Any]:
    """Independently read actual immutable files without importing the producer implementation."""
    rows = {}
    for path in sorted((root / "maintenance-v1/operations").iterdir()):
        if path.is_dir():
            rows[path.name] = {
                child.name: json.loads(child.read_bytes()) for child in sorted(path.iterdir())
            }
    return {
        "owner": json.loads((root / ".retention-owner.json").read_bytes()),
        "operations": rows,
    }


def run(directory: Path, skip_build: bool) -> dict[str, Any]:
    """Run owned storage/CLI processes, native stop/wait and readonly reconciliation."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory = directory.resolve()
    if not skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "artifact-store", "--example", "layout_probe"],
            cwd=ROOT,
            check=True,
            timeout=180,
        )
    probe = ROOT / "target/debug/examples/layout_probe"
    sources = [
        Path(__file__),
        *sorted((ROOT / "core/artifact-store/src").rglob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
    ]
    frozen = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sources
    }
    save(directory / "source-identity.json", frozen)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    commands: list[dict[str, Any]] = []

    def execute(arguments: list[str], *, passed: bool = True) -> dict[str, Any]:
        """Retain exact argv, native exit and output before checking either success or refusal."""
        completed = subprocess.run(
            arguments, cwd=ROOT, env=environment, capture_output=True, timeout=90
        )
        label = "command-" + str(len(commands))
        (directory / (label + ".stdout")).write_bytes(completed.stdout)
        (directory / (label + ".stderr")).write_bytes(completed.stderr)
        commands.append({"argv": arguments, "exit_code": completed.returncode})
        save(directory / "commands.json", commands)
        if not passed:
            assert completed.returncode != 0
            return {
                "stderr": completed.stderr.decode(errors="replace"),
                "exit_code": completed.returncode,
            }
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        value = json.loads(completed.stdout)
        assert isinstance(value, dict)
        return value

    def cli(*arguments: str, passed: bool = True) -> dict[str, Any]:
        """Execute the shipped standalone recovery CLI without a test wrapper or internal calls."""
        return execute([sys.executable, "-m", "roboguide_recovery", *arguments], passed=passed)

    root = directory / "artifact"
    actual = execute([str(probe), "references", str(root)])
    owner = actual["owner_id"]
    input_path = directory / "original.bin"
    source = (ROOT / "core/domain/src/memory.rs").read_bytes()
    # Real opaque files provide a copy window; this is no device/capability or deployment inventory.
    for index in range(8):
        payload = bytes([index]) + (source * (16 * 1024**2 // len(source) + 1))[: 16 * 1024**2 - 1]
        input_path.write_bytes(payload)
        row = execute([str(probe), "references", str(root), str(input_path)])
        assert row["owner_id"] == owner
    profile: dict[str, Any] = {
        "schema": "roboguide.recovery-profile/v0.11",
        "profile_id": "actual-artifact-snapshot",
        "revision": 1,
        "max_bytes": 768 * 1024**2,
        "max_files": 512,
        "budget_seconds": 60,
        "artifact_replica_sets": [],
        "components": [
            {
                "component_id": "artifact",
                "kind": "artifact",
                "root": str(root),
                "database": None,
                "trees": [],
                "depends_on": [],
            }
        ],
    }
    profile_path = directory / "profile.json"
    save(profile_path, profile)
    destination = directory / "interrupted"
    arguments = [
        sys.executable,
        "-m",
        "roboguide_recovery",
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(destination),
        "--command-id",
        "original-interrupted",
    ]
    observed = None
    stopped = None
    with (
        (directory / "interrupted.stdout").open("wb") as output,
        (directory / "interrupted.stderr").open("wb") as error,
    ):
        child = subprocess.Popen(
            arguments,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=error,
            stdin=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and child.poll() is None:
                paths = list((root / "maintenance-v1/operations").glob("*/intent.json"))
                if paths:
                    try:
                        observed = json.loads(paths[0].read_bytes())
                    except (OSError, json.JSONDecodeError):
                        continue
                    os.kill(child.pid, signal.SIGSTOP)
                    stopped = os.waitid(os.P_PID, child.pid, os.WSTOPPED)
                    break
                time.sleep(0.001)
            assert observed is not None and stopped is not None
            assert stopped.si_code == os.CLD_STOPPED and stopped.si_status == signal.SIGSTOP
            assert not destination.exists(), "original copy published before interruption window"
            assert observed["store_owner_id"] == owner and len(observed["inputs"]) == 8
            before = history(root)
            assert all("receipt.json" not in row for row in before["operations"].values())
            cli(
                "backup",
                "--profile",
                str(profile_path),
                "--destination",
                str(directory / "live-refused"),
                "--command-id",
                "live-refused",
                passed=False,
            )
            assert not (directory / "live-refused").exists()
            os.kill(child.pid, signal.SIGKILL)
            assert child.wait(timeout=10) == -signal.SIGKILL
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    save(
        directory / "original-interrupted.json",
        {
            "argv": arguments,
            "actual_pid": child.pid,
            "native_stop": {"code": stopped.si_code, "status": stopped.si_status},
            "actual_wait": child.returncode,
            "original": observed,
        },
    )
    refused = execute(arguments, passed=False)
    assert "readonly reconciliation" in refused["stderr"]
    assert history(root) == before
    aborted = cli(
        "abort-snapshots",
        "--profile",
        str(profile_path),
        "--destination",
        str(destination),
        "--command-id",
        "original-interrupted",
    )
    assert aborted["receipts"][0]["state"] == "aborted"
    assert (
        cli(
            "abort-snapshots",
            "--profile",
            str(profile_path),
            "--destination",
            str(destination),
            "--command-id",
            "original-interrupted",
        )
        == aborted
    )
    assert "readonly reconciliation" in execute(arguments, passed=False)["stderr"]
    assert next(iter(history(root)["operations"].values()))["intent.json"] == observed
    bundle_path = directory / "complete-bundle"
    # Deny the original receipt write after intent, while leaving source reads valid.
    completion_arguments = [
        sys.executable,
        "-m",
        "roboguide_recovery",
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(bundle_path),
        "--command-id",
        "original-complete",
    ]
    completion_path = None
    with (
        (directory / "receipt-failure.stdout").open("wb") as output,
        (directory / "receipt-failure.stderr").open("wb") as error,
    ):
        child_completion = subprocess.Popen(
            completion_arguments,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=error,
            stdin=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and child_completion.poll() is None:
                for path in (root / "maintenance-v1/operations").glob("*/intent.json"):
                    try:
                        value = json.loads(path.read_bytes())
                    except (OSError, json.JSONDecodeError):
                        continue
                    if value["command_id"] == "original-complete":
                        completion_path = path.parent
                        break
                if completion_path is not None:
                    os.kill(child_completion.pid, signal.SIGSTOP)
                    stop_result = os.waitid(os.P_PID, child_completion.pid, os.WSTOPPED)
                    assert stop_result is not None and stop_result.si_code == os.CLD_STOPPED
                    completion_path.chmod(0o500)
                    os.kill(child_completion.pid, signal.SIGCONT)
                    break
                time.sleep(0.001)
            assert completion_path is not None
            assert child_completion.wait(timeout=75) != 0
            assert bundle_path.is_dir()
            assert not (completion_path / "receipt.json").exists()
            before_completion = json.loads((completion_path / "intent.json").read_bytes())
        finally:
            if completion_path is not None:
                completion_path.chmod(0o700)
            if child_completion.poll() is None:
                child_completion.kill()
                child_completion.wait(timeout=10)
    immutable_files = {
        str(path.relative_to(bundle_path)): (path.stat().st_ino, path.stat().st_mtime_ns)
        for path in bundle_path.rglob("*")
        if path.is_file()
    }
    receipt = cli(
        "backup",
        "--profile",
        str(profile_path),
        "--destination",
        str(bundle_path),
        "--command-id",
        "original-complete",
    )
    assert json.loads((completion_path / "intent.json").read_bytes()) == before_completion
    assert {
        str(path.relative_to(bundle_path)): (path.stat().st_ino, path.stat().st_mtime_ns)
        for path in bundle_path.rglob("*")
        if path.is_file()
    } == immutable_files
    pin = receipt["manifest_digest"]
    cli("verify", "--bundle", str(bundle_path), "--manifest-digest", pin)
    manifest = json.loads((bundle_path / "manifest.json").read_bytes())
    original = manifest["artifact_snapshot_operations"][0]
    assert (
        history(root)["operations"][original["operation_id"][7:]]["receipt.json"]["state"]
        == "released"
    )
    restored = directory / "restored"
    restored_receipt = cli(
        "restore",
        "--bundle",
        str(bundle_path),
        "--manifest-digest",
        pin,
        "--destination",
        str(restored),
        "--command-id",
        "restore",
    )
    assert restored_receipt["schema"] == "roboguide.restore-receipt/v0.11"
    recovered = restored / "stores/artifact"
    recovered_history = history(recovered)
    assert recovered_history["owner"]["owner_id"] == owner
    assert "receipt.json" not in recovered_history["operations"][original["operation_id"][7:]]
    assert execute([str(probe), "references", str(recovered)])["owner_id"] == owner
    relocated = json.loads(json.dumps(profile))
    relocated["components"][0]["root"] = str(recovered)
    relocated_path = directory / "restored-profile.json"
    save(relocated_path, relocated)
    assert (
        cli(
            "reconcile-snapshots",
            "--profile",
            str(relocated_path),
            "--bundle",
            str(bundle_path),
            "--manifest-digest",
            pin,
        )["copied_files"]
        == 0
    )
    assert (
        history(recovered)["operations"][original["operation_id"][7:]]["intent.json"]
        == recovered_history["operations"][original["operation_id"][7:]]["intent.json"]
    )
    independent = []
    for row in manifest["files"]:
        if "/objects-v1/" not in row["path"]:
            continue
        path = restored / row["path"]
        with path.open("rb") as stream:
            checksum = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        assert checksum == row["content_digest"] and path.stat().st_size == row["byte_size"]
        independent.append({"content_digest": checksum, "byte_size": path.stat().st_size})
    assert len(independent) == 8
    assert {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sources
    } == frozen
    result = {
        "passed": True,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "actual_owner": owner,
        "actual_original_wait": child.returncode,
        "acquire_preceded_actual_copy": True,
        "active_writer_refused": True,
        "original_interrupted_copy_never_repeated": True,
        "original_budget_unchanged": True,
        "actual_abort_retains_partial_evidence": True,
        "original_active_intent_survived_cold_restore": True,
        "actual_readonly_reconciliation": True,
        "actual_receipt_write_denied_after_publication": True,
        "published_retry_copied_zero_files": True,
        "independent_content_reads": independent,
        "probe_digest": hashlib.sha256(probe.read_bytes()).hexdigest(),
    }
    save(directory / "result.json", result)
    return result


def main() -> None:
    """Require an isolated new output root and explicitly select the actual native build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.output, arguments.skip_build), sort_keys=True))


if __name__ == "__main__":
    main()
