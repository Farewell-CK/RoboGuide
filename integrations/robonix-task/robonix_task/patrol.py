"""Build and inspect a fixed two-dog navigation draft without network or execution authority.

Inputs retain independent per-Node targets, the current registry/inventory, chassis diagnostics,
and an explicitly registered onsite verifier. Missing Scene/map provenance stays a blocker.
This preparation profile contains two navigation Tasks only; Robonix photo/artifact/summary
support is reported separately and is never invented to make a patrol look complete.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

ARRIVAL_CONTRACT = {"namespace": "observation.patrol", "name": "verify-arrival", "version": "v1"}
TASK_CONTRACT = {"namespace": "task", "name": "delegate", "version": "v1"}
MAX_ARRIVAL_AGE_MS = 5000
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class PatrolValidationError(ValueError):
    """Reject a changed draft or an attempted submission without complete current authority."""


@dataclass(frozen=True, slots=True)
class PatrolAssignment:
    """Freeze the explicitly requested first-round division without choosing a physical route."""

    node_id: str
    entity_id: str
    actor_id: str
    context_role: str
    task_id: str
    role_id: str
    target_name: str


ASSIGNMENTS = (
    PatrolAssignment(
        "lite3-dog1",
        "entity-lite3-dog1",
        "dog1-inspector",
        "dog1",
        "dog1-navigate",
        "dog1-navigator",
        "茶水间门口",
    ),
    PatrolAssignment(
        "lite3-dog2",
        "entity-lite3-dog2",
        "dog2-inspector",
        "dog2",
        "dog2-navigate",
        "dog2-navigator",
        "王老师办公室门口",
    ),
)


def _copy(value: Any) -> Any:
    """Detach finite JSON so a caller cannot mutate retained input through the returned draft."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _one(values: Any, key: str, identity: str) -> dict[str, Any] | None:
    """Select one exact item; duplicates and missing objects remain unresolved."""
    if not isinstance(values, list):
        return None
    selected = [v for v in values if isinstance(v, dict) and v.get(key) == identity]
    return selected[0] if len(selected) == 1 else None


def _mapping(value: Any) -> dict[str, Any]:
    """Treat absent or malformed observational documents as missing evidence."""
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> bool:
    """Refuse booleans and unbounded integers without overflowing preflight arithmetic."""
    try:
        return (
            not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)
        )
    except OverflowError:
        return False


def _target(snapshot: dict[str, Any], assignment: PatrolAssignment) -> dict[str, Any] | None:
    """Read only this Node's own target, never a same-label annotation borrowed from its peer."""
    return _one(snapshot.get("targets"), "node_id", assignment.node_id)


def _region_valid(region: Any, target: dict[str, Any], assignment: PatrolAssignment) -> bool:
    """Check original region identity and finite nondegenerate polygon without selecting a goal."""
    if not isinstance(region, dict) or (
        region.get("kind") != "region"
        or region.get("annotation_id") != target.get("region_id")
        or region.get("name") != assignment.target_name
        or region.get("stale") is not False
    ):
        return False
    points = region.get("points")
    if not isinstance(points, list) or not 3 <= len(points) <= 256:
        return False
    for point in points:
        if not isinstance(point, list) or len(point) != 2 or any(not _finite(n) for n in point):
            return False
    area = sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )
    return math.isfinite(area) and abs(area) > 1e-9


def arrival_predicate(snapshot: dict[str, Any], assignment: PatrolAssignment) -> str:
    """Bind the onsite arrival predicate to its exact source, keeping missing bindings explicit."""
    target = _target(snapshot, assignment) or {}
    reference = {
        "entity_id": assignment.entity_id,
        "node_id": assignment.node_id,
        "target_name": assignment.target_name,
        "map_id": target.get("map_id"),
        "region_id": target.get("region_id"),
        "region_source_digest": target.get("region_source_digest"),
    }
    raw = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    pending = any(reference[k] is None for k in ("map_id", "region_id", "region_source_digest"))
    return (
        "onsite-arrival/v0.1 "
        + ("pending-map-binding " if pending else "")
        + assignment.entity_id
        + " arrived-and-stopped at "
        + assignment.target_name
        + " target-reference-sha256:"
        + digest
    )


def assess_patrol_preflight(
    snapshot: dict[str, Any], *, observed_at_ms: int | None = None
) -> dict[str, Any]:
    """List preparation gaps without promoting diagnostics, map labels or flags into permission.

    ``physical_registry`` is the current Controller registry document (or its GET wrapper).
    ``inventory`` is the original Controller inventory. ``targets`` contains one independently
    sourced object per Node, including ``region_source`` with Node/map/digest attribution,
    ``region`` with the original annotation, and an explicit ``map_binding_verified`` fact.
    ``chassis`` holds existing posture diagnostic results keyed by Node. ``arrival_verifier``
    and ``photo_support`` are deployment observations, not model-generated declarations.
    """
    navigation: list[dict[str, str]] = []
    patrol: list[dict[str, str]] = []

    def block(code: str, node_id: str = "", *, whole_patrol: bool = False) -> None:
        """Keep stable attributed blockers instead of manufacturing coordinates or readiness."""
        (patrol if whole_patrol else navigation).append({"code": code, "node_id": node_id})

    registry = snapshot.get("physical_registry")
    if isinstance(registry, dict) and "registry" in registry:
        registry = registry["registry"]
    if not isinstance(registry, dict) or (
        registry.get("schema") != "roboguide.physical-entity-registry/v0.1"
        or not isinstance(registry.get("registry_id"), str)
        or not registry["registry_id"]
        or type(registry.get("revision")) is not int
        or registry["revision"] <= 0
        or registry.get("routing_profile") != "one-routable-entity-per-node"
    ):
        block("physical_registry_unavailable")
        registry = {}
    inventory = snapshot.get("inventory", {})
    nodes = inventory.get("nodes") if isinstance(inventory, dict) else None
    inventory_time = _mapping(inventory).get("observed_at_ms")
    now = inventory_time if observed_at_ms is None else observed_at_ms
    if (
        type(now) is not int
        or type(inventory_time) is not int
        or not 0 <= now - inventory_time < 5000
    ):
        block("current_controller_inventory_time_unavailable")
    for assignment in ASSIGNMENTS:
        node_id = assignment.node_id
        entity = _one(registry.get("entities"), "entity_id", assignment.entity_id)
        if entity is None or entity.get("node_id") != node_id:
            block("physical_entity_route_unverified", node_id)
        node = _one(nodes, "node_id", node_id)
        if node is None or node.get("liveness") != "Reachable":
            block("node_not_reachable", node_id)
        if (
            node is None
            or not isinstance(node.get("contracts"), list)
            or "task.delegate@v1" not in node["contracts"]
        ):
            block("robonix_task_delegate_unavailable", node_id)
        chassis = _mapping(_mapping(snapshot.get("chassis")).get(node_id))
        remaining = _mapping(chassis.get("evidence")).get("remaining_valid_for_ms")
        chassis_time = _mapping(chassis.get("evidence")).get("controller_observed_at_ms")
        if not isinstance(chassis, dict) or (
            chassis.get("status") != "reported"
            or chassis.get("posture") not in {"standing", "down"}
            or type(remaining) is not int
            or remaining <= 0
            or type(chassis_time) is not int
            or type(now) is not int
            or not 0 <= now - chassis_time < remaining
        ):
            block("fresh_reported_chassis_unavailable", node_id)
        target = _target(snapshot, assignment)
        if target is None:
            block("independent_target_source_missing", node_id)
        else:
            source = target.get("region_source")
            if (
                target.get("entity_id") != assignment.entity_id
                or target.get("target_name") != assignment.target_name
            ):
                block("fixed_target_assignment_mismatch", node_id)
            if not isinstance(source, dict) or source.get("node_id") != node_id:
                block("region_source_node_mismatch", node_id)
            if not isinstance(target.get("map_id"), str) or not target["map_id"]:
                block("target_map_missing", node_id)
            elif not isinstance(source, dict) or source.get("map_id") != target["map_id"]:
                block("region_source_map_mismatch", node_id)
            if target.get("map_binding_verified") is not True:
                block("independent_map_binding_unverified", node_id)
            digest = target.get("region_source_digest")
            if (
                not isinstance(digest, str)
                or _DIGEST.fullmatch(digest) is None
                or not isinstance(source, dict)
                or source.get("document_digest") != digest
            ):
                block("region_source_digest_unverified", node_id)
            if not _region_valid(target.get("region"), target, assignment):
                block("original_target_region_unverified", node_id)
        photo = _mapping(_mapping(snapshot.get("photo_support")).get(node_id))
        if not isinstance(photo, dict) or photo.get("robonix_capture") is not True:
            block("robonix_photo_operation_unverified", node_id, whole_patrol=True)
        if not isinstance(photo, dict) or photo.get("artifact_receipt") is not True:
            block("robonix_photo_artifact_receipt_unverified", node_id, whole_patrol=True)
    verifier = snapshot.get("arrival_verifier", {})
    if not isinstance(verifier, dict) or (
        verifier.get("registered") is not True
        or not isinstance(verifier.get("source_id"), str)
        or not verifier["source_id"]
        or verifier.get("contract") != ARRIVAL_CONTRACT
        or verifier.get("max_evidence_age_ms") != MAX_ARRIVAL_AGE_MS
        or verifier.get("mode") != "independent-onsite-attestation"
    ):
        block("independent_arrival_verifier_unavailable")
    elif not isinstance(verifier.get("predicates"), list) or any(
        arrival_predicate(snapshot, a) not in verifier["predicates"] for a in ASSIGNMENTS
    ):
        block("arrival_predicate_not_authorized_by_source")
    if snapshot.get("summary_artifact_supported") is not True:
        block("patrol_summary_artifact_unavailable", whole_patrol=True)
    return {
        "schema_version": "roboguide.patrol-preflight/v0.1",
        "status": "Blocked" if navigation or patrol else "ReadyForOnsiteAuthorization",
        "navigation_ready": not navigation,
        "navigation_blockers": navigation,
        "patrol_blockers": patrol,
        "motion_authorized": False,
        "physical_effect_verified": False,
        "plan_scope": "navigation-only-preparation",
        "target_predicates": {a.task_id: arrival_predicate(snapshot, a) for a in ASSIGNMENTS},
        "limitations": [
            "freshness_is_a_readonly_snapshot_not_dispatch_authority",
            "map_label_equality_does_not_establish_shared_coordinates",
            "arrival_requires_independent_current_attempt_bound_onsite_evidence",
            "photo_and_summary_tasks_not_fabricated_when_robonix_receipts_are_missing",
        ],
    }


def build_patrol_plan(snapshot: dict[str, Any], mission_id: str) -> dict[str, Any]:
    """Build an unapplied navigation-only template; pending evidence never creates permission."""
    if not isinstance(mission_id, str) or _ID.fullmatch(mission_id) is None:
        raise PatrolValidationError("bounded_mission_identity_required")
    tasks = []
    for assignment in ASSIGNMENTS:
        target = _target(snapshot, assignment) or {}
        predicate = arrival_predicate(snapshot, assignment)
        location = (
            f"Scene区域 {target['region_id']}，本机地图 {target['map_id']}"
            if target.get("region_id") and target.get("map_id")
            else "目标地图和Scene来源尚未独立核验，不得开始执行"
        )
        tasks.append(
            {
                "id": assignment.task_id,
                "description": (
                    f"到达{assignment.target_name}并停稳，等待独立现场证据；"
                    "本机完成不等于目标已满足。"
                ),
                "depends_on": [],
                "context_id": "partitioned-patrol",
                "coupling_mode": "independent",
                "roles": [
                    {
                        "id": assignment.role_id,
                        "context_role": assignment.context_role,
                        "requirements": {
                            "capabilities": [{"contract": dict(TASK_CONTRACT), "constraints": []}],
                            "resources": [{"kind": "space", "units": 1}],
                            "memories": [],
                        },
                        "execution_intent": {
                            "operation": dict(TASK_CONTRACT),
                            "parameters": {"profile": "local-task-v1"},
                            "objective": (
                                f"由本机Robonix导航至{assignment.target_name}（{location}），到达后保持停止。"
                                "仅使用已确认的本机地图与原目标定义；来源不明、定位不可用或本机拒绝时如实失败，"
                                "不得切换地图、调整目标、自动重试动作或下发其他动作。"
                                "不执行拍照或汇总；后续动作须为独立任务。RG负责选择、批准和资源调度。"
                                "不得绕过本机Robonix控制底盘，不要求本机证明集群身份或匹配RG别名。"
                            ),
                        },
                        "resource_scope": "task",
                    }
                ],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": 300000,
                },
                "satisfaction": {
                    "basis": "verifier-evidence",
                    "expected_effect": predicate,
                    "verifier": {
                        "contract": dict(ARRIVAL_CONTRACT),
                        "predicate": predicate,
                        "max_evidence_age_ms": MAX_ARRIVAL_AGE_MS,
                    },
                },
            }
        )
    return {
        "schema_version": "roboguide.mission-plan/v0.10",
        "mission": {
            "id": mission_id,
            "objective": (
                "双狗固定分区导航准备：茶水间门口、王老师办公室门口；"
                "到达需独立现场确认。本模板不提交运动。"
            ),
            "actors": [{"id": a.actor_id, "physical_entity": a.entity_id} for a in ASSIGNMENTS],
        },
        "contexts": [
            {
                "id": "partitioned-patrol",
                "coupling_mode": "independent",
                "relations": [],
                "roles": [{"id": a.context_role, "actor": a.actor_id} for a in ASSIGNMENTS],
                "executor_constraints": [
                    {
                        "kind": "distinct-physical-entities",
                        "context_roles": [a.context_role for a in ASSIGNMENTS],
                    }
                ],
            }
        ],
        "tasks": tasks,
    }


def validate_patrol_plan(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    motion_authorized: bool = False,
    for_submission: bool = False,
    observed_at_ms: int | None = None,
) -> dict[str, Any]:
    """Check the fixed draft; submission also requires current gates and onsite consent."""
    try:
        expected = build_patrol_plan(snapshot, plan["mission"]["id"])
        if plan.get("schema_version") != expected["schema_version"] or set(plan) != set(expected):
            raise PatrolValidationError("patrol_plan_schema_or_fields_changed")
        if plan["mission"]["actors"] != expected["mission"]["actors"]:
            raise PatrolValidationError("two_exact_distinct_physical_entities_required")
        if plan["contexts"] != expected["contexts"]:
            raise PatrolValidationError("independent_distinct_entity_context_required")
        if not isinstance(plan.get("tasks"), list) or len(plan["tasks"]) != 2:
            raise PatrolValidationError("two_navigation_tasks_only")
        for original in expected["tasks"]:
            current = _one(plan["tasks"], "id", original["id"])
            if current is None:
                raise PatrolValidationError("original_navigation_task_missing")
            for key in ("context_id", "depends_on", "coupling_mode", "satisfaction"):
                if current.get(key) != original[key]:
                    raise PatrolValidationError("independent_onsite_arrival_gate_required")
            if (
                current.get("roles") != original["roles"]
                or current.get("timing") != original["timing"]
            ):
                raise PatrolValidationError("fixed_navigation_intent_or_requirements_changed")
        preflight = assess_patrol_preflight(snapshot, observed_at_ms=observed_at_ms)
    except (KeyError, TypeError, AttributeError) as error:
        raise PatrolValidationError("malformed_fixed_patrol_plan_or_preflight") from error
    if for_submission:
        if motion_authorized is not True:
            raise PatrolValidationError("onsite_motion_authorization_required")
        if type(observed_at_ms) is not int:
            raise PatrolValidationError("current_controller_observation_required")
        if not preflight["navigation_ready"]:
            raise PatrolValidationError("current_navigation_preflight_blocked")
    return {
        "schema_version": "roboguide.patrol-plan-validation/v0.1",
        "plan_valid": True,
        "submitted": False,
        "motion_authorized": motion_authorized is True,
        "read_only_validation": not for_submission,
        "navigation_ready": preflight["navigation_ready"],
        "navigation_blockers": _copy(preflight["navigation_blockers"]),
        "patrol_blockers": _copy(preflight["patrol_blockers"]),
        "plan_scope": "navigation-only-preparation",
    }
