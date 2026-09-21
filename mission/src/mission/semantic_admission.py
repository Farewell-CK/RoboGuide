"""Deterministic semantic admission checks applied before Controller submission."""

from __future__ import annotations

from mission.contract_values import MissionPlanError
from mission.grounding_context import GroundingContextSnapshot, admitted_physical_entity_ids
from mission.models import MissionPlan
from mission.semantic_evidence import SemanticExpression


def validate_authoritative_executor_constraints(
    plan: MissionPlan,
    grounding_context: GroundingContextSnapshot,
) -> None:
    """Reject physical distinctness that strengthens an authoritative objective.

    Environment-authoritative semantics define the accepted objective for their
    run. A hard physical executor constraint is admitted only when every affected
    Actor is bound to a different fresh physical entity and each identity occurs
    in the authoritative goal. This is a deliberately sufficient, fail-closed
    proof: deployment availability, Actor count, and resource capacity cannot
    manufacture Mission semantics.

    Missions without authoritative semantic evidence retain the general v0.8
    contract behavior; their semantic review remains owned by the configured MI
    policy.
    """
    evidence = grounding_context.semantic_evidence
    if evidence is None:
        return
    admitted_entities = admitted_physical_entity_ids(grounding_context)
    goal_arguments = _predicate_arguments(evidence.goal)
    actors = {actor.actor_id: actor for actor in plan.mission.actors}
    for context_index, context in enumerate(plan.contexts):
        roles = {role.role_id: role for role in context.roles}
        for constraint_index, constraint in enumerate(context.executor_constraints):
            path = f"contexts[{context_index}].executor_constraints[{constraint_index}]"
            constrained_entities: list[str] = []
            for role_id in constraint.context_roles:
                actor = actors[roles[role_id].actor_id]
                entity_id = actor.physical_entity
                if (
                    entity_id is None
                    or entity_id not in admitted_entities
                    or entity_id not in goal_arguments
                ):
                    raise MissionPlanError(
                        f"{path} lacks authoritative physical-entity grounding "
                        f"for ContextRole {role_id}"
                    )
                constrained_entities.append(entity_id)
            if len(set(constrained_entities)) != len(constrained_entities):
                raise MissionPlanError(
                    f"{path} does not resolve to pairwise distinct authoritative entities"
                )


def _predicate_arguments(expression: SemanticExpression) -> frozenset[str]:
    """Collect exact predicate arguments from one immutable semantic tree."""
    if expression.kind == "predicate":
        return frozenset(expression.arguments)
    return frozenset(
        argument for operand in expression.operands for argument in _predicate_arguments(operand)
    )
