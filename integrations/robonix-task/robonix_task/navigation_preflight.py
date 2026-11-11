"""Join original reported maps and pose freshness for fixed-patrol preparation only.

The caller authenticates input projections. This pure profile is not Core admission,
localization verification or motion authorization; execution requires a fresh recheck.
"""

from __future__ import annotations

import math
from typing import Any, cast

from robonix_task.patrol import (
    ARRIVAL_CONTRACT,
    ASSIGNMENTS,
    TASK_CONTRACT,
    arrival_predicate,
    assess_patrol_preflight,
)
from robonix_task.patrol_grounding import PatrolGroundingError, build_patrol_snapshot
from robonix_task.position import MAX_MAPPING_AGE_MS, inspect_position
from robonix_task.posture import inspect_posture
from robonix_task.verifier_catalog import VerifierCatalogError, build_arrival_catalog

_CATALOG = {
    "schema_version": "roboguide.capability-catalog/v0.3",
    "capabilities": [
        {"contract": c, "description": "Fixed preparation contract", "attributes": []}
        for c in (TASK_CONTRACT, ARRIVAL_CONTRACT)
    ],
    "operations": [
        {
            "operation": TASK_CONTRACT,
            "description": "Existing local task delegation",
            "parameters": [],
            "required_capabilities": [TASK_CONTRACT],
        }
    ],
}


def assess_navigation_preflight(
    world_state: dict[str, Any],
    node_state: dict[str, Any],
    inventory: dict[str, Any],
    physical_registry: dict[str, Any],
    verifier_view: dict[str, Any],
    source_config: dict[str, Any],
    *,
    local_read_elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Require each own Scene map, fresh reported pose and exact registered arrival source.

    Position budgets charge later observations in the same Controller clock plus the
    caller's full local acquisition/validation delay. They never compare host clocks
    or turn Mapping response receipt time into a pose measurement timestamp. Missing
    photo/summary support stays visible but does not block this navigation-only profile.
    """
    result: dict[str, Any] = {
        "schema_version": "roboguide.reported-navigation-preflight/v0.1",
        "status": "Blocked",
        "navigation_ready": False,
        "navigation_blockers": [],
        "positions": {},
        "patrol_snapshot": None,
        "patrol_blockers": [],
        "reported_map_consistency_verified": False,
        "localization_verified": False,
        "shared_frame_verified": False,
        "motion_authorized": False,
        "physical_effect_verified": False,
        "scope": "fixed-patrol-reported-navigation-only",
        "limitations": [
            "authenticate original inputs and recheck immediately before approval",
            "matching reported map labels do not establish a shared physical frame",
            "native pose measurement time and localization quality remain unknown",
            "this snapshot grants no motion, arrival, device-stop or recovery authority",
        ],
    }

    def block(code: str, node_id: str = "") -> None:
        """Retain an attributed preparation refusal without inventing physical evidence."""
        result["navigation_blockers"].append({"code": code, "node_id": node_id})

    try:
        if type(local_read_elapsed_ms) is not int or not 0 <= local_read_elapsed_ms <= 3600000:
            raise PatrolGroundingError("bounded_local_read_elapsed_ms_required")
        posture = {
            a.node_id: inspect_posture(node_state, inventory, node_id=a.node_id)
            for a in ASSIGNMENTS
        }
        snapshot = build_patrol_snapshot(
            world_state,
            inventory,
            physical_registry,
            posture,
            local_read_elapsed_ms=local_read_elapsed_ms,
        )
        result["patrol_snapshot"] = snapshot
        clocks = [v.get("observed_at_ms") for v in (world_state, node_state, inventory)]
        if any(type(v) is not int or v < 0 for v in clocks):
            raise PatrolGroundingError("original_controller_observation_clock_required")
        now = max(cast(list[int], clocks))
        maps_consistent = True
        for assignment in ASSIGNMENTS:
            original_posture = posture[assignment.node_id]
            original_evidence = original_posture.get("evidence", {})
            posture_remaining = original_evidence.get("remaining_valid_for_ms")
            posture_observed = original_evidence.get("controller_observed_at_ms")
            if (
                original_posture.get("status") != "reported"
                or type(posture_remaining) is not int
                or type(posture_observed) is not int
                or not 0 <= posture_observed <= now
                or posture_remaining - (now - posture_observed) <= local_read_elapsed_ms
            ):
                block("fresh_reported_chassis_budget_unavailable", assignment.node_id)
            selected = [t for t in snapshot["targets"] if t["node_id"] == assignment.node_id]
            expected = selected[0]["map_id"] if len(selected) == 1 else ""
            position = inspect_position(
                node_state, inventory, node_id=assignment.node_id, expected_map_id=expected
            )
            if position["status"] == "reported":
                evidence = position["evidence"]
                records = [
                    r
                    for r in node_state["records"]
                    if r.get("object")
                    == {"class": "node", "object_type": "robot", "object_id": assignment.node_id}
                    and r.get("source") == evidence["source"]
                    and r.get("channel_id") == evidence["channel_id"]
                ]
                assert len(records) == 1
                envelope = records[0]["value"]
                retained = node_state["observed_at_ms"] - envelope["received_at_ms"]
                remaining = math.floor(
                    min(
                        envelope["valid_for_ms"] - retained,
                        MAX_MAPPING_AGE_MS - evidence["mapping_age_plus_controller_retention_ms"],
                    )
                    - (now - node_state["observed_at_ms"])
                    - local_read_elapsed_ms
                )
                evidence["remaining_valid_for_ms"] = max(0, remaining)
                if remaining <= 0:
                    position.update(
                        status="unavailable",
                        position=None,
                        reason="mapping_budget_expired_during_preparation",
                    )
            result["positions"][assignment.node_id] = position
            if position["status"] != "reported":
                block(
                    "fresh_matching_reported_pose_unavailable:" + position["reason"],
                    assignment.node_id,
                )
                maps_consistent = False
        result["reported_map_consistency_verified"] = maps_consistent
        try:
            _, provenance = build_arrival_catalog(_CATALOG, verifier_view, source_config)
            targets = source_config["targets"]
            if len(targets) != len(ASSIGNMENTS):
                raise VerifierCatalogError("fixed_profile_exact_target_set_required")
            for assignment in ASSIGNMENTS:
                actual = [t for t in snapshot["targets"] if t["node_id"] == assignment.node_id]
                configured = [t for t in targets if t["node_id"] == assignment.node_id]
                if (
                    len(actual) != 1
                    or len(configured) != 1
                    or any(
                        configured[0][key] != actual[0][key]
                        for key in [
                            "entity_id",
                            "node_id",
                            "target_name",
                            "map_id",
                            "region_id",
                            "region_source_digest",
                        ]
                    )
                    or configured[0]["predicate"] != arrival_predicate(snapshot, assignment)
                ):
                    raise VerifierCatalogError("current_target_differs_from_registered_source")
            snapshot["arrival_verifier"] = {
                "registered": True,
                "published": True,
                "source_id": source_config["source_id"],
                "contract": dict(ARRIVAL_CONTRACT),
                "max_evidence_age_ms": 5000,
                "mode": "independent-onsite-attestation",
                "predicates": [t["predicate"] for t in targets],
                "provenance": provenance,
            }
        except (VerifierCatalogError, KeyError, TypeError, ValueError):
            block("registered_arrival_source_or_current_target_mismatch")
        assessment = assess_patrol_preflight(snapshot, observed_at_ms=now)
        result["navigation_blockers"].extend(assessment["navigation_blockers"])
        result["patrol_blockers"] = assessment["patrol_blockers"]
        result["navigation_ready"] = not result["navigation_blockers"]
        result["status"] = (
            "ReadyForOnsiteAuthorization" if result["navigation_ready"] else "Blocked"
        )
    except (PatrolGroundingError, KeyError, TypeError, ValueError, OverflowError, AssertionError):
        block("original_navigation_projection_or_read_budget_invalid")
    return result
