"""Shared-prefix A/B/C ablation over production adapters and unchanged review routing."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.rejected_draft import RejectedPlanError
from mission.responses import (
    JsonTransport,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
    UrllibJsonTransport,
)
from mission.review import MissionPlanReview, MissionReviewRoute, route_mission_review

from roboguide_eval.mi_ablation.evidence import RecordingTransport, digest, write_new
from roboguide_eval.mi_ablation.scoring import check_plan, object_value, objects


def _generate(
    first: Callable[[], MissionPlan],
    regenerate: Callable[[RejectedPlanError], MissionPlan],
    directory: Path,
    budget: int,
) -> MissionPlan:
    """Regenerate only preserved structural failures within the same fixed cycle budget."""
    rejected: RejectedPlanError | None = None
    for attempt in range(budget + 1):
        try:
            plan = first() if rejected is None else regenerate(rejected)
            write_new(
                directory / f"attempt-{attempt}.json", {"valid": True, "plan": plan.to_json()}
            )
            return plan
        except RejectedPlanError as error:
            write_new(
                directory / f"attempt-{attempt}.json",
                {
                    "valid": False,
                    "provider_output": error.provider_output,
                    "normalized_output": error.normalized_output,
                    "validation_errors": list(error.validation_errors),
                },
            )
            rejected = error
    assert rejected is not None
    raise rejected


def _branch(
    status: str,
    plan: MissionPlan | None,
    calls: int,
    checks: list[JSONObject],
) -> JSONObject:
    """Capture a logical branch without confusing readiness with Controller admission."""
    return {
        "status": status,
        "candidate_digest": digest(plan.to_json()) if plan else None,
        "plan": plan.to_json() if plan else None,
        "logical_provider_calls": calls,
        "scoring": check_plan(plan, checks) if plan else None,
        "controller_submitted": False,
        "physical_success": None,
    }


def run_trial(
    case: JSONObject,
    replicate: int,
    directory: Path,
    settings: MissionSettings,
    catalog: CanonicalCapabilityCatalog,
    environment: Mapping[str, str],
    delegate: JsonTransport | None = None,
) -> JSONObject:
    """Consume a trial once, reuse the exact first plan/review, and preserve every failure."""
    if not 0 <= settings.prevalidation_recovery_attempts <= 2:
        raise ValueError("ablation supports at most two structural regeneration attempts")
    if not 0 <= settings.max_repair_attempts <= 2:
        raise ValueError("ablation supports at most two review/repair rounds")
    if settings.llm.model != settings.llm.review_model:
        raise ValueError("paired ablation requires one common model")
    intent_input = object_value(case["grounded_intent"])
    intent = GroundedIntent(
        cast(str, intent_input["objective"]),
        tuple(cast(list[str], intent_input["constraints"])),
        tuple(cast(list[str], intent_input["assumptions"])),
    )
    mission_id = f"mi-ablation-{case['id']}-r{replicate}"
    context = GroundingContextSnapshot.create(
        request_id=mission_id,
        dialogue_digest=digest(intent.to_json()),
        captured_at_ms=0,
    )
    checks = objects(case["checks"])
    # Rubrics are confined to evidence/scoring; none of the ports receive them.
    public_input: JSONObject = {
        "mission_id": mission_id,
        "grounded_intent": intent.to_json(),
        "grounding_context": context.to_json(),
        "capability_catalog": catalog.to_json(),
        "satisfaction_policy": (
            settings.satisfaction_policy.to_json() if settings.satisfaction_policy else None
        ),
    }
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory / "claim.json", {"case_id": case["id"], "replicate": replicate})
    write_new(directory / "input.json", public_input)
    write_new(directory / "private-rubric.json", case)
    transport = RecordingTransport(
        delegate or UrllibJsonTransport(),
        directory / "calls",
        settings.llm.model,
        (environment.get(settings.provider.api_key_env, ""),),
    )
    planner = ResponsesMissionPlanner(settings, environment, transport)
    reviewer = ResponsesMissionReviewer(settings, environment, transport)
    repairer = ResponsesMissionRepairer(settings, environment, transport)
    branches: JSONObject = {}
    result: JSONObject = {
        "schema_version": "roboguide.mi-ablation-trial/v0.1",
        "case_id": case["id"],
        "family": case["family"],
        "replicate": replicate,
        "input_digest": digest(public_input),
        "grounding_context_digest": context.context_digest,
        "case_digest": digest(case),
        "branches": branches,
        "reviews": [],
        "error": None,
        "semantic_correctness": "pending_blind_human_review",
    }
    plan: MissionPlan | None = None
    try:
        transport.stage = "planner"
        plan = _generate(
            lambda: planner.plan(mission_id, intent, catalog, context),
            lambda error: planner.regenerate(
                mission_id,
                intent,
                catalog,
                context,
                error.provider_output,
                list(error.validation_errors),
            ),
            directory / "planner",
            settings.prevalidation_recovery_attempts,
        )
        branches["A"] = _branch("candidate_ready", plan, len(transport.calls), checks)
        for round_index in range(settings.max_repair_attempts + 1):
            transport.stage = f"reviewer-{round_index}"
            review = reviewer.review(intent, plan, catalog, context)
            route = route_mission_review(review)
            witness: JSONObject = {
                "round": round_index,
                "plan_digest": digest(plan.to_json()),
                "grounding_context_digest": context.context_digest,
                "review": review.to_json(),
                "route": route.value,
                "call_index": len(transport.calls),
            }
            write_new(directory / f"review-{round_index}.json", witness)
            cast(list[JSONValue], result["reviews"]).append(witness)
            if round_index == 0:
                branches["B"] = _branch(route.value, plan, len(transport.calls), checks)
            if route is not MissionReviewRoute.REPAIR:
                branches["C"] = _branch(route.value, plan, len(transport.calls), checks)
                break
            if round_index == settings.max_repair_attempts:
                branches["C"] = _branch(
                    "repair_budget_exhausted", plan, len(transport.calls), checks
                )
                break
            transport.stage = f"repairer-{round_index + 1}"
            plan = _repair(
                repairer,
                mission_id,
                intent,
                plan,
                review,
                catalog,
                context,
                directory / f"repair-{round_index + 1}",
                settings.prevalidation_recovery_attempts,
            )
    except Exception as error:
        message = str(error)
        for secret in transport.secrets:
            message = message.replace(secret, "[REDACTED]")
        result["error"] = {
            "stage": transport.stage,
            "type": type(error).__name__,
            "message": message,
            "status_code": getattr(error, "status_code", None),
        }
        for name in ("A", "B", "C"):
            if name not in branches:
                branches[name] = _branch(
                    "structure_rejected" if isinstance(error, RejectedPlanError) else "unavailable",
                    plan,
                    len(transport.calls),
                    checks,
                )
    result["actual_provider_calls"] = len(transport.calls)
    result["calls"] = transport.calls
    for raw_branch in branches.values():
        branch = object_value(raw_branch)
        prefix = transport.calls[: cast(int, branch["logical_provider_calls"])]
        usage = [call.get("usage") for call in prefix]
        branch["logical_provider_elapsed_seconds"] = sum(
            cast(float, call["elapsed_seconds"]) for call in prefix
        )
        branch["logical_total_tokens"] = (
            sum(cast(int, object_value(item)["total_tokens"]) for item in usage)
            if all(
                isinstance(item, dict) and type(item.get("total_tokens")) is int for item in usage
            )
            else None
        )
    # The first review is physically made once; logical B/C costs include their shared prefix.
    write_new(directory / "result.json", result)
    return result


def _repair(
    repairer: ResponsesMissionRepairer,
    mission_id: str,
    intent: GroundedIntent,
    plan: MissionPlan,
    review: MissionPlanReview,
    catalog: CanonicalCapabilityCatalog,
    context: GroundingContextSnapshot,
    directory: Path,
    budget: int,
) -> MissionPlan:
    """Bind each repair regeneration to the same rejected plan, review and snapshot."""
    return _generate(
        lambda: repairer.repair(mission_id, intent, plan, review, catalog, context),
        lambda error: repairer.regenerate(
            mission_id,
            intent,
            plan,
            review,
            catalog,
            context,
            error.provider_output,
            list(error.validation_errors),
        ),
        directory,
        budget,
    )
