"""Inspect only authenticated RG State for reported posture; never dispatch a native task."""

from __future__ import annotations

import argparse
import json
import math
import ssl
from pathlib import Path
from typing import Any

from robonix_task.position import read_controller

CONTRACT = "robonix/primitive/chassis/state"
MAX_AGE_MS = 5000


def inspect_posture(
    state: dict[str, Any], inventory: dict[str, Any], *, node_id: str
) -> dict[str, Any]:
    """Require exact RG source identity and both Controller and aggregate freshness evidence."""
    result: dict[str, Any] = {
        "schema_version": "roboguide.reported-posture-inspection/v0.1",
        "node_id": node_id,
        "status": "unavailable",
        "posture": None,
        "motion_authorized": False,
        "physical_effect_verified": False,
        "limitations": [
            "individual_posture_measurement_time_unavailable",
            "damping_and_remote_connection_not_provided",
            "publisher_to_controller_transport_age_not_measured",
        ],
    }

    def refuse(reason: str) -> dict[str, Any]:
        """Retain the reason while withholding posture on missing or expired evidence."""
        return {**result, "reason": reason}

    if (
        not node_id
        or state.get("schema") != "roboguide.state-query/v0.1"
        or inventory.get("schema_version") != "roboguide.inventory/v0.1"
    ):
        return refuse("unsupported_response_or_missing_node")
    now = state.get("observed_at_ms")
    nodes, records = inventory.get("nodes"), state.get("records")
    if type(now) is not int or not isinstance(nodes, list) or not isinstance(records, list):
        return refuse("missing_controller_observation")
    selected = [n for n in nodes if isinstance(n, dict) and n.get("node_id") == node_id]
    if len(selected) != 1 or selected[0].get("liveness") != "Reachable":
        return refuse("node_unreachable_or_ambiguous")
    expected = {"class": "node", "object_type": "robot", "object_id": node_id}
    selected = [
        r
        for r in records
        if isinstance(r, dict)
        and r.get("object") == expected
        and r.get("source") == f"node:{node_id}/robot-observer"
        and r.get("channel_id") == "robot-state"
        and r.get("semantic") == "reported"
    ]
    if len(selected) != 1:
        return refuse("exact_state_source_missing_or_ambiguous")
    record = selected[0]
    envelope = record.get("value")
    if not isinstance(envelope, dict) or envelope.get("payload_schema") != "lite3.robot-state/v0.1":
        return refuse("unsupported_state_payload")
    received, ttl = envelope.get("received_at_ms"), envelope.get("valid_for_ms")
    if (
        record.get("stale") is not False
        or type(received) is not int
        or type(ttl) is not int
        or not 0 <= now - received < ttl
        or not envelope.get("source_epoch")
        or type(envelope.get("sequence")) is not int
        or envelope["sequence"] <= 0
    ):
        return refuse("controller_state_stale_or_unqualified")
    snapshot = envelope.get("value")
    chassis = snapshot.get("chassis") if isinstance(snapshot, dict) else None
    if not isinstance(chassis, dict) or chassis.get("contract_id") != CONTRACT:
        return refuse("chassis_state_unavailable")
    age = chassis.get("age_ms")
    value = chassis.get("value")
    source_ttl = value.get("stale_ms") if isinstance(value, dict) else None
    if (
        chassis.get("availability") != "current"
        or not isinstance(age, int | float)
        or isinstance(age, bool)
        or not math.isfinite(age)
        or age < 0
        or not isinstance(value, dict)
        or value.get("stale") is not False
        or not isinstance(source_ttl, int | float)
        or isinstance(source_ttl, bool)
        or not math.isfinite(source_ttl)
        or source_ttl <= 0
        or age + now - received >= min(MAX_AGE_MS, source_ttl)
    ):
        return refuse("chassis_state_stale_or_age_missing")
    if value.get("posture") not in {"standing", "down"}:
        return refuse("reported_posture_unknown")
    result.update(
        status="reported",
        posture=value["posture"],
        battery=value.get("battery"),
        reason="bounded_age_robonix_aggregate",
        evidence={
            "source": record["source"],
            "channel_id": record["channel_id"],
            "source_epoch": envelope["source_epoch"],
            "sequence": envelope["sequence"],
            "controller_received_at_ms": received,
            "controller_observed_at_ms": now,
            "aggregate_age_plus_controller_retention_ms": age + now - received,
            "provider_id": chassis.get("provider_id"),
            "registration_id": chassis.get("registration_id"),
            "chassis_received_at_ns": chassis.get("received_at_ns"),
            "remaining_valid_for_ms": math.floor(
                min(ttl - (now - received), min(MAX_AGE_MS, source_ttl) - age - (now - received))
            ),
            "original_value": value,
        },
    )
    return result


def main() -> None:
    """GET only RG inventory and State with an existing authenticated operator identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-origin", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=str(args.ca))
    context.load_cert_chain(str(args.cert), str(args.key))
    inventory = read_controller(args.controller_origin, "/v1/inventory", context)
    state = read_controller(
        args.controller_origin, "/v1/state/records?object_class=node&object_type=robot", context
    )
    result = inspect_posture(state, inventory, node_id=args.node_id)
    document = {"result": result, "state": state, "inventory": inventory}
    if args.output is not None:
        args.output.write_text(json.dumps(document, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
