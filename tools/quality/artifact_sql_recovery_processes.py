"""Own real zero-Node Controller/upload/GC and interrupted SQLite processes for recovery checks."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import sqlite3
import ssl
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import call, grant
from check_controller_storage_processes import Controllers
from check_resource_quota_processes import ROOT, request, stop
from recovery_profile_selection import declare_controller_storage, standalone_schema


def digest(path: Path) -> str:
    """Hash actual inputs and executables without claiming backend identity."""
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value: object) -> bytes:
    """Match actual complete-upload command digests without normalizing original evidence."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def gc_rows(database: Path) -> dict[str, list[Any]]:
    """Independently read every original GC row only when SQLite is already readable."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return {
            name: [list(row) for row in connection.execute(f"SELECT * FROM {name} ORDER BY 1")]
            for name in ("gc_metadata", "gc_commands", "gc_tombstones")
        }


class Case:
    """Own exact real processes/files; no registration, Node service, device or model is created."""

    def __init__(self, output: Path, binary: Path) -> None:
        """Create isolated actual TLS, original controller SQL and an explicit source v5 CAS."""
        output.mkdir(parents=True, mode=0o700)
        self.output, self.binary = output, binary
        self.controllers = Controllers(output)
        self.controller_root = output / "controller"
        self.controller_root.mkdir()
        self.database = self.controller_root / "events.sqlite3"
        self.artifact = output / "cas"
        self.gc_database = self.artifact / "reclamation-v1/journal.sqlite3"
        self.journal = Path(str(self.gc_database) + "-journal")
        self.cli_environment = os.environ | {"PYTHONPATH": str(ROOT / "apps/recovery-tool")}
        self.commands: list[dict[str, Any]] = []
        self.processes: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
        self.controllers.environment["ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA"] = (
            "roboguide.artifact-storage/v5"
        )
        leaf = ssl.PEM_cert_to_DER_cert((output / "pki/admin.crt").read_text())
        policy = output / "artifact-policy.json"
        policy.write_bytes(
            canonical(
                {
                    "schema_version": "roboguide.artifact-authorization/v0.1",
                    "registry_id": "actual-sql-recovery-artifact-policy",
                    "revision": 1,
                    "grants": {
                        "sha256:" + hashlib.sha256(leaf).hexdigest(): grant(
                            "quota-admin", "admin", "local"
                        )
                    },
                }
            )
        )
        for name, value in (
            ("SERVER_CERT", output / "pki/server.crt"),
            ("SERVER_KEY", output / "pki/server.key"),
            ("CLIENT_CA", output / "pki/ca.crt"),
            ("ROLE_MAP", policy),
        ):
            self.controllers.environment["ROBOGUIDE_ARTIFACT_MTLS_" + name] = str(value)
        self.profile: dict[str, Any] = {
            "schema": "roboguide.recovery-profile/v0.16",
            "profile_id": "actual-original-sql-recovery",
            "revision": 1,
            "max_bytes": 512 * 1024 * 1024,
            "max_files": 1024,
            "budget_seconds": 30,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": "controller",
                    "kind": "controller",
                    "root": str(self.controller_root),
                    "database": "events.sqlite3",
                    "trees": [],
                    "depends_on": [],
                },
                {
                    "component_id": "artifact",
                    "kind": "artifact",
                    "root": str(self.artifact),
                    "database": None,
                    "trees": [],
                    "depends_on": ["controller"],
                    "storage_schema": "roboguide.artifact-storage/v5",
                },
            ],
        }
        self.profile_path = output / "profile.json"

    def save_profile(self) -> None:
        """Freeze this explicit current input; original GC intents retain their earlier profile."""
        self.profile_path.write_bytes(canonical(self.profile))

    def start(
        self, storage: str = "1", *, database: Path | None = None, artifact: Path | None = None
    ) -> tuple[subprocess.Popen[bytes], str, str]:
        """Start actual owned software on declared SQL/CAS without hardcoding any Node inventory."""
        child, operator = self.controllers.launch(
            self.binary,
            database or self.database,
            "controller-" + str(len(self.controllers.children)),
            storage,
            True,
            artifact_root=artifact or self.artifact,
        )
        args = self.controllers.children[-1][1]["arguments"]
        return child, operator, "https://" + args[4]

    def command(self, arguments: list[str], *, passed: bool = True) -> dict[str, Any]:
        """Retain an actual CLI incarnation, stdout/stderr and its native wait even on failure."""
        args = [sys.executable, "-m", "roboguide_recovery", *arguments]
        child = subprocess.Popen(
            args, env=self.cli_environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        row: dict[str, Any] = {"pid": child.pid, "arguments": args}
        self.processes.append((child, row))
        try:
            stdout, stderr = child.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=10)
            raise
        finally:
            row["native_wait"] = child.poll()
        label = "command-" + str(len(self.commands))
        (self.output / (label + ".stdout")).write_bytes(stdout)
        (self.output / (label + ".stderr")).write_bytes(stderr)
        row["label"] = label
        self.commands.append(row)
        if passed and child.returncode != 0:
            raise ValueError("actual CLI failed: " + stderr.decode(errors="replace"))
        if not passed:
            if child.returncode == 0:
                raise ValueError("refused actual CLI unexpectedly succeeded")
            return {"stderr": stderr.decode(), "native_wait": child.returncode}
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise ValueError("actual CLI returned a non-object")
        return value

    def seed(self) -> None:
        """Create nonempty original history and live bytes through actual mTLS upload/GC."""
        child, operator, artifact = self.start("legacy")
        status, inventory = request(operator, self.controllers.admin, "/v1/inventory")
        assert status == 200 and inventory["nodes"] == []
        payloads = {
            "released": (ROOT / "core/state/src/controller_storage.rs").read_bytes(),
            "retained": (ROOT / "core/domain/src/memory.rs").read_bytes(),
        }
        originals = {}
        for name, payload in payloads.items():
            path = "/v1/artifact-uploads/" + name
            assert (
                call(
                    artifact,
                    self.controllers.admin,
                    "POST",
                    "/v1/artifact-uploads",
                    canonical({"upload_id": name}),
                )[0]
                == 201
            )
            assert (
                call(artifact, self.controllers.admin, "PUT", path + "/content", payload)[0] == 202
            )
            checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
            status, raw = call(
                artifact,
                self.controllers.admin,
                "POST",
                path + "/finalize",
                canonical({"content_digest": checksum, "byte_size": len(payload)}),
            )
            assert status == 201, raw
            originals[name] = json.loads(raw)["receipt"]
        released = originals["released"]
        status, raw = call(
            artifact,
            self.controllers.admin,
            "POST",
            "/v1/artifact-uploads/released/retire",
            canonical(
                {
                    "schema": "roboguide.artifact-upload-retirement-command/v0.1",
                    "command_id": "original-standalone-upload-retirement",
                    "original_upload_digest": "sha256:"
                    + hashlib.sha256(canonical(released)).hexdigest(),
                    "reason": "Release the original upload; retain independent live content",
                }
            ),
        )
        assert status == 200, raw
        self.controllers.finish(child)
        self.profile["schema"] = standalone_schema(self.database)
        declare_controller_storage(self.profile)
        self.save_profile()
        self.original_gc = self.command(
            [
                "gc-artifact",
                "--profile",
                str(self.profile_path),
                "--artifact",
                "artifact",
                "--content-digest",
                released["content_digest"],
                "--byte-size",
                str(released["byte_size"]),
                "--command-id",
                "original-standalone-source-gc",
                "--reason",
                "Reclaim only the independently retired original upload",
            ]
        )
        assert self.original_gc["phase"] == "completed"
        child, operator, artifact = self.start("1")
        assert request(operator, self.controllers.admin, "/v1/inventory")[1]["nodes"] == []
        self.retained_payload = payloads["retained"]
        self.retained_digest = originals["retained"]["content_digest"]
        self.released_digest = released["content_digest"]
        assert call(
            artifact, self.controllers.admin, "GET", "/v1/artifacts/" + self.retained_digest
        ) == (200, self.retained_payload)
        self.controllers.finish(child)
        if self.profile["schema"] not in {
            "roboguide.recovery-profile/v0.18",
            "roboguide.recovery-profile/v0.24",
            "roboguide.recovery-profile/v0.26",
            "roboguide.recovery-profile/v0.27",
            "roboguide.recovery-profile/v0.28",
        }:
            self.profile["schema"] = "roboguide.recovery-profile/v0.17"
        self.profile["components"][0]["storage_schema"] = "roboguide.controller-storage/v1"
        self.save_profile()
        self.original_rows = gc_rows(self.gc_database)
        assert (
            len(self.original_rows["gc_commands"]) == len(self.original_rows["gc_tombstones"]) == 1
        )
        self.original_sql_digest = digest(self.gc_database)
        (self.output / "original-gc-rows.json").write_bytes(canonical(self.original_rows))
        (self.output / "original-uploads.json").write_bytes(canonical(originals))

    def hot(self, *, mib: int = 2, keep_alive: bool = False) -> subprocess.Popen[bytes]:
        """Spill a real uncommitted transaction and retain actual SIGKILL/wait evidence."""
        code = """import fcntl,json,os,sqlite3,sys
from pathlib import Path
root=Path(sys.argv[1])
lease=(root/'.writer.lock').open('r+b')
fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
con=sqlite3.connect(root/'reclamation-v1/journal.sqlite3')
con.execute('PRAGMA synchronous=FULL')
con.execute('PRAGMA cache_size=1')
con.execute('PRAGMA cache_spill=ON')
con.execute('BEGIN IMMEDIATE')
pending='uncommitted-native-rollback-'+'x'*(int(sys.argv[2])*1024*1024)
con.execute("UPDATE gc_metadata SET value=? WHERE key='schema'",(pending,))
journal=root/'reclamation-v1/journal.sqlite3-journal'
print(json.dumps({'pid':os.getpid(),'journal_size':journal.stat().st_size,'journal_magic':journal.read_bytes()[:8].hex()}),flush=True)
sys.stdin.buffer.read(1)
"""
        args = [sys.executable, "-c", code, str(self.artifact), str(mib)]
        errors = (self.output / "fault-sql.stderr").open("xb")
        try:
            child = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors
            )
        finally:
            errors.close()
        record: dict[str, Any] = {"pid": child.pid, "arguments": args}
        self.processes.append((child, record))
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise TimeoutError("actual uncommitted SQL did not reach its spill")
        raw = child.stdout.readline(1024 * 1024)
        if not raw:
            raise ValueError(
                "actual SQL fault child exited: " + (self.output / "fault-sql.stderr").read_text()
            )
        observed = json.loads(raw)
        assert observed["journal_size"] > 512 and observed["journal_magic"] != "0000000000000000"
        record["observed_spill"] = observed
        if not keep_alive:
            child.kill()
            record["native_wait"] = child.wait(timeout=10)
            assert child.returncode == -signal.SIGKILL
        return child

    def repair_arguments(self, directory: Path) -> list[str]:
        """Pin this actual current original SQL/journal before invoking any effectful CLI."""
        return [
            "recover-artifact-journal",
            "--profile",
            str(self.profile_path),
            "--artifact",
            "artifact",
            "--sql-digest",
            digest(self.gc_database),
            "--journal-digest",
            digest(self.journal),
            "--evidence-directory",
            str(directory),
            "--reason",
            "Recover this actual owned original uncommitted SQL, preserving all GC authority",
        ]

    def verify_current(self) -> None:
        """Reopen the actual Controller, read retained bytes and preserve exact old GC rows."""
        assert gc_rows(self.gc_database) == self.original_rows
        assert digest(self.gc_database) == self.original_sql_digest
        child, operator, artifact = self.start()
        assert request(operator, self.controllers.admin, "/v1/inventory")[1]["nodes"] == []
        assert call(
            artifact, self.controllers.admin, "GET", "/v1/artifacts/" + self.retained_digest
        ) == (200, self.retained_payload)
        assert (
            call(artifact, self.controllers.admin, "GET", "/v1/artifacts/" + self.released_digest)[
                0
            ]
            != 200
        )
        self.controllers.finish(child)

    def close(self) -> None:
        """Reap only owned children and keep original actual waits on every gate outcome."""
        for child, record in reversed(self.processes):
            if child.poll() is None:
                stop(child)
            record["native_wait"] = child.returncode
        self.controllers.close()
        (self.output / "processes.json").write_bytes(
            canonical(
                {
                    "controllers": [record for _, record in self.controllers.children],
                    "other_processes": [record for _, record in self.processes],
                    "commands": self.commands,
                }
            )
        )
