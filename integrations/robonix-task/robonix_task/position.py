"""Inspect reported robot positions through Controller State, without execution authority."""

from __future__ import annotations

import argparse
import copy
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAX_MAPPING_AGE_MS = 10_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _number(value: object) -> bool:
    """Accept finite JSON numbers while excluding booleans and unbounded integer conversion."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def annotate_mapping_age(snapshot: dict[str, Any], now_ns: int) -> dict[str, Any]:
    """Add same-host Mapping response age without refreshing or changing source coordinates.

    This qualifies the cached HTTP observation only. The Mapping response does not
    expose a pose measurement timestamp, calibrated quality, or immutable map revision.
    Missing receipts and wall-clock rollback remain unavailable; source values survive.
    """
    result = copy.deepcopy(snapshot)
    result["snapshot_read_at_ns"] = now_ns
    mapping = result.get("mapping")
    if not isinstance(mapping, dict):
        mapping = {"value": None}
        result["mapping"] = mapping
    stamp = mapping.get("received_at_ns")
    age = (
        (now_ns - stamp) / 1_000_000
        if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > 0 and now_ns >= stamp
        else None
    )
    mapping["age_ms"] = age
    mapping["availability"] = (
        "current"
        if mapping.get("value") is not None and age is not None and age <= MAX_MAPPING_AGE_MS
        else "stale"
        if age is not None and mapping.get("value") is not None
        else "unavailable"
    )
    mapping["freshness_basis"] = (
        "observer host receive time of Mapping HTTP response; "
        "pose measurement timestamp and localization quality are not provided"
    )
    return result


def inspect_position(
    state: dict[str, Any],
    inventory: dict[str, Any],
    *,
    node_id: str,
    expected_map_id: str,
    local_system_id: str = "robot-observer",
    channel_id: str = "robot-state",
) -> dict[str, Any]:
    """Select one exact Controller record; reject stale, ambiguous and mismatched observations.

    Reported coordinates are diagnostic facts, not localization verification, world
    grounding, a saved origin, or permission to move. Source clocks are not subtracted
    from Controller clocks. Missing source-age metadata fails closed for this reader.
    """
    result: dict[str, Any] = {
        "schema_version": "roboguide.reported-position-inspection/v0.1",
        "status": "unavailable",
        "node_id": node_id,
        "expected_map_id": expected_map_id,
        "position": None,
        "motion_authorized": False,
        "localization_verified": False,
        "mi_grounding_admitted": False,
    }

    def refuse(reason: str) -> dict[str, Any]:
        """Preserve the diagnostic context while withholding coordinates on uncertainty."""
        return {**result, "reason": reason}

    if not node_id or not expected_map_id:
        return refuse("explicit_node_and_map_required")
    if (
        state.get("schema") != "roboguide.state-query/v0.1"
        or inventory.get("schema_version") != "roboguide.inventory/v0.1"
    ):
        return refuse("unsupported_controller_response")
    now = state.get("observed_at_ms")
    if type(now) is not int or now < 0:
        return refuse("controller_observation_time_missing")
    nodes = inventory.get("nodes")
    if not isinstance(nodes, list):
        return refuse("inventory_unavailable")
    selected = [n for n in nodes if isinstance(n, dict) and n.get("node_id") == node_id]
    if len(selected) != 1 or selected[0].get("liveness") != "Reachable":
        return refuse("node_not_reachable_or_ambiguous")
    records = state.get("records")
    if not isinstance(records, list):
        return refuse("state_records_unavailable")
    expected_object = {"class": "node", "object_type": "robot", "object_id": node_id}
    records = [
        r
        for r in records
        if isinstance(r, dict)
        and r.get("object") == expected_object
        and r.get("source") == f"node:{node_id}/{local_system_id}"
        and r.get("channel_id") == channel_id
        and r.get("semantic") == "reported"
    ]
    if len(records) != 1:
        return refuse("exact_source_record_missing_or_ambiguous")
    record = records[0]
    envelope = record.get("value")
    if not isinstance(envelope, dict) or envelope.get("payload_schema") != "lite3.robot-state/v0.1":
        return refuse("unsupported_robot_state")
    result["evidence"] = {
        "source": record["source"],
        "channel_id": channel_id,
        "source_epoch": envelope.get("source_epoch"),
        "sequence": envelope.get("sequence"),
        "controller_received_at_ms": envelope.get("received_at_ms"),
        "controller_observed_at_ms": now,
    }
    if (
        not isinstance(envelope.get("source_epoch"), str)
        or not envelope["source_epoch"]
        or type(envelope.get("sequence")) is not int
        or envelope["sequence"] <= 0
    ):
        return refuse("source_epoch_or_sequence_missing")
    received, valid_for = envelope.get("received_at_ms"), envelope.get("valid_for_ms")
    if (
        record.get("stale") is not False
        or type(received) is not int
        or type(valid_for) is not int
        or valid_for <= 0
        or not 0 <= now - received < valid_for
    ):
        return refuse("controller_record_stale_or_clock_invalid")
    snapshot = envelope.get("value")
    mapping = snapshot.get("mapping") if isinstance(snapshot, dict) else None
    if not isinstance(mapping, dict):
        return refuse("mapping_observation_missing")
    age = mapping.get("age_ms")
    if (
        mapping.get("availability") != "current"
        or not isinstance(age, int | float)
        or isinstance(age, bool)
        or not _number(age)
        or age < 0
    ):
        return refuse("mapping_response_age_unavailable")
    retained_age = age + (now - received)
    result["evidence"]["mapping_response_age_at_publisher_read_ms"] = age
    result["evidence"]["mapping_age_plus_controller_retention_ms"] = retained_age
    if retained_age > MAX_MAPPING_AGE_MS:
        return refuse("mapping_response_stale")
    value = mapping.get("value")
    if not isinstance(value, dict) or value.get("has_map") is not True:
        return refuse("active_map_unavailable")
    if value.get("map_id") != expected_map_id:
        return refuse("map_mismatch")
    if value.get("mode") != "localization":
        return refuse("localization_mode_not_reported")
    pose = value.get("pose")
    if not isinstance(pose, dict) or not all(_number(pose.get(k)) for k in ["x", "y", "theta"]):
        return refuse("finite_map_pose_missing")
    result.update(
        status="reported",
        reason="bounded_age_mapping_response_available",
        position={
            "map_id": value["map_id"],
            "x_m": pose["x"],
            "y_m": pose["y"],
            "yaw_rad": pose["theta"],
            "coordinate_basis": "Robonix Mapping API map pose; frame not separately verified",
            "map_revision_id": None,
            "pose_measurement_observed_at": None,
        },
        limitations=[
            "source_pose_timestamp_and_localization_quality_not_provided",
            "immutable_map_revision_and_shared_frame_not_verified",
            "publisher_to_controller_transport_age_not_measured",
            "node_record_is_not_admitted_mi_world_grounding",
        ],
    )
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so client-certificate reads cannot silently change their target."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Reject every redirect, including a same-host endpoint change."""
        return None


def _reject_constant(value: str) -> Any:
    """Refuse non-JSON floating constants rather than accepting NaN or infinity."""
    raise ValueError("invalid JSON constant: " + value)


def read_controller(origin: str, path: str, context: ssl.SSLContext) -> dict[str, Any]:
    """GET one bounded authenticated Controller response; never retry, redirect or POST."""
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Controller origin must be an explicit HTTPS origin")
    if not path.startswith("/v1/") or path.startswith("//"):
        raise ValueError("only fixed Controller v1 read routes are allowed")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )
    request = urllib.request.Request(origin.rstrip("/") + path, method="GET")
    with opener.open(request, timeout=10) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Controller response exceeds 2 MiB")
    value = json.loads(raw, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Controller response must be an object")
    return value


def main() -> None:
    """Read only RG State and inventory, retaining exact inputs with an inspection result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-origin", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--local-system-id", default="robot-observer")
    parser.add_argument("--channel-id", default="robot-state")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=str(args.ca))
    context.load_cert_chain(str(args.cert), str(args.key))
    query = urllib.parse.urlencode(
        {
            "object_class": "node",
            "object_type": "robot",
        }
    )
    inventory = read_controller(args.controller_origin, "/v1/inventory", context)
    state = read_controller(args.controller_origin, "/v1/state/records?" + query, context)
    result = inspect_position(
        state,
        inventory,
        node_id=args.node_id,
        expected_map_id=args.map_id,
        local_system_id=args.local_system_id,
        channel_id=args.channel_id,
    )
    bundle = {
        "recorded_at_ns": time.time_ns(),
        "controller_origin": args.controller_origin,
        "inventory_response": inventory,
        "state_response": state,
        "inspection": result,
    }
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(bundle, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(0 if result["status"] == "reported" else 2)


if __name__ == "__main__":
    main()
