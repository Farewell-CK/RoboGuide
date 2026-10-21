"""Build advisory, source-bound effect-composition evidence for MI review."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import cast

from mission.models import CapabilityContractRef, JSONObject, MissionPlan, OperationRef
from mission.planning_profile import (
    DeploymentPlanningProfile,
    PlanningStateEffect,
)

SCHEMA = "roboguide.operation-effect-composition-review/v0.1"
MAX_ANALYSIS_SLOTS = 128
MAX_FINDINGS = 128


def _canonical_digest(value: object) -> str:
    """Hash a finite JSON-compatible value using stable canonical encoding."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _operation_id(operation: OperationRef | CapabilityContractRef) -> str:
    """Render the exact operation identity used by the frozen profile."""
    return f"{operation.namespace}.{operation.name}@{operation.version}"


def _task_order(plan: MissionPlan) -> dict[tuple[str, str], str]:
    """Classify Task order from explicit DAG edges, without inventing sequencing."""
    predecessors = {task.task_id: set(task.depends_on) for task in plan.tasks}

    def reaches(source: str, target: str) -> bool:
        """Return whether the frozen DAG proves source precedes target."""
        pending = list(predecessors[target])
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == source:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(predecessors[current])
        return False

    result: dict[tuple[str, str], str] = {}
    task_ids = [task.task_id for task in plan.tasks]
    for left_index, left in enumerate(task_ids):
        for right in task_ids[left_index + 1 :]:
            if reaches(left, right):
                result[(left, right)] = "ordered"
            elif reaches(right, left):
                result[(left, right)] = "reverse-ordered"
            else:
                result[(left, right)] = "unordered"
    return result


def _effect_key(
    effect: PlanningStateEffect,
    actor_id: str,
    parameters: dict[str, str | int | float | bool],
) -> tuple[str, str] | None:
    """Resolve a declared effect to an abstract logical subject, never a Node."""
    if effect.subject_scope == "actor":
        return effect.domain, f"actor:{actor_id}"
    parameter_name = effect.subject_parameter
    if parameter_name is None:
        return None
    value = parameters.get(parameter_name)
    if not isinstance(value, str) or not value:
        return None
    return effect.domain, f"entity:{value}"


def operation_effect_composition_review(
    plan: MissionPlan,
    profile: DeploymentPlanningProfile,
) -> JSONObject:
    """Compare Task effects while retaining logical and physical identity boundaries.

    Findings are advisory only. A matching state domain means at least one operation
    may change a domain read or changed by another operation; it does not prove that
    any required terminal predicate becomes false or establish physical bindings.
    """
    body: dict[str, object] = {
        "schema_version": SCHEMA,
        "authority": "derived-from-frozen-plan-and-deployment-planning-profile",
        "decision": "advisory",
        "plan_sha256": _canonical_digest(plan.to_json()),
        "planning_profile_sha256": profile.profile_digest,
        "analysis_status": "complete",
        "findings": [],
        "unknown_pairs": [],
        "truncated": False,
        "guidance": {
            "possible_overlap_is_not_a_proven_goal_conflict": True,
            "logical_actor_does_not_select_physical_entity": True,
            "unlisted_operation_effects_are_unknown": True,
            "official_terminal_verification_remains_authoritative": True,
        },
    }
    contexts = {
        context.context_id: {role.role_id: role.actor_id for role in context.roles}
        for context in plan.contexts
    }
    slots: list[tuple[str, str, str, str, dict[str, str | int | float | bool]]] = []
    for task in plan.tasks:
        context_roles = contexts[task.context_id]
        for role in task.roles:
            if role.context_role is None or role.context_role not in context_roles:
                continue
            slots.append(
                (
                    task.task_id,
                    role.role_id,
                    context_roles[role.context_role],
                    _operation_id(role.execution.operation),
                    dict(role.execution.parameters),
                )
            )
    if len(slots) > MAX_ANALYSIS_SLOTS:
        body["analysis_status"] = "incomplete-slot-bound-exceeded"
        body["truncated"] = True
        return _seal(body)

    declarations = {
        _operation_id(declaration.operation): declaration
        for declaration in profile.operation_effects
    }
    task_order = _task_order(plan)
    task_position = {task.task_id: index for index, task in enumerate(plan.tasks)}
    findings: list[dict[str, object]] = []
    unknown_pairs: list[dict[str, object]] = []
    unknown_pairs_truncated = False

    def report_unknown(pair: dict[str, object]) -> None:
        """Retain bounded unknown evidence and mark any omitted pair explicitly."""
        nonlocal unknown_pairs_truncated
        if len(unknown_pairs) < MAX_FINDINGS:
            unknown_pairs.append(pair)
        else:
            unknown_pairs_truncated = True

    for left_index, left in enumerate(slots):
        for right in slots[left_index + 1 :]:
            left_task, left_role, actor_id, left_operation, left_parameters = left
            right_task, right_role, right_actor_id, right_operation, right_parameters = right
            actor_ids = sorted({actor_id, right_actor_id})
            if left_task == right_task and left_role == right_role:
                continue
            left_declaration = declarations.get(left_operation)
            right_declaration = declarations.get(right_operation)
            if left_declaration is None or right_declaration is None:
                report_unknown(
                    {
                        "actor_ids": actor_ids,
                        "task_ids": sorted({left_task, right_task}),
                        "operations": sorted({left_operation, right_operation}),
                        "reason": "operation_effects_not_published",
                    }
                )
                continue
            if left_task == right_task:
                left_task_order = "same-task"
            else:
                first_task, second_task = sorted(
                    (left_task, right_task), key=task_position.__getitem__
                )
                left_task_order = task_order[(first_task, second_task)]
            if left_task_order == "reverse-ordered":
                earlier, later = right, left
                earlier_declaration, later_declaration = right_declaration, left_declaration
                earlier_order, later_order = right_task, left_task
            elif left_task_order == "ordered":
                earlier, later = left, right
                earlier_declaration, later_declaration = left_declaration, right_declaration
                earlier_order, later_order = left_task, right_task
            else:
                earlier, later = left, right
                earlier_declaration, later_declaration = left_declaration, right_declaration
                earlier_order, later_order = left_task, right_task
            earlier_parameters = earlier[4]
            later_parameters = later[4]
            earlier_effects = [
                (_effect_key(effect, earlier[2], earlier_parameters), effect)
                for effect in earlier_declaration.effects
            ]
            later_effects = [
                (_effect_key(effect, later[2], later_parameters), effect)
                for effect in later_declaration.effects
            ]
            unresolved_binding = any(key is None for key, _ in [*earlier_effects, *later_effects])
            overlaps = [
                (earlier_key, earlier_effect, later_effect)
                for earlier_key, earlier_effect in earlier_effects
                for later_key, later_effect in later_effects
                if earlier_key is not None
                and earlier_key == later_key
                and (earlier_effect.access != "read" or later_effect.access != "read")
            ]
            possible_physical_alias = left[2] != right[2] and any(
                earlier_effect.domain == later_effect.domain
                and earlier_effect.subject_parameter is None
                and later_effect.subject_parameter is None
                and (earlier_effect.access != "read" or later_effect.access != "read")
                for earlier_effect in earlier_declaration.effects
                for later_effect in later_declaration.effects
            )
            if overlaps:
                domains = sorted({key[0] for key, _, _ in overlaps})
                bindings: list[dict[str, object]] = []
                for key, earlier_effect, later_effect in overlaps:
                    first_value = (
                        earlier_parameters.get(earlier_effect.result_parameter)
                        if earlier_effect.result_parameter is not None
                        else None
                    )
                    second_value = (
                        later_parameters.get(later_effect.result_parameter)
                        if later_effect.result_parameter is not None
                        else None
                    )
                    bindings.append(
                        {
                            "domain": key[0],
                            "subject": key[1],
                            "first_access": earlier_effect.access,
                            "second_access": later_effect.access,
                            "first_result": first_value,
                            "second_result": second_value,
                            "result_relation": (
                                "same"
                                if first_value is not None and first_value == second_value
                                else "different"
                                if first_value is not None
                                and second_value is not None
                                and first_value != second_value
                                else "unknown"
                            ),
                        }
                    )
                findings.append(
                    {
                        "code": "possible-shared-state-interaction",
                        "actor_ids": actor_ids,
                        "task_order": (
                            "same-task"
                            if earlier_order == later_order
                            else "ordered"
                            if left_task_order in {"ordered", "reverse-ordered"}
                            else "unordered"
                        ),
                        "first_slot": {
                            "task_id": earlier[0],
                            "role_id": earlier[1],
                            "operation": earlier[3],
                        },
                        "second_slot": {
                            "task_id": later[0],
                            "role_id": later[1],
                            "operation": later[3],
                        },
                        "state_domains": domains,
                        "effect_bindings": bindings,
                        "declaration_coverage": {
                            "first": earlier_declaration.coverage,
                            "second": later_declaration.coverage,
                        },
                        "physical_entity_binding": "not-selected",
                        "interpretation": (
                            "these operations may interact through the same declared state domain"
                        ),
                    }
                )
            if (
                unresolved_binding
                or earlier_declaration.coverage != "complete"
                or later_declaration.coverage != "complete"
                or possible_physical_alias
            ):
                report_unknown(
                    {
                        "actor_ids": actor_ids,
                        "task_ids": sorted({earlier[0], later[0]}),
                        "operations": sorted({earlier[3], later[3]}),
                        "reason": (
                            "physical_entity_binding_unresolved"
                            if possible_physical_alias
                            else "effect_binding_unresolved"
                            if unresolved_binding
                            else "effect_declaration_is_partial"
                        ),
                    }
                )
            if len(findings) >= MAX_FINDINGS:
                body["truncated"] = True
                body["analysis_status"] = "truncated"
                break
        if len(findings) >= MAX_FINDINGS:
            break
    body["findings"] = findings
    body["unknown_pairs"] = unknown_pairs
    if unknown_pairs_truncated:
        body["truncated"] = True
        body["analysis_status"] = "truncated"
    return _seal(body)


def _seal(body: Mapping[str, object]) -> JSONObject:
    """Bind the advisory document to its exact source values."""
    digest = _canonical_digest(body)
    return cast(JSONObject, {**body, "digest": digest})
