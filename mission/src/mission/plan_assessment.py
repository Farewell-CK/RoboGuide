"""Exact-plan, source-bound Controller contract feedback, separate from admission."""

from __future__ import annotations

import re
from typing import cast

from mission.deployment_assessment import plan_body_digest
from mission.execution_planning_evidence import (
    ExecutionPlanningEvidence,
    validate_execution_topology,
)
from mission.models import JSONObject, MissionPlan
from mission.plan_validation import MAX_PLAN_ISSUES, PlanValidationError

SCHEMA = "roboguide.plan-validation/v0.2"
LEGACY_SCHEMA = "roboguide.plan-validation/v0.1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CODES = {
    "verifier.source_unavailable",
    "verifier.source_unsupported",
    "verifier.blocks_dependent_work",
    "verifier.blocks_actor_reuse",
    "deployment.contract_incompatible",
    "deployment.coupling_mode_unsupported",
    "deployment.context_relations_unsupported",
    "deployment.dependencies_unsupported",
    "deployment.slot_limit_exceeded",
    "deployment.resource_requirement_invalid",
    "deployment.endpoint_capacity_exceeded",
    "deployment.concurrent_capacity_exceeded",
    "deployment.context_capacity_exceeded",
    "deployment.operation_unsupported",
    "deployment.intent_invalid",
    "deployment.actor_placement_unavailable",
    "deployment.concurrent_actor_reuse",
    "deployment.concurrent_placement_unavailable",
    "deployment.object_relocation_conflict",
    "deployment.topology_unsupported",
    "verifier.retained_capacity_exceeded",
    "verifier.retained_placement_unavailable",
}


def validate_assessment(
    value: JSONObject,
    plan: MissionPlan,
    evidence: ExecutionPlanningEvidence | None = None,
) -> None:
    """Reject detached or malformed replies before exposing concrete conflicts to recovery.

    ValueError means a configuration/transport/source problem, never a draft
    defect. PlanValidationError means the exact checked draft has known issues.
    """
    schema = value.get("schema_version")
    fields = {
        "schema_version",
        "scope",
        "mission_id",
        "plan_body_sha256",
        "decision",
        "verifier_source_digest",
        "deployment_digest",
        "execution_lifetime",
        "world_identity",
        "issues",
        "issues_truncated",
    } | ({"execution_topology"} if schema == SCHEMA else set())
    if (
        set(value) != fields
        or not isinstance(schema, str)
        or schema not in {SCHEMA, LEGACY_SCHEMA}
        or value["scope"] != "published-contracts"
        or value["mission_id"] != plan.mission.mission_id
        or value["plan_body_sha256"] != plan_body_digest(plan)
        or type(value["issues_truncated"]) is not bool
    ):
        raise ValueError("Controller plan assessment is invalid or detached")
    topology = value.get("execution_topology")
    if topology is not None:
        validate_execution_topology(topology)
    for key in ("verifier_source_digest", "deployment_digest"):
        item = value[key]
        if item is not None and (not isinstance(item, str) or not _DIGEST.fullmatch(item)):
            raise ValueError("Controller plan assessment source digest is invalid")
    identity = value["world_identity"]
    if identity is not None and (
        not isinstance(identity, dict)
        or set(identity)
        != {
            "run_id",
            "episode_id",
            "scene_id",
            "dataset_revision",
            "dataset_sha256",
            "semantic_evidence_digest",
        }
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 2048
            for item in identity.values()
        )
    ):
        raise ValueError("Controller plan assessment world identity is invalid")
    if evidence is not None:
        document = evidence.to_json()
        source = cast(JSONObject, document["verifier_source"])
        if (
            value["verifier_source_digest"] != source["digest"]
            or value["execution_lifetime"] != document["execution_lifetime"]
            or value["deployment_digest"] is None
            or value["world_identity"] != document["identity"]
            or ("execution_topology" in document and topology != document["execution_topology"])
        ):
            raise ValueError("Controller plan assessment differs from frozen deployment evidence")
    issues = value["issues"]
    if not isinstance(issues, list) or len(issues) > MAX_PLAN_ISSUES:
        raise ValueError("Controller plan assessment issues exceed their bound")
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or set(issue) != {"code", "path", "message"}
            or not isinstance(issue["code"], str)
            or issue["code"] not in _CODES
            or not isinstance(issue["path"], str)
            or not issue["path"].startswith("/")
            or len(issue["path"]) > 1024
            or not isinstance(issue["message"], str)
            or not issue["message"].strip()
            or len(issue["message"]) > 8192
        ):
            raise ValueError("Controller plan assessment issue is invalid")
    if value["decision"] != ("blocked" if issues else "not_blocked") or (
        value["issues_truncated"] and len(issues) != MAX_PLAN_ISSUES
    ):
        raise ValueError("Controller plan assessment decision contradicts its diagnostics")
    if issues:
        raise PlanValidationError(
            [
                {
                    **cast(JSONObject, issue),
                    "scope": "published-contracts",
                    "plan_body_sha256": value["plan_body_sha256"],
                    "verifier_source_digest": value["verifier_source_digest"],
                    "deployment_digest": value["deployment_digest"],
                    "issues_truncated": value["issues_truncated"],
                }
                for issue in issues
            ]
        )
