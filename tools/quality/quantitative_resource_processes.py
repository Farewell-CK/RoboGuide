"""Actual process-slot conformance with zero devices, simulators or external models."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, ComputeServer, address, request, save, wait_for
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from recovery_profile_selection import standalone_schema

RESOURCE_CLASS = "software.process-slots/v1"


def configuration(source: str) -> str:
    """Select the explicit contract and exactly two declared actual local process slots."""
    return (
        source.replace("node-config/v0.7", "node-config/v0.14")
        .replace('local_locks = ["file-hasher"]', "local_locks = []")
        .replace(
            'capacity = 1\nowner = "posix-compute"',
            'capacity = 2\nallocation_mode = "divisible"\n'
            f'resource_class = "{RESOURCE_CLASS}"\nowner = "posix-compute"',
        )
    )


def mission_plan(source: dict[str, Any]) -> dict[str, Any]:
    """Declare three independent computations using existing declared process slots."""
    result = copy.deepcopy(source)
    result["schema_version"] = "roboguide.mission-plan/v0.10"
    result["mission"]["objective"] = "independently verify all declared file computations"
    result["tasks"].append(copy.deepcopy(result["tasks"][-1]))
    for index, task in enumerate(result["tasks"]):
        task["id"], task["depends_on"] = f"hash-{index}", []
        for role in task["roles"]:
            role["requirements"]["resources"] = [
                {
                    "kind": "compute",
                    "units": 1,
                    "allocation_mode": "divisible",
                    "resource_class": RESOURCE_CLASS,
                }
            ]
    return result


class QuantitativeCheck:
    """Observe committed Control, original Node journals and actual owned children."""

    def __init__(self, directory: Path, origin: str, provider: ComputeServer, mission: str) -> None:
        """Retain only this gate's original process provider and explicit local source locations."""
        self.directory, self.origin, self.provider, self.mission = (
            directory,
            origin,
            provider,
            mission,
        )
        self.original: dict[str, Any] = {}
        self.original_claims: list[dict[str, Any]] = []
        self.processes: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.recovery: dict[str, Any] = {}

    def control(self) -> dict[str, Any]:
        """Read one atomic committed SQL checkpoint independently of HTTP labels."""
        with closing(
            sqlite3.connect((self.directory / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as connection:
            schema, raw = connection.execute(
                "SELECT schema,checkpoint_json FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
        if schema not in {
            "roboguide.controller-checkpoint/v56",
            "roboguide.controller-checkpoint/v57",
            "roboguide.controller-checkpoint/v58",
            "roboguide.controller-checkpoint/v59",
        }:
            raise ValueError("actual quantitative gate requires the original v56 full checkpoint")
        wrapper = json.loads(raw)
        return dict(json.loads(wrapper["integration_json"])["control"])

    def attempts(self) -> list[dict[str, Any]]:
        """Observe original Mission attempts through the explicit local profile."""
        return list(request(self.origin, "/v1/execution-attempts")[1]["attempts"])

    def freeze(self) -> None:
        """Freeze original children and exact parent, borrower and quota bytes."""
        wait_for(lambda: len(self.provider.jobs) == 2, "two actual process slots before first read")
        wait_for(
            lambda: len(self.control()["capacity_ledger"]["allocations"]) == 2,
            "two original quantitative Commit records",
        )
        wait_for(lambda: self.node_originals_ready(), "durable original Node grants before restart")
        control = self.control()
        self.original = copy.deepcopy(control["capacity_ledger"])
        self.original_claims = copy.deepcopy(control["quota_ledger"]["claims"])
        status, view = request(self.origin, "/v1/resource-allocations")
        assert status == 200 and view["schema_version"] == "roboguide.resource-allocation-view/v0.1"
        assert {item["quantitative_grant"]["allocation_id"] for item in view["allocations"]} == set(
            self.original["allocations"]
        )

        assert self.original["generation"] == 2
        assert sum(claim["units"] for claim in self.original_claims) == 2
        assert not any(attempt["task_id"] == "hash-2" for attempt in self.attempts())
        validator = Draft202012Validator(
            json.loads(
                (ROOT / "contracts/node/v0.16/resource-allocation-grant.schema.json").read_bytes()
            )
        )
        for identity, (process, _, invocation) in self.provider.jobs.items():
            assert process.poll() is None and identity in self.provider.read_permits
            grants = invocation["resource_grants"]
            assert len(grants) == 1 and grants[0]["units"] == 1
            validator.validate(grants[0])
            assert grants[0]["allocation_id"] in self.original["allocations"]
            assert grants[0]["resource_class"] == RESOURCE_CLASS
            assert grants[0]["task_ref"]["mission_id"] == self.mission
            self.processes.append(
                {"execution_id": identity, "pid": process.pid, "grant": grants[0]}
            )
        save(
            self.directory / "quantitative-original.json",
            {
                "ledger": self.original,
                "claims": self.original_claims,
                "actual_owned_processes_before_read": self.processes,
            },
        )

    def node_originals_ready(self) -> bool:
        """Read original durable handles and exact grant bytes before stopping the Node process."""
        with closing(
            sqlite3.connect(
                (self.directory / "node-state/execution-journal.sqlite3").as_uri() + "?mode=ro",
                uri=True,
            )
        ) as connection:
            rows = connection.execute(
                "SELECT execution_id,invocation_content,local_handle,status "
                "FROM execution_authority_v7"
            ).fetchall()
            marker = connection.execute(
                "SELECT version FROM node_journal_storage_schema"
            ).fetchone()
        assert marker in {(14,), (15,)}
        current = {identity: (raw, handle, status) for identity, raw, handle, status in rows}
        return len(current) == 2 and all(
            identity in current
            and current[identity][1] is not None
            and current[identity][2] in {"accepted", "running"}
            and json.loads(current[identity][0])["resource_grants"]
            for identity in self.provider.jobs
        )

    def restored(self) -> None:
        """Require unchanged grants and original actual children after Controller restart."""
        wait_for(
            lambda: self.control()["capacity_ledger"] == self.original,
            "exact original allocation recovery",
        )
        assert self.control()["quota_ledger"]["claims"] == self.original_claims
        assert len(self.provider.jobs) == 2 and len(self.provider.invocations) == 2
        for record in self.processes:
            process = self.provider.jobs[record["execution_id"]][0]
            assert process.pid == record["pid"] and process.poll() is None
        assert not any(attempt["task_id"] == "hash-2" for attempt in self.attempts())
        save(
            self.directory / "quantitative-restored.json",
            {
                "ledger": self.control()["capacity_ledger"],
                "same_original_owned_pids": self.processes,
            },
        )

    def quiescent_recovery(self, previous_controller: Path | None) -> None:
        """Use the shipped CLI on stopped writers without granting restored execution authority."""
        output = self.directory.with_name(self.directory.name + "-recovery")
        output.mkdir(mode=0o700)
        profile: dict[str, Any] = {
            "schema": standalone_schema(self.directory / "controller.sqlite3"),
            "profile_id": "original-quantitative-file-process-stores",
            "revision": 1,
            "max_bytes": 1024 * 1024 * 1024,
            "max_files": 10000,
            "budget_seconds": 20,
            "artifact_replica_sets": [],
            "components": [
                {
                    "component_id": identifier,
                    "kind": kind,
                    "root": str(root),
                    "database": database,
                    "trees": [],
                    "depends_on": dependencies,
                    **({"storage_schema": schema} if schema else {}),
                }
                for identifier, kind, root, database, dependencies, schema in [
                    (
                        "controller",
                        "controller",
                        self.directory,
                        "controller.sqlite3",
                        ["cas"],
                        "roboguide.controller-storage/v1",
                    ),
                    (
                        "cas",
                        "artifact",
                        self.directory / "cas",
                        None,
                        [],
                        "roboguide.artifact-storage/v3",
                    ),
                    (
                        "node",
                        "node",
                        self.directory / "node-state",
                        "execution-journal.sqlite3",
                        ["controller"],
                        None,
                    ),
                ]
            ],
        }
        save(output / "profile.json", profile)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        }
        environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")

        def command(*arguments: str) -> dict[str, Any]:
            """Retain actual CLI receipts or the exact bounded failure output."""
            completed = subprocess.run(
                [sys.executable, "-m", "roboguide_recovery", *arguments],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=22,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.decode(errors="replace"))
            result = json.loads(completed.stdout)
            assert isinstance(result, dict)
            return result

        backup_arguments = (
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "original-capacity-backup",
        )
        created = command(*backup_arguments)
        save(output / "backup-receipt.json", created)
        bundle_arguments = (
            "--bundle",
            str(output / "bundle"),
            "--manifest-digest",
            created["manifest_digest"],
        )
        assert command("verify", *bundle_arguments)["verified"] is True
        restore_arguments = (
            "restore",
            *bundle_arguments,
            "--destination",
            str(output / "restored"),
            "--command-id",
            "original-capacity-restore",
        )
        receipt = command(*restore_arguments)
        save(output / "restore-receipt.json", receipt)
        assert receipt["schema"] == profile["schema"].replace("recovery-profile", "restore-receipt")
        assert command(*backup_arguments) == created
        assert command(*restore_arguments) == receipt
        roots = {item["component_id"]: Path(item["root"]) for item in receipt["components"]}
        with closing(sqlite3.connect(roots["controller"] / "controller.sqlite3")) as connection:
            schema, raw = connection.execute(
                "SELECT schema,checkpoint_json FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
        assert schema in {
            "roboguide.controller-checkpoint/v56",
            "roboguide.controller-checkpoint/v57",
            "roboguide.controller-checkpoint/v58",
            "roboguide.controller-checkpoint/v59",
        }
        control = json.loads(json.loads(raw)["integration_json"])["control"]
        assert control["capacity_ledger"] == self.original
        assert control["quota_ledger"]["claims"] == self.original_claims
        source = self.directory / "node-state/execution-journal.sqlite3"
        restored = roots["node"] / "execution-journal.sqlite3"
        for path in (source, restored):
            with closing(sqlite3.connect(path)) as connection:
                assert connection.execute(
                    "SELECT version FROM node_journal_storage_schema"
                ).fetchone() in {(14,), (15,)}
                rows = connection.execute(
                    "SELECT invocation_content,local_handle FROM execution_authority_v7"
                ).fetchall()
            grants = [json.loads(row[0])["resource_grants"][0] for row in rows]
            assert {grant["allocation_id"] for grant in grants} == set(self.original["allocations"])
            assert len(rows) == 2 and all(row[1] is not None for row in rows)
        old_refusal: dict[str, Any] | None = None
        if previous_controller is not None:
            binary = previous_controller.resolve(strict=True)
            schema_result = subprocess.run(
                [str(binary), "--application-schema"], capture_output=True, timeout=5, check=True
            )
            old_schema = schema_result.stdout.decode().strip()
            assert old_schema == "roboguide.controller-checkpoint/v55"
            old_environment = environment | {
                "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA": "1",
                "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA": "roboguide.artifact-storage/v3",
            }
            negative = subprocess.run(
                [
                    str(binary),
                    address(),
                    str(roots["controller"] / "controller.sqlite3"),
                    address(),
                    address(),
                    str(roots["cas"]),
                ],
                cwd=ROOT,
                env=old_environment,
                capture_output=True,
                timeout=5,
            )
            save(
                output / "previous-controller-output.json",
                {
                    "stdout": negative.stdout.decode(errors="replace"),
                    "stderr": negative.stderr.decode(errors="replace"),
                },
            )
            assert negative.returncode != 0 and b"checkpoint" in negative.stderr.lower()
            with closing(sqlite3.connect(roots["controller"] / "controller.sqlite3")) as connection:
                assert connection.execute(
                    "SELECT schema,checkpoint_json FROM controller_checkpoint WHERE singleton=1"
                ).fetchone() == (schema, raw)
            old_refusal = {
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "application_schema": old_schema,
                "exit_code": negative.returncode,
            }
        self.recovery = {
            "evidence_directory": str(output),
            "manifest_digest": created["manifest_digest"],
            "receipt_schema": receipt["schema"],
            "exact_active_allocation_and_node_grants_preserved": True,
            "duplicate_commands_observed_original_receipts": True,
            "restored_services_started": 0,
            "previous_controller_refusal": old_refusal,
            "external_local_eaios_and_input_files_in_bundle": False,
        }
        save(output / "result.json", self.recovery)

    def release(self, task: str) -> None:
        """Permit one admitted child to read; require its actual computation and exit."""
        attempts = wait_for(
            lambda: [
                attempt
                for attempt in self.attempts()
                if attempt["task_id"] == task and attempt["execution_id"] in self.provider.jobs
            ],
            "original admitted actual child for " + task,
        )
        assert len(attempts) == 1
        self.provider.release_read(attempts[0]["execution_id"])

    def completed_before_verifier(self, index: int) -> None:
        """Check that Control retains capacity before independent satisfaction."""
        control = self.control()
        records = control["capacity_ledger"]["allocations"]
        if index == 0:
            assert control["capacity_ledger"] == self.original
            assert len(self.provider.jobs) == 2
            assert not any(attempt["task_id"] == "hash-2" for attempt in self.attempts())
        assert any(
            record["original"]["task_ref"]["task_id"] == f"hash-{index}"
            for record in records.values()
        )
        self.completed.append(
            {
                "task_id": f"hash-{index}",
                "retained_allocations": sorted(records),
                "used_units": sum(claim["units"] for claim in control["quota_ledger"]["claims"]),
            }
        )

    def finish(self) -> dict[str, Any]:
        """Join final SQL and actual wait/output evidence with no device claims."""
        control = self.control()
        assert control["capacity_ledger"] == {"generation": 3, "allocations": {}}
        assert control["quota_ledger"]["claims"] == []
        assert len(self.provider.jobs) == 3 and all(
            process.poll() == 0 for process, _, _ in self.provider.jobs.values()
        )
        result = {
            "resource_class": RESOURCE_CLASS,
            "declared_process_slots": 2,
            "actual_original_processes": self.processes,
            "completion_before_independent_verifier": self.completed,
            "original_grants_survived_controller_restart": True,
            "final_generation": 3,
            "hardware_enforcement": "unavailable",
            "devices": 0,
            "quiescent_recovery": self.recovery,
        }
        save(self.directory / "quantitative-result.json", result)
        return result
