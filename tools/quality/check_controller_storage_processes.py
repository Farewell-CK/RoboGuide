"""Check actual Controller hardlinks, original generation upgrade and cold file recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.error
from contextlib import closing
from pathlib import Path
from typing import Any

from check_content_archive_processes import run as run_archive
from check_resource_quota_processes import ROOT, make_certificates, port, request, stop


def digest(path: Path) -> str:
    """Freeze actual binary/source bytes rather than relying on a branch or build label."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> dict[str, list[Any]]:
    """Retain every original SQL row, normalizing only the explicitly renamed event-table name."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        result = {}
        for name in names:
            if name == "controller_storage_format":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            values = [list(row) for row in connection.execute("SELECT * FROM " + quoted)]
            if name == "sqlite_sequence":
                for row in values:
                    if row[0] == "controller_v1_events":
                        row[0] = "events"
            result["events" if name == "controller_v1_events" else name] = values
        return result


class Controllers:
    """Own children and retain actual native waits independently from HTTP observations."""

    def __init__(self, output: Path) -> None:
        """Create an isolated local TLS policy without registering any Node or device."""
        self.output = output
        leaves, self.admin, _ = make_certificates(output / "pki")
        policy = output / "operator.json"
        policy.write_text(
            json.dumps(
                {
                    "schema_version": "roboguide.operator-authorization/v0.1",
                    "registry_id": "actual-inode-process-policy",
                    "revision": 1,
                    "grants": {
                        "sha256:" + leaves["admin"]: {
                            "principal_id": "quota-admin",
                            "role": "recovery-admin",
                            "tenant_ids": ["*"],
                        }
                    },
                }
            )
        )
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        }
        self.environment.update(
            {
                "TOKIO_WORKER_THREADS": "1",
                "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT": str(output / "pki/server.crt"),
                "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY": str(output / "pki/server.key"),
                "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA": str(output / "pki/ca.crt"),
                "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP": str(policy),
            }
        )
        self.children: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []

    def launch(
        self, binary: Path, database: Path, label: str, storage: str | None, healthy: bool
    ) -> tuple[subprocess.Popen[bytes], str]:
        """Start exact software with a distinct CAS; refusal requires an actual process exit."""
        operator_port = port()
        origin = f"https://127.0.0.1:{operator_port}"
        arguments = [
            str(binary),
            f"127.0.0.1:{port()}",
            str(database),
            f"127.0.0.1:{operator_port}",
            f"127.0.0.1:{port()}",
            str(self.output / (label + "-cas")),
        ]
        environment = dict(self.environment)
        if storage is not None:
            environment["ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA"] = storage
        log_path = self.output / (label + ".log")
        with log_path.open("xb") as log:
            child = subprocess.Popen(arguments, cwd=ROOT, env=environment, stdout=log, stderr=log)
        record: dict[str, Any] = {
            "pid": child.pid,
            "arguments": arguments,
            "binary_digest": digest(binary),
            "storage": storage,
            "log": str(log_path),
        }
        self.children.append((child, record))
        if not healthy:
            record["native_wait"] = child.wait(timeout=12)
            if record["native_wait"] == 0:
                raise ValueError("refused Controller unexpectedly exited successfully")
            return child, origin
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Controller unexpectedly exited: " + str(log_path))
            try:
                status, value = request(origin, self.admin, "/healthz")
                if status == 200:
                    record["health"] = value
                    return child, origin
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise TimeoutError("Controller health deadline expired: " + str(log_path))

    def finish(self, child: subprocess.Popen[bytes]) -> None:
        """Reap only this owned process and retain its original actual exit result."""
        stop(child)
        for process, record in self.children:
            if process is child:
                record["native_wait"] = child.returncode
                break

    def close(self) -> None:
        """Preserve all original waits on success and on partial gate failure."""
        for child, _ in reversed(self.children):
            self.finish(child)


def probe(executable: Path, arguments: list[str], output: Path, label: str) -> dict[str, Any]:
    """Run a bounded actual storage process and retain exact stdout/stderr/native wait."""
    completed = subprocess.run(
        [str(executable), *arguments], capture_output=True, timeout=15, check=False
    )
    (output / (label + ".stdout")).write_bytes(completed.stdout)
    (output / (label + ".stderr")).write_bytes(completed.stderr)
    result = {
        "arguments": [str(executable), *arguments],
        "native_wait": completed.returncode,
        "stdout": completed.stdout.decode(),
        "stderr": completed.stderr.decode(),
    }
    if completed.returncode != 0:
        raise ValueError("actual storage probe failed: " + json.dumps(result))
    return result


def first_observation(child: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Read one bounded actual admission before testing its retained resource lifetime."""
    assert child.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            raise TimeoutError("actual storage holder did not report admission")
    raw = child.stdout.readline(1024 * 1024 + 1)
    value = json.loads(raw)
    if len(raw) > 1024 * 1024 or not isinstance(value, dict):
        raise ValueError("actual storage holder returned unbounded or invalid evidence")
    return value


def run(
    output: Path,
    binary: Path,
    executable: Path,
    legacy: Path | None,
    legacy_recovery_ref: str | None,
) -> dict[str, Any]:
    """Exercise actual hardlinks, legacy, clone, empty, upgrade and original cold recovery."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    sources = [
        *sorted((ROOT / "core/state/src").glob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
        ROOT / "apps/integration-server/src/application/persistence.rs",
        ROOT / "apps/integration-server/src/main.rs",
        Path(__file__),
        Path(__file__).with_name("check_content_archive_processes.py"),
        Path(__file__).with_name("check_resource_quota_processes.py"),
        ROOT / "core/state/examples/controller_storage_probe.rs",
    ]
    frozen = {str(path.relative_to(ROOT)): digest(path) for path in sources}
    result: dict[str, Any] = {
        "schema": "roboguide.actual-controller-storage/v0.1",
        "passed": False,
        "binary_digest": digest(binary),
        "probe_digest": digest(executable),
        "historical_binary": legacy is not None,
        "legacy_digest": digest(legacy) if legacy is not None else None,
        "legacy_recovery_ref": legacy_recovery_ref,
        "source_identity": frozen,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    controllers = Controllers(output)
    clone: subprocess.Popen[bytes] | None = None
    observer: subprocess.Popen[bytes] | None = None
    try:
        for storage in ("legacy", "1"):
            database = output / ("current-" + storage + ".sqlite3")
            original, _ = controllers.launch(binary, database, "current-" + storage, storage, True)
            alias = output / ("current-" + storage + "-hardlink.sqlite3")
            os.link(database, alias)
            controllers.launch(binary, alias, "refused-current-" + storage, storage, False)
            controllers.finish(original)
        # Actual current SQL retains the clone after the original EventLog object has dropped.
        database = output / "clone.sqlite3"
        clone = subprocess.Popen(
            [str(executable), "hold-clone", str(database), "legacy"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        held = first_observation(clone)
        alias = output / "clone-hardlink.sqlite3"
        os.link(database, alias)
        controllers.launch(binary, alias, "refused-actual-clone", "legacy", False)
        tail, errors = clone.communicate(b"x", timeout=10)
        result["clone"] = held | {
            "native_wait": clone.returncode,
            "tail": tail.decode(),
            "stderr": errors.decode(),
        }
        assert clone.returncode == 0
        clone = None
        admitted, _ = controllers.launch(binary, alias, "closed-clone", "legacy", True)
        controllers.finish(admitted)
        # Produce original authenticated quota authority, then preserve its complete source.
        old = legacy or binary
        producer_args = [
            sys.executable,
            str(Path(__file__).with_name("check_resource_quota_processes.py")),
            "--output",
            str(output / "original-legacy"),
            "--skip-build",
            "--controller-binary",
            str(old),
        ]
        producer = subprocess.run(producer_args, capture_output=True, timeout=90, check=False)
        (output / "original-producer.stdout").write_bytes(producer.stdout)
        (output / "original-producer.stderr").write_bytes(producer.stderr)
        result["original_producer"] = {
            "arguments": producer_args,
            "native_wait": producer.returncode,
        }
        if producer.returncode != 0:
            raise ValueError("original native quota producer did not complete")
        result["original_quota_gate"] = json.loads(
            (output / "original-legacy/result.json").read_bytes()
        )
        original_root = output / "original-legacy/owned-controller"
        for name, leaf in (
            ("SERVER_CERT", "server.crt"),
            ("SERVER_KEY", "server.key"),
            ("CLIENT_CA", "ca.crt"),
        ):
            controllers.environment["ROBOGUIDE_OPERATOR_MTLS_" + name] = str(
                original_root / "pki" / leaf
            )
        controllers.environment["ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP"] = str(
            original_root / "operator.json"
        )
        controllers.admin = ssl.create_default_context(cafile=str(original_root / "pki/ca.crt"))
        controllers.admin.load_cert_chain(
            original_root / "pki/admin.crt", original_root / "pki/admin.key"
        )
        database = output / "original-legacy/owned-controller/events.sqlite3"
        result["original_rows"] = rows(database)
        old_child, _ = controllers.launch(old, database, "original-live", None, True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        alias = database.with_name("different-original-hardlink.sqlite3")
        os.link(database, alias)
        controllers.launch(binary, alias, "refused-old-live-migration", "1", False)
        assert old_child.poll() is None
        controllers.finish(old_child)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        before = rows(database)
        locked = output / "actual-locked-legacy.sqlite3"
        shutil.copyfile(database, locked)
        with closing(sqlite3.connect(locked)) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            controllers.launch(binary, locked, "refused-exclusive-sql", "1", False)
            connection.rollback()
        assert rows(locked) == before
        result["upgrade_only"] = probe(
            executable, ["upgrade", str(database)], output, "original-upgrade"
        )
        after = rows(database)
        if after != before:
            raise ValueError("generation migration changed original SQL row evidence")
        result["original_rows_preserved"] = True
        observer_args = [str(executable), "observe", str(database)]
        observer = subprocess.Popen(
            observer_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        result["readonly_observer"] = first_observation(observer) | {"arguments": observer_args}
        upgraded, origin = controllers.launch(binary, database, "actual-v1-upgrade", "1", True)
        assert (
            request(origin, controllers.admin, "/v1/resource-quotas")[1]["registry"]["revision"]
            == 2
        )
        assert request(origin, controllers.admin, "/v1/inventory")[1]["nodes"] == []
        controllers.finish(upgraded)
        assert observer.poll() is None
        tail, errors = observer.communicate(b"x", timeout=10)
        result["readonly_observer"].update(
            native_wait=observer.returncode, tail=tail.decode(), stderr=errors.decode()
        )
        assert observer.returncode == 0
        observer = None
        # Ordinary managed legacy opens and the actual historical binary must both refuse v1.
        controllers.launch(binary, database, "refused-v1-as-legacy", "legacy", False)
        if legacy is not None:
            controllers.launch(legacy, database, "refused-old-binary-v1", None, False)
        empty = output / "empty.sqlite3"
        result["empty"] = probe(executable, ["empty", str(empty)], output, "empty")
        if legacy is not None:
            controllers.launch(legacy, empty, "refused-old-binary-empty", None, False)
        # Missing original marker/view cannot initialize an apparently new generation.
        for label, sql in (
            ("missing-marker", "DELETE FROM controller_storage_format"),
            ("missing-view", "DROP VIEW events"),
        ):
            damaged = output / (label + ".sqlite3")
            shutil.copyfile(empty, damaged)
            with closing(sqlite3.connect(damaged)) as connection:
                connection.execute(sql)
                connection.commit()
            damaged_digest = digest(damaged)
            controllers.launch(binary, damaged, "refused-" + label, "1", False)
            assert digest(damaged) == damaged_digest
        result["complete_original_archive_recovery"] = run_archive(
            output / "complete-original-archive",
            True,
            None,
            legacy_recovery_ref,
            abort_unpublished=True,
            controller_storage_v1=True,
        )
        if any(digest(path) != frozen[str(path.relative_to(ROOT))] for path in sources):
            raise ValueError("actual storage gate source changed during execution")
        if (
            digest(binary) != result["binary_digest"]
            or digest(executable) != result["probe_digest"]
        ):
            raise ValueError("actual storage gate binary changed during execution")
        result["passed"] = True
        return result
    finally:
        if clone is not None:
            stop(clone)
        if observer is not None:
            stop(observer)
            result["readonly_observer_native_wait"] = observer.returncode
        controllers.close()
        result["processes"] = [record for _, record in controllers.children]
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


def main() -> None:
    """Execute only an explicit owned software gate; hardware and online HA remain unproven."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", type=Path, default=ROOT / "target/debug/integration-server")
    parser.add_argument(
        "--probe", type=Path, default=ROOT / "target/debug/examples/controller_storage_probe"
    )
    parser.add_argument("--legacy-controller", type=Path)
    parser.add_argument("--legacy-recovery-ref")
    args = parser.parse_args()
    value = run(
        args.output.absolute(),
        args.controller.resolve(strict=True),
        args.probe.resolve(strict=True),
        args.legacy_controller.resolve(strict=True) if args.legacy_controller is not None else None,
        args.legacy_recovery_ref,
    )
    print(json.dumps({"passed": value["passed"], "processes": len(value["processes"])}))


if __name__ == "__main__":
    main()
