"""Actual software CPU calendars, original process waits and independent SQL; zero devices."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ComputeServer, plan, request, save, wait_for
from topology_recovery_processes import recover


class CalendarCheck:
    """Compose the actual file worker with explicit local Controller booking authority."""

    def __init__(self, directory: Path, origin: str, node: str, provider: ComputeServer) -> None:
        """Retain only original actual process/profile identities supplied by the native caller."""
        self.directory, self.origin, self.node, self.provider = directory, origin, node, provider
        self.registry: dict[str, Any] = {}
        self.original: dict[str, Any] = {}
        self.recovery: dict[str, Any] = {}

    def configuration(self, source: str) -> str:
        """Declare the actual software worker temporal slot, without a physical resource."""
        return (
            source + f'\n[[resources]]\nid = "{self.node}-cpu-opening"\n'
            'kind = "time"\ncapacity = 1\nowner = "posix-compute"\n'
        )

    def install(self) -> None:
        """Freeze a declared future opening from the actual Controller's own read-only clock."""
        status, view = request(self.origin, "/v1/resource-topology")
        assert status == 200 and view["registry"] is None
        now = view["projected_at_ms"]
        self.registry = {
            "schema": "roboguide.resource-topology/v0.1",
            "registry_id": "owned-file-worker-calendar",
            "revision": 1,
            "clock": "controller_receive_time",
            "stop_timeout_ms": 2000,
            "booking_duration_ms": 25000,
            "booking_max_age_ms": 60000,
            "resources": [
                {
                    "resource_id": self.node + "-cpu-opening",
                    "node_id": self.node,
                    "kind": "Time",
                    "space": None,
                    "time_domain": "owned-software-cpu/" + self.node,
                    "windows": [{"starts_at": now + 2500, "ends_at": now + 60000}],
                }
            ],
        }
        command = {
            "command_id": "original-software-calendar",
            "expected_revision": 0,
            "registry": self.registry,
        }
        status, receipt = request(self.origin, "/v1/resource-topology", command)
        assert status == 200 and receipt["receipt"]["revision"] == 1
        assert request(self.origin, "/v1/resource-topology", command)[1] == receipt
        save(
            self.directory / "calendar-policy.json", {"input": command, "original_receipt": receipt}
        )

    def mission_plan(self, source: dict[str, Any]) -> dict[str, Any]:
        """Request semantic Time beside Compute without a Node or duration in intent."""
        result = copy.deepcopy(source)
        for task in result["tasks"]:
            task["roles"][0]["requirements"]["resources"].append({"kind": "time", "units": 1})
        return result

    def checkpoint(self) -> dict[str, Any]:
        """Read the actual committed wrapper through a separate read-only SQLite connection."""
        with closing(
            sqlite3.connect((self.directory / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            schema, raw = sql.execute(
                "SELECT schema,checkpoint_json FROM controller_checkpoint"
            ).fetchone()
        assert schema in {
            "roboguide.controller-checkpoint/v58",
            "roboguide.controller-checkpoint/v59",
        }
        result: dict[str, Any] = json.loads(raw)
        return result

    def time_reservation(self) -> dict[str, Any] | None:
        """Inspect the original same-reservation evidence independently of public passed labels."""
        control = json.loads(self.checkpoint()["integration_json"])["control"]
        held = control["reservations"].get(self.node + "-cpu-opening")
        return None if held is None else dict(held)

    def freeze(self) -> None:
        """Keep the original actual CPU child alive before read while freezing its exact booking."""
        wait_for(
            lambda: len(self.provider.jobs) == 1 and self.time_reservation() is not None,
            "actual CPU execution inside its declared opening",
        )
        original = self.time_reservation()
        assert original is not None and original["group_id"] is not None
        proof = original["topology"]
        assert proof["revision"] == 1 and proof["entry"] == self.registry["resources"][0]
        assert proof["admitted_at"] >= proof["entry"]["windows"][0]["starts_at"]
        assert proof["planned_end"] - proof["admitted_at"] <= 25000
        identity, (process, _, _) = next(iter(self.provider.jobs.items()))
        assert process.poll() is None and identity in self.provider.read_permits
        self.original = {"reservation": original, "pid": process.pid, "execution_id": identity}
        save(self.directory / "calendar-original.json", self.original)

    def quiescent_recovery(self, old_controller: Path | None) -> None:
        """Recover the active booking after original writer waits."""
        self.recovery = recover(
            self.directory,
            self.directory.with_name(self.directory.name + "-active-recovery"),
            old_controller,
        )

    def restored(self) -> None:
        """Restart original owners without extending the booking or replaying external CPU work."""
        wait_for(
            lambda: self.time_reservation() == self.original["reservation"],
            "same original booking after restart",
        )
        process = self.provider.jobs[self.original["execution_id"]][0]
        assert process.pid == self.original["pid"] and process.poll() is None
        assert len(self.provider.invocations) == 1

    def release(self, task: str) -> None:
        """Permit the original Task child to read before independent verification."""
        wait_for(
            lambda: any(
                invocation["task_id"] == task for _, _, invocation in self.provider.jobs.values()
            ),
            "actual original calendar Task before read",
        )
        identity = next(
            identity
            for identity, (_, _, invocation) in self.provider.jobs.items()
            if invocation["task_id"] == task
        )
        self.provider.release_read(identity)

    def finish(self) -> dict[str, Any]:
        """Observe actual overrun, child exit and retained unconfirmed stopping."""
        assert self.time_reservation() is None
        _, view = request(self.origin, "/v1/resource-topology")
        second = copy.deepcopy(self.registry)
        second["revision"], second["booking_duration_ms"] = 2, 300
        second["resources"][0]["windows"] = [
            {"starts_at": view["projected_at_ms"], "ends_at": view["projected_at_ms"] + 30000}
        ]
        status, receipt = request(
            self.origin,
            "/v1/resource-topology",
            {"command_id": "original-overrun-calendar", "expected_revision": 1, "registry": second},
        )
        assert status == 200, receipt
        source = self.mission_plan(plan("calendar-overrun-" + self.node))
        source["tasks"] = [source["tasks"][0]]
        assert request(self.origin, "/v1/missions", source)[0] == 202
        wait_for(lambda: len(self.provider.jobs) == 3, "original overrun child before read")
        identity, (child, _, invocation) = next(
            (identity, job)
            for identity, job in self.provider.jobs.items()
            if job[2]["mission_id"] == source["mission"]["id"]
        )
        assert identity in self.provider.read_permits and invocation["task_id"] == "hash-0"
        wait_for(
            lambda: child.poll() is not None, "actual original overrun cancellation and OS exit"
        )
        native_wait = child.wait(timeout=5)
        assert native_wait < 0
        wait_for(
            lambda: bool(self.checkpoint()["safety_stops"]),
            "durable original booking stop evidence",
        )
        wrapper = self.checkpoint()
        stops = copy.deepcopy(wrapper["safety_stops"])
        held = self.time_reservation()
        assert held is not None and held["topology"]["revision"] == 2
        assert all(
            purpose["record"]["phase"] not in {"Confirmed", "confirmed"}
            for purpose in stops.values()
        )
        # This old software provider deliberately declares no independent safe-state export.
        # Its actual wait is checker evidence; Controller must retain the unresolved stop/resource.
        before = {key: value["record"]["deadline"] for key, value in stops.items()}
        assert request(self.origin, "/v1/resource-topology")[1]["reservations"]
        after = self.checkpoint()["safety_stops"]
        assert {key: value["record"]["deadline"] for key, value in after.items()} == before
        result = {
            "passed": True,
            "original": self.original,
            "active_recovery": self.recovery,
            "overrun": {
                "execution_id": identity,
                "pid": child.pid,
                "native_wait": native_wait,
                "held_reservation": held,
                "original_stops": stops,
                "controller_stop_confirmation": "unavailable",
            },
            "software_nodes": 1,
            "devices": 0,
            "physical_space": "unavailable",
            "external_models": 0,
        }
        save(self.directory / "calendar-result.json", result)
        return result
