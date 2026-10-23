"""Check actual inactive Controller inode/SQL installation, without quorum or device claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

from check_controller_storage_processes import Controllers, first_observation, rows
from check_resource_quota_processes import ROOT, request
from check_state_verifier_processes import save


class ProjectionCheck:
    """Own actual processes and preserve original receipts independently of local import results."""

    def __init__(
        self, output: Path, controller: Path, probe: Path, state: Path, holder: Path
    ) -> None:
        """Declare exact binaries and a fresh authenticated Controller profile with zero Nodes."""
        self.output = output
        self.controller = controller
        self.probe = probe
        self.state = state
        self.holder = holder
        self.binaries = {
            str(path): self.digest(path) for path in (controller, probe, state, holder)
        }
        self.controllers = Controllers(output)
        self.directory = output / "owned"
        self.directory.mkdir()
        self.database = self.directory / "controller.sqlite3"
        self.cas = self.directory / "cas"
        self.commands: list[dict[str, Any]] = []
        self.holder_waits: list[dict[str, Any]] = []
        self.original_command = {
            "command_id": "actual-original-projection-quota",
            "expected_revision": 0,
            "registry": {
                "registry_id": "actual-original-projection-policy",
                "revision": 1,
                "limits": [{"tenant_id": "original-tenant", "kind": "Compute", "max_units": 12}],
            },
        }

    @staticmethod
    def digest(path: Path) -> str:
        """Pin actual file bytes rather than a mutable build path or a branch label."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def execute(self, arguments: list[str], label: str, *, accepted: bool) -> dict[str, Any]:
        """Run exactly one local process, retaining its actual stdout/stderr and native wait."""
        completed = subprocess.run(arguments, capture_output=True, timeout=20, check=False)
        record = {
            "arguments": arguments,
            "native_wait": completed.returncode,
            "stdout": completed.stdout.decode(),
            "stderr": completed.stderr.decode(),
        }
        save(self.output / (label + ".json"), record)
        self.commands.append(record)
        assert (completed.returncode == 0) is accepted, record
        return record

    def start(self, label: str, storage: str) -> tuple[subprocess.Popen[bytes], str]:
        """Start actual Controller composition against the same original SQL and CAS roots."""
        return self.controllers.launch(
            self.controller, self.database, label, storage, True, artifact_root=self.cas
        )

    def capture(self, label: str) -> tuple[Path, dict[str, Any]]:
        """Export actual original complete rows only after the owned Controller has exited."""
        destination = self.output / (label + ".original.json")
        self.execute(
            [str(self.state), "export", str(self.database), str(destination)],
            label + "-capture",
            accepted=True,
        )
        original = json.loads(destination.read_bytes())
        self.event_sequence = original["checkpoint"]["event_sequence"]
        return destination, original

    def unchanged_rows(self, previous: dict[str, list[Any]], label: str) -> None:
        """Check all original SQL values, allowing only exact derived-counter reconstruction."""
        current = rows(self.database)
        expected = json.loads(json.dumps(previous))
        metadata = dict(expected["event_log_metadata"])
        if "next_event_number" not in metadata:
            # The actual normal source omits zero. The installer derives it from this exact head.
            expected["event_log_metadata"].append(["next_event_number", str(self.event_sequence)])
        save(self.output / (label + "-all-sql-rows.json"), current)
        for name in expected.keys() | current.keys():
            original_rows = sorted(expected.get(name, []), key=lambda row: json.dumps(row))
            actual_rows = sorted(current.get(name, []), key=lambda row: json.dumps(row))
            assert original_rows == actual_rows, name

    def materialize(self, view: Path, label: str, storage: str) -> dict[str, Any]:
        """Require refusal of explicitly incomplete local input through the actual composition."""
        return self.execute(
            [
                str(self.probe),
                str(self.directory),
                str(view),
                storage,
                self.schema,
                "declared-local-check",
            ],
            label,
            accepted=False,
        )

    def install(
        self, original: Path, label: str, storage: str, *, accepted: bool
    ) -> dict[str, Any]:
        """Exercise the same complete installation function used by a validated supervisor view."""
        return self.execute(
            [str(self.probe), "--complete-original", str(self.directory), str(original), storage],
            label,
            accepted=accepted,
        )

    def held_alias(self, view: Path) -> None:
        """Prove another actual managed SQL clone cannot be bypassed through a hardlink pathname."""
        alias = self.directory / "original-hardlink.sqlite3"
        os.link(self.database, alias)
        held = subprocess.Popen(
            [str(self.holder), "hold-clone", str(alias), "legacy"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            actual = first_observation(held)
            source_digest = self.digest(self.database)
            source_inode = self.database.stat().st_ino
            refused = self.materialize(view, "held-original-alias", "legacy")
            assert "actual SQL inode writer is active" in refused["stderr"]
            assert self.digest(self.database) == source_digest
            assert self.database.stat().st_ino == source_inode == alias.stat().st_ino
            save(self.output / "held-original-alias-observation.json", actual)
        finally:
            assert held.stdin is not None
            held.stdin.write(b"x")
            held.stdin.close()
            self.holder_waits.append({"pid": held.pid, "native_wait": held.wait(timeout=10)})
            alias.unlink()

    def seed(self) -> tuple[Path, dict[str, Any]]:
        """Persist an authenticated quota command without creating Nodes or capabilities."""
        controller, origin = self.start("original-controller", "legacy")
        status, receipt = request(
            origin, self.controllers.admin, "/v1/resource-quotas", self.original_command
        )
        assert status == 200, receipt
        assert request(origin, self.controllers.admin, "/v1/inventory")[1]["nodes"] == []
        self.controllers.finish(controller)
        self.receipt = receipt
        save(
            self.output / "original-authenticated-command.json",
            {"command": self.original_command, "receipt": receipt},
        )
        original, data = self.capture("original")
        self.schema = data["checkpoint"]["schema"]
        self.inode = self.database.stat().st_ino
        return original, data

    def verify(self) -> dict[str, Any]:
        """Exercise lease refusal, complete replay, stale authority and native SQL rollback."""
        original, data = self.seed()
        empty = self.output / "explicit-empty-view.json"
        save(empty, {"revision": 0, "checkpoint": None, "events": [], "commands": []})
        self.held_alias(empty)
        before = rows(self.database)
        save(self.output / "original-all-sql-rows.json", before)
        refused = self.materialize(empty, "empty-view-original-authority", "legacy")
        assert "cannot replace original Controller authority" in refused["stderr"]
        assert rows(self.database) == before
        assert self.database.stat().st_ino == self.inode

        incomplete = self.output / "explicit-metadata-only-view.json"
        save(
            incomplete,
            {
                "revision": data["commands"][-1]["authority_revision"],
                "checkpoint": data["checkpoint"],
                "events": data["events"],
                "commands": data["commands"],
            },
        )
        checksum = self.digest(self.database)
        refused = self.materialize(incomplete, "modern-incomplete-view", "legacy")
        assert "original complete recovery authority" in refused["stderr"]
        assert self.digest(self.database) == checksum

        sentinel = self.directory / "controller.materializing.sqlite3"
        sentinel.write_bytes(b"retained prior partial file; never an authority")
        self.install(original, "original-legacy-install", "legacy", accepted=True)
        save(self.output / "after-install-all-sql-rows.json", rows(self.database))
        self.unchanged_rows(before, "original-legacy-install")
        installed = self.output / "installed.original.json"
        self.execute(
            [str(self.state), "export", str(self.database), str(installed)],
            "installed-capture",
            accepted=True,
        )
        assert json.loads(installed.read_bytes()) == data
        assert self.database.stat().st_ino == self.inode
        assert sentinel.read_bytes() == b"retained prior partial file; never an authority"

        controller, origin = self.start("actual-original-reopen", "legacy")
        assert (
            request(origin, self.controllers.admin, "/v1/resource-quotas")[1]["registry"]
            == self.original_command["registry"]
        )
        assert request(
            origin, self.controllers.admin, "/v1/resource-quotas", self.original_command
        ) == (200, self.receipt)
        update = json.loads(json.dumps(self.original_command))
        update["command_id"] = "actual-next-projection-quota"
        update["expected_revision"] = 1
        update["registry"]["revision"] = 2
        update["registry"]["limits"][0]["max_units"] = 13
        status, updated_receipt = request(
            origin, self.controllers.admin, "/v1/resource-quotas", update
        )
        assert status == 200, updated_receipt
        assert request(origin, self.controllers.admin, "/v1/inventory")[1]["nodes"] == []
        self.controllers.finish(controller)
        save(
            self.output / "actual-successor-command.json",
            {"command": update, "receipt": updated_receipt},
        )

        successor_rows = rows(self.database)
        refused = self.install(original, "stale-complete-original", "legacy", accepted=False)
        assert "discard original commands" in refused["stderr"]
        assert rows(self.database) == successor_rows
        current, current_data = self.capture("current")
        assert current_data["commands"][: len(data["commands"])] == data["commands"]
        self.install(current, "actual-v1-install", "1", accepted=True)
        self.unchanged_rows(successor_rows, "actual-v1-install")
        assert self.database.stat().st_ino == self.inode
        refused = self.install(current, "v1-refuses-legacy-install", "legacy", accepted=False)
        assert "requires the actual managed v1 writer" in refused["stderr"]

        with closing(sqlite3.connect(self.database)) as sql, sql:
            sql.execute(
                "CREATE TRIGGER refuse_projection BEFORE DELETE ON control_command_journal "
                "BEGIN SELECT RAISE(ABORT,'actual projection SQL rollback'); END"
            )
        try:
            self.install(current, "actual-sql-rollback", "1", accepted=False)
            self.unchanged_rows(successor_rows, "actual-sql-rollback")
        finally:
            with closing(sqlite3.connect(self.database)) as sql, sql:
                sql.execute("DROP TRIGGER refuse_projection")
        self.install(current, "actual-sql-retry", "1", accepted=True)
        self.unchanged_rows(successor_rows, "actual-sql-retry")

        controller, origin = self.start("actual-v1-reopen", "1")
        assert (
            request(origin, self.controllers.admin, "/v1/resource-quotas")[1]["registry"]
            == update["registry"]
        )
        assert request(
            origin, self.controllers.admin, "/v1/resource-quotas", self.original_command
        ) == (200, self.receipt)
        assert request(origin, self.controllers.admin, "/v1/inventory")[1]["nodes"] == []
        self.controllers.finish(controller)
        broken = json.loads(json.dumps(current_data))
        del broken["recovery"]["metadata"]["archive_authority_id"]
        broken_path = self.output / "missing-original-marker.json"
        save(broken_path, broken)
        new_directory = self.output / "refused-new-target"
        new_directory.mkdir()
        self.execute(
            [str(self.probe), "--complete-original", str(new_directory), str(broken_path), "1"],
            "missing-marker-before-target",
            accepted=False,
        )
        assert not (new_directory / "controller.sqlite3").exists()
        for storage in ("legacy", "1"):
            directory = self.output / ("actual-fresh-" + storage)
            directory.mkdir()
            self.execute(
                [
                    str(self.probe),
                    str(directory),
                    str(empty),
                    storage,
                    self.schema,
                    "declared-local-check",
                ],
                "fresh-empty-" + storage,
                accepted=True,
            )
            assert rows(directory / "controller.sqlite3")["control_command_journal"] == []
        assert all(self.digest(Path(path)) == checksum for path, checksum in self.binaries.items())
        return {
            "passed": True,
            "scope": "actual local SQL installation; no accepted quorum or online HA claim",
            "binary_sha256": self.binaries,
            "original_sql_inode": self.inode,
            "final_sql_inode": self.database.stat().st_ino,
            "original_commands": len(data["commands"]),
            "retained_successor_commands": len(current_data["commands"]),
            "actual_projection_processes": len(self.commands),
            "original_quota_revision": 1,
            "actual_successor_quota_revision": 2,
            "nodes": 0,
            "devices": 0,
            "models": 0,
            "simulators": 0,
        }


def run(output: Path, controller: Path, probe: Path, state: Path, holder: Path) -> dict[str, Any]:
    """Retain original commands and native waits, including partial check failures."""
    output.mkdir(parents=True, exist_ok=False)
    check = ProjectionCheck(output, controller, probe, state, holder)
    result: dict[str, Any] = {"passed": False}
    try:
        result = check.verify()
    finally:
        check.controllers.close()
        result["actual_controllers"] = [record for _, record in check.controllers.children]
        result["actual_alias_holders"] = check.holder_waits
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require an explicit new directory and maintained binaries; discover no live services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller", type=Path, default=ROOT / "target/debug/integration-server")
    parser.add_argument(
        "--probe", type=Path, default=ROOT / "target/debug/examples/projection_probe"
    )
    parser.add_argument(
        "--state-probe", type=Path, default=ROOT / "target/debug/examples/replica_state_probe"
    )
    parser.add_argument(
        "--holder-probe", type=Path, default=ROOT / "target/debug/examples/controller_storage_probe"
    )
    arguments = parser.parse_args()
    result = run(
        arguments.output.absolute(),
        arguments.controller.resolve(strict=True),
        arguments.probe.resolve(strict=True),
        arguments.state_probe.resolve(strict=True),
        arguments.holder_probe.resolve(strict=True),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
