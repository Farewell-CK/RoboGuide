"""Bounded plan diagnostics; invalid drafts never become executable MissionPlans."""

from __future__ import annotations

from collections import Counter
from typing import cast

from mission.grounding_context import GroundingContextSnapshot, admitted_physical_entity_ids
from mission.models import JSONObject, MissionPlanError

MAX_PLAN_ISSUES = 32


class PlanValidationError(MissionPlanError):
    """Carry independently proven failures without rewriting the rejected candidate."""

    def __init__(self, issues: list[JSONObject]) -> None:
        """Bound feedback and retain the first explanation for legacy error consumers."""
        if not issues:
            raise ValueError("plan validation needs a concrete issue")
        self.validation_errors = tuple(dict(issue) for issue in issues[:MAX_PLAN_ISSUES])
        super().__init__(str(self.validation_errors[0]["message"]))


def reference_issues(
    value: JSONObject, grounding_context: GroundingContextSnapshot | None = None
) -> list[JSONObject]:
    """Inspect exact v0.8 logical references without admitting or repairing raw JSON.

    Malformed containers stay the canonical parser's responsibility. This pass
    collects only missing references whose declarations can be read safely.
    """
    if value.get("schema_version") != "roboguide.mission-plan/v0.8":
        return []
    mission, contexts, tasks = value.get("mission"), value.get("contexts"), value.get("tasks")
    if (
        not isinstance(mission, dict)
        or not isinstance(contexts, list)
        or not isinstance(tasks, list)
    ):
        return []
    actors = mission.get("actors")
    if not isinstance(actors, list):
        return []
    if any(not isinstance(actor, dict) or not isinstance(actor.get("id"), str) for actor in actors):
        return []
    actor_ids = {cast(str, actor["id"]) for actor in actors if isinstance(actor, dict)}
    issues: list[JSONObject] = []
    context_roles: dict[str, set[str]] = {}
    context_ids = [
        cast(str, context["id"])
        for context in contexts
        if isinstance(context, dict) and isinstance(context.get("id"), str)
    ]
    ambiguous_contexts = {
        identifier for identifier, count in Counter(context_ids).items() if count > 1
    }
    for index, context in enumerate(contexts):
        if not isinstance(context, dict) or not isinstance(context.get("id"), str):
            continue
        roles = context.get("roles")
        if not isinstance(roles, list):
            continue
        context_id = cast(str, context["id"])
        context_roles[context_id] = {
            cast(str, role["id"])
            for role in roles
            if isinstance(role, dict) and isinstance(role.get("id"), str)
        }
        for role_index, role in enumerate(roles):
            if (
                isinstance(role, dict)
                and isinstance(role.get("actor"), str)
                and role["actor"] not in actor_ids
            ):
                issues.append(
                    {
                        "code": "plan.undeclared_actor",
                        "path": f"/contexts/{index}/roles/{role_index}/actor",
                        "message": (
                            "ContextRole references undeclared actor; references must match "
                            "an exact mission.actors id."
                        ),
                    }
                )
                if len(issues) >= MAX_PLAN_ISSUES:
                    return issues
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("context_id"), str):
            continue
        context_id = cast(str, task["context_id"])
        if context_id in ambiguous_contexts:
            continue
        if context_id not in context_roles:
            issues.append(
                {
                    "code": "plan.undeclared_context",
                    "path": f"/tasks/{index}/context_id",
                    "message": "Task references an undeclared Context.",
                }
            )
            if len(issues) >= MAX_PLAN_ISSUES:
                return issues
            continue
        roles = task.get("roles")
        if not isinstance(roles, list):
            continue
        for role_index, role in enumerate(roles):
            if (
                isinstance(role, dict)
                and isinstance(role.get("context_role"), str)
                and role["context_role"] not in context_roles[context_id]
            ):
                issues.append(
                    {
                        "code": "plan.undeclared_context_role",
                        "path": f"/tasks/{index}/roles/{role_index}/context_role",
                        "message": "TaskRole references a ContextRole not declared in its Context.",
                    }
                )
                if len(issues) >= MAX_PLAN_ISSUES:
                    return issues
    if (
        grounding_context is not None
        and grounding_context.semantic_evidence is not None
        and not admitted_physical_entity_ids(grounding_context)
    ):
        for index, context in enumerate(contexts):
            if not isinstance(context, dict) or not isinstance(
                context.get("executor_constraints"), list
            ):
                continue
            for constraint_index, constraint in enumerate(
                cast(list[object], context["executor_constraints"])
            ):
                if (
                    isinstance(constraint, dict)
                    and constraint.get("kind") == "distinct-physical-entities"
                ):
                    issues.append(
                        {
                            "code": "plan.executor_constraint_unfounded",
                            "path": f"/contexts/{index}/executor_constraints/{constraint_index}",
                            "message": (
                                f"contexts[{index}].executor_constraints[{constraint_index}] "
                                "lacks authoritative physical-entity grounding; "
                                "the frozen context admits no physical-entity identities."
                            ),
                        }
                    )
                    if len(issues) >= MAX_PLAN_ISSUES:
                        return issues
    return issues[:MAX_PLAN_ISSUES]
