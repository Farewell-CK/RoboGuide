"""Independently verify original spatial/calendar authority without creating resource inventory."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .capacity_recovery import _checkpoint, _closed, _integer

WRAPPER = "roboguide.controller-checkpoint/v58"


def _space(value: Any) -> dict[str, Any]:
    """Require exact references and representable integer-millimetre clearance inflation."""
    result = _closed(
        value,
        {"conflict_domain", "frame", "anchor_revision", "lower_mm", "upper_mm", "clearance_mm"},
    )
    if any(
        not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > 256
        for key in ("conflict_domain", "frame", "anchor_revision")
    ) or not _integer(result["clearance_mm"], (1 << 32) - 1):
        raise ValueError("topology original spatial reference or clearance differs")
    for key in ("lower_mm", "upper_mm"):
        if not isinstance(result[key], list) or len(result[key]) != 3:
            raise ValueError("topology original footprint requires three exact axes")
        if any(type(item) is not int or not -(1 << 63) <= item < 1 << 63 for item in result[key]):
            raise ValueError("topology original spatial coordinates exceed signed bounds")
    for lower, upper in zip(result["lower_mm"], result["upper_mm"], strict=True):
        if (
            lower >= upper
            or lower - result["clearance_mm"] < -(1 << 63)
            or upper + result["clearance_mm"] >= 1 << 63
        ):
            raise ValueError("topology original spatial bounds or inflated extent differs")
    return result


def _entry(value: Any) -> dict[str, Any]:
    """Validate a complete exact declaration and canonical local Controller openings."""
    result = _closed(value, {"resource_id", "node_id", "kind", "space", "time_domain", "windows"})
    if any(
        not isinstance(result[key], str) or not result[key].strip()
        for key in ("resource_id", "node_id")
    ):
        raise ValueError("topology original deployment resource/Node identity is absent")
    if result["kind"] == "Space":
        if result["time_domain"] is not None:
            raise ValueError("spatial footprint cannot hide a shared calendar authority")
        _space(result["space"])
    elif (
        result["kind"] != "Time"
        or result["space"] is not None
        or not result["windows"]
        or not isinstance(result["time_domain"], str)
        or not result["time_domain"].strip()
        or len(result["time_domain"]) > 256
    ):
        raise ValueError("topology original resource kind and footprint disagree")
    if not isinstance(result["windows"], list) or len(result["windows"]) > 128:
        raise ValueError("topology original calendar exceeds its closed bound")
    previous = None
    for window in result["windows"]:
        _closed(window, {"starts_at", "ends_at"})
        if (
            not _integer(window["starts_at"])
            or not _integer(window["ends_at"])
            or window["starts_at"] >= window["ends_at"]
            or previous is not None
            and previous >= window["starts_at"]
        ):
            raise ValueError("topology original openings overlap or have invalid bounds")
        previous = window["ends_at"]
    return result


def _registry(value: Any) -> dict[str, dict[str, Any]]:
    """Refuse hidden clock translation, ambiguous frames and noncanonical resource identities."""
    result = _closed(
        value,
        {
            "schema",
            "registry_id",
            "revision",
            "clock",
            "stop_timeout_ms",
            "booking_duration_ms",
            "booking_max_age_ms",
            "resources",
        },
    )
    if (
        result["schema"] != "roboguide.resource-topology/v0.1"
        or result["clock"] != "controller_receive_time"
        or not isinstance(result["registry_id"], str)
        or not result["registry_id"].strip()
        or len(result["registry_id"]) > 128
        or not _integer(result["revision"])
        or result["revision"] == 0
        or not _integer(result["stop_timeout_ms"], 3_600_000)
        or result["stop_timeout_ms"] == 0
        or not _integer(result["booking_duration_ms"], 3_600_000)
        or result["booking_duration_ms"] == 0
        or not _integer(result["booking_max_age_ms"], 604_800_000)
        or result["booking_max_age_ms"] < result["booking_duration_ms"]
        or not isinstance(result["resources"], list)
        or len(result["resources"]) > 4096
    ):
        raise ValueError("topology original registry schema, clock or bounds differ")
    entries: dict[str, dict[str, Any]] = {}
    frames: dict[str, tuple[str, str]] = {}
    previous = ""
    for raw in result["resources"]:
        entry = _entry(raw)
        if entry["resource_id"] <= previous:
            raise ValueError("topology original resource identities are not canonical")
        previous = entry["resource_id"]
        entries[previous] = entry
        if entry["space"] is not None:
            space = entry["space"]
            reference = space["frame"], space["anchor_revision"]
            if frames.setdefault(space["conflict_domain"], reference) != reference:
                raise ValueError("topology original conflict domain changed its frame or anchor")
    return entries


def _permits(entry: dict[str, Any], start: int, end: int | None) -> bool:
    """Check the entire original interval; a planning end never becomes release evidence."""
    return (
        _integer(start)
        and (end is None or _integer(end) and start < end)
        and (
            not entry["windows"]
            or end is not None
            and any(
                window["starts_at"] <= start and end <= window["ends_at"]
                for window in entry["windows"]
            )
        )
    )


def _conflicts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare exact validated declarations, treating inclusive clearance contact as exclusion."""
    if left["resource_id"] == right["resource_id"]:
        return True
    if left["time_domain"] is not None and left["time_domain"] == right["time_domain"]:
        return True
    first, second = left["space"], right["space"]
    if first is None or second is None or first["conflict_domain"] != second["conflict_domain"]:
        return False
    return all(
        first["lower_mm"][axis] - first["clearance_mm"]
        <= second["upper_mm"][axis] + second["clearance_mm"]
        and second["lower_mm"][axis] - second["clearance_mm"]
        <= first["upper_mm"][axis] + first["clearance_mm"]
        for axis in range(3)
    )


def _booking(decision: dict[str, Any], history: dict[str, Any], missions: list[Any]) -> None:
    """Bind deployment booking to original policy and Mission time without renewal."""
    policy = history[str(max(int(key) for key in history))]
    estimate = decision.get("duration_estimate")
    if estimate is not None and estimate.get("source", {}).get("owner") == "robo_guide":
        component = estimate["source"].get("component", "")
        if component.startswith("resource-topology-booking/"):
            original = [
                value
                for value in history.values()
                if component
                == f"resource-topology-booking/{value['registry_id']}/{value['revision']}"
            ]
            if len(original) != 1:
                raise ValueError("booking source lacks its exact original deployment policy")
            policy = original[0]
            accepted = [
                mission["accepted_at_ms"]
                for mission in missions
                if mission["plan"]["mission"]["id"] == decision["task_ref"]["mission_id"]
            ]
            if (
                len(accepted) != 1
                or estimate["task_ref"] != decision["task_ref"]
                or estimate["received_at"] != accepted[0]
                or estimate["duration_ms"] != policy["booking_duration_ms"]
                or estimate["valid_for_ms"] != policy["booking_max_age_ms"]
                or decision["starts_at"] < accepted[0]
                or decision["ends_at"] is None
                or decision["ends_at"] - accepted[0] > policy["booking_max_age_ms"]
            ):
                raise ValueError("booking changed original Mission age or declared budget")
    if (
        decision["ends_at"] is not None
        and not 0 < decision["ends_at"] - decision["starts_at"] <= policy["booking_duration_ms"]
    ):
        raise ValueError("booking exceeds its original deployment duration")


def validate(connection: sqlite3.Connection) -> None:
    """Verify complete original topology and reservation joins under the existing bundle locks."""
    wrapper = _checkpoint(connection)
    if wrapper is None:
        return
    control = json.loads(wrapper["integration_json"])["control"]
    if wrapper["schema"] not in {
        WRAPPER,
        "roboguide.controller-checkpoint/v59",
        "roboguide.controller-checkpoint/v60",
    }:
        if (
            control.get("resource_topology_registry") is not None
            or control.get("resource_topology_history")
            or any(
                record.get("topology_recovery_assignments")
                for record in control.get("scheduled_tasks", [])
            )
        ):
            raise ValueError("old Controller wrapper hides spatial/calendar authority")
        return
    if not {"resource_topology_registry", "resource_topology_history"} <= control.keys():
        raise ValueError("current Controller lacks mandatory topology reference tables")
    history = control["resource_topology_history"]
    if not isinstance(history, dict) or len(history) > 1024:
        raise ValueError("topology original history exceeds its closed bound")
    previous = None
    declarations = {}
    for revision, registry in sorted(history.items(), key=lambda pair: int(pair[0])):
        declarations[revision] = _registry(registry)
        if str(registry["revision"]) != revision or (
            registry["revision"] != 1
            if previous is None
            else (
                registry["registry_id"] != previous["registry_id"]
                or registry["revision"] != previous["revision"] + 1
            )
        ):
            raise ValueError("topology original policy history is discontinuous")
        previous = registry
    if previous != control["resource_topology_registry"]:
        raise ValueError("topology current policy lacks original complete history")
    current = {} if previous is None else declarations[str(previous["revision"])]
    active = []
    for identity, reservation in control["reservations"].items():
        evidence = reservation.get("topology")
        if evidence is None and identity not in current:
            continue
        if not isinstance(evidence, dict):
            raise ValueError("held resource lost its original topology reservation")
        _closed(evidence, {"revision", "entry", "admitted_at", "planned_end"})
        if (
            not _integer(evidence["revision"])
            or evidence["entry"] != current.get(identity)
            or evidence["entry"] != declarations.get(str(evidence["revision"]), {}).get(identity)
            or not _permits(evidence["entry"], evidence["admitted_at"], evidence["planned_end"])
        ):
            raise ValueError("held topology changed original declaration, policy or interval")
        active.append(evidence["entry"])
    for index, entry in enumerate(active):
        if any(_conflicts(entry, other) for other in active[index + 1 :]):
            raise ValueError("active original spatial reservations intersect")
    future: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for reservation in control.get("scheduled_tasks", []):
        decision = reservation["decision"]
        overrides = reservation.get("topology_recovery_assignments", {})
        if not isinstance(overrides, dict) or len(overrides) > len(decision["selections"]):
            raise ValueError("calendar recovery projection exceeds original roles")
        for role, replacement in overrides.items():
            original = [
                selection for selection in decision["selections"] if selection["role_id"] == role
            ]
            if (
                previous is None
                or reservation["phase"] != "Activated"
                or len(original) != 1
                or replacement["role_id"] != role
                or len(replacement["resource_ids"]) != len(original[0]["resource_ids"])
            ):
                raise ValueError("calendar recovery changed original roles or active authority")
        if reservation["phase"] != "Invalidated" and any(
            identity in current
            for selection in decision["selections"]
            for identity in selection["resource_ids"]
        ):
            _booking(decision, history, json.loads(wrapper.get("orchestration_json", "[]")))
        if reservation["phase"] != "Scheduled":
            continue
        chosen: list[dict[str, Any]] = []
        for selection in decision["selections"]:
            for identity in selection["resource_ids"]:
                if identity not in current:
                    continue
                entry = current[identity]
                if entry["node_id"] != selection["node_id"] or not _permits(
                    entry, decision["starts_at"], decision["ends_at"]
                ):
                    raise ValueError("future topology owner or opening differs")
                if any(_conflicts(entry, other) for other in chosen):
                    raise ValueError("future joint spatial declarations intersect")
                chosen.append(entry)
                for prior, other in future:
                    if (
                        _conflicts(entry, other)
                        and decision["starts_at"] < prior["ends_at"]
                        and prior["starts_at"] < decision["ends_at"]
                    ):
                        raise ValueError("future topology calendar reservations intersect")
        future.extend((decision, entry) for entry in chosen)
