"""Semantic invariant checks for the Mission Front-half eval.

Invariants are evaluated against the serialized ``MissionRequestRecord`` of a
finished case plus the case's declared expectations. They are deliberately
semantic — coverage, capability identities, lifecycle behavior, leakage
patterns — never exact DAG matches, so a plan may vary in decomposition while
still preserving the user's objective.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

GLOBAL_FORBIDDEN_PATTERNS: Final[tuple[str, ...]] = (
    r"\bnav_to_obj\b",
    r"\bnav_to_goal\b",
    r"\bbase_velocity\b",
    r"\bcmd_vel\b",
    r"\bwaypoint",
    r"\boracle\s+skill",
    r"\bROS\s?2?\b",
    r"\bgpt-\d",
    r"\barticulated_agent\b",
    r"https?://",
    r"\[\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*\]",
)


@dataclass(frozen=True, slots=True)
class InvariantOutcome:
    """Report one named semantic invariant check."""

    name: str
    passed: bool
    detail: str


def _plan(record: dict[str, object]) -> dict[str, object]:
    """Return the plan sub-object of a record, or an empty mapping.

    Args:
        record: The serialized MissionRequestRecord.

    Returns:
        The plan mapping when present, otherwise an empty mapping.
    """
    plan = record.get("plan")
    return plan if isinstance(plan, dict) else {}


def _mission(record: dict[str, object]) -> dict[str, object]:
    """Return the mission spec sub-object of a record, or an empty mapping.

    Args:
        record: The serialized MissionRequestRecord.

    Returns:
        The mission mapping when present, otherwise an empty mapping.
    """
    mission = _plan(record).get("mission")
    return mission if isinstance(mission, dict) else {}


def collect_roles(record: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Collect every TaskRole of the plan together with its owning task id.

    Args:
        record: The serialized MissionRequestRecord.

    Returns:
        A list of ``(task_id, role_json)`` pairs in plan order.
    """
    tasks = _plan(record).get("tasks")
    if not isinstance(tasks, list):
        return []
    pairs: list[tuple[str, dict[str, object]]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id", "?"))
        roles = task.get("roles")
        if not isinstance(roles, list):
            continue
        for role in roles:
            if isinstance(role, dict):
                pairs.append((task_id, role))
    return pairs


def _capability_identities(role: dict[str, object]) -> list[str]:
    """Return the canonical capability identities required by one role.

    Args:
        role: One serialized TaskRole.

    Returns:
        ``namespace.name@version`` identities from the role requirements.
    """
    requirements = role.get("requirements")
    if not isinstance(requirements, dict):
        return []
    capabilities = requirements.get("capabilities")
    if not isinstance(capabilities, list):
        return []
    identities: list[str] = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        contract = capability.get("contract")
        if not isinstance(contract, dict):
            continue
        namespace = contract.get("namespace")
        name = contract.get("name")
        version = contract.get("version")
        if all(isinstance(part, str) for part in (namespace, name, version)):
            identities.append(f"{namespace}.{name}@{version}")
    return identities


def _execution_identity(role: dict[str, object]) -> str | None:
    """Return the canonical operation identity of one role's execution intent.

    Args:
        role: One serialized TaskRole.

    Returns:
        The ``namespace.name@version`` identity, or ``None`` when absent.
    """
    execution = role.get("execution_intent")
    if not isinstance(execution, dict):
        return None
    contract = execution.get("capability_contract")
    if not isinstance(contract, dict):
        return None
    namespace = contract.get("namespace")
    name = contract.get("name")
    version = contract.get("version")
    if all(isinstance(part, str) for part in (namespace, name, version)):
        return f"{namespace}.{name}@{version}"
    return None


def harvest_strings(value: object) -> list[str]:
    """Harvest every string in a nested JSON value.

    Args:
        value: Arbitrary decoded JSON.

    Returns:
        All string leaves, in depth-first order.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for key, entry in value.items() for s in [key, *harvest_strings(entry)]]
    if isinstance(value, list):
        return [s for entry in value for s in harvest_strings(entry)]
    return []


def check_final_lifecycle(
    case: object, record: dict[str, object], expected_lifecycle: str
) -> InvariantOutcome:
    """Check the final lifecycle against the case expectation.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        expected_lifecycle: The lifecycle the case expects to reach.

    Returns:
        The invariant outcome; details include recorded issues on mismatch.
    """
    lifecycle = str(record.get("lifecycle", "unknown"))
    if lifecycle == expected_lifecycle:
        return InvariantOutcome("final_lifecycle", True, f"reached {lifecycle}")
    issues = record.get("issues")
    issue_list = [str(issue) for issue in issues] if isinstance(issues, list) else []
    detail = f"expected {expected_lifecycle}, reached {lifecycle}"
    if issue_list:
        detail += f"; issues: {'; '.join(issue_list)[:400]}"
    return InvariantOutcome("final_lifecycle", False, detail)


def check_clarification_behavior(
    case: object, record: dict[str, object], clarify_first_pass: bool
) -> InvariantOutcome:
    """Check that clarification behavior matches the case expectation.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        clarify_first_pass: Whether the case expects clarification questions.

    Returns:
        The invariant outcome; over-asking on unambiguous cases and silent
        guessing on ambiguous cases both fail.
    """
    dialogue = record.get("dialogue")
    turns = dialogue if isinstance(dialogue, list) else []
    questions = [
        str(turn.get("content", ""))
        for turn in turns
        if isinstance(turn, dict)
        and turn.get("kind") == "ClarificationQuestion"
        and turn.get("speaker") == "MissionIntelligence"
    ]
    if clarify_first_pass:
        if questions:
            return InvariantOutcome(
                "clarification_behavior",
                True,
                f"{len(questions)} clarification question(s) asked: {' | '.join(questions[:3])}",
            )
        return InvariantOutcome(
            "clarification_behavior",
            False,
            "case expected clarification questions but the pipeline produced none",
        )
    if not questions:
        return InvariantOutcome(
            "clarification_behavior", True, "no clarification needed, as expected"
        )
    return InvariantOutcome(
        "clarification_behavior",
        False,
        f"unambiguous case produced {len(questions)} clarification question(s): "
        f"{' | '.join(questions[:3])}",
    )


def check_objective_fidelity(
    case: object, record: dict[str, object], must_cover: tuple[str, ...]
) -> InvariantOutcome:
    """Check that the accepted objective preserves the user's key entities.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        must_cover: Terms (entity or action keywords) that must survive into
            the objective, case-insensitively.

    Returns:
        The invariant outcome; coverage is checked against the plan objective
        first and the grounded-intent objective as fallback.
    """
    mission = _mission(record)
    plan_objective = str(mission.get("objective", ""))
    assessment = record.get("assessment")
    intent_objective = str(assessment.get("objective", "")) if isinstance(assessment, dict) else ""
    haystack = f"{plan_objective}\n{intent_objective}".lower()
    missing = [term for term in must_cover if term.lower() not in haystack]
    if not must_cover:
        return InvariantOutcome("objective_fidelity", True, "no coverage terms declared")
    if not missing:
        return InvariantOutcome(
            "objective_fidelity",
            True,
            f"covered {len(must_cover)} term(s) in: {plan_objective[:160]}",
        )
    return InvariantOutcome(
        "objective_fidelity",
        False,
        f"objective dropped terms {missing}; plan objective: {plan_objective[:200]!r}",
    )


def check_decomposition_sanity(
    case: object,
    record: dict[str, object],
    *,
    min_tasks: int,
    min_roles: int,
    min_actors: int,
) -> InvariantOutcome:
    """Check decomposition shape: counts, dependency integrity, role presence.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        min_tasks: Minimum number of tasks expected.
        min_roles: Minimum total number of TaskRoles expected.
        min_actors: Minimum number of declared Actors expected.

    Returns:
        The invariant outcome describing any structural defect found.
    """
    plan = _plan(record)
    mission = _mission(record)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return InvariantOutcome("decomposition_sanity", False, "plan carries no tasks")
    actors = mission.get("actors")
    actor_count = len(actors) if isinstance(actors, list) else 0
    role_pairs = collect_roles(record)
    task_ids = {str(task.get("id")) for task in tasks if isinstance(task, dict)}
    defects: list[str] = []
    if len(tasks) < min_tasks:
        defects.append(f"tasks {len(tasks)} < {min_tasks}")
    if len(role_pairs) < min_roles:
        defects.append(f"roles {len(role_pairs)} < {min_roles}")
    if actor_count < min_actors:
        defects.append(f"actors {actor_count} < {min_actors}")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        roles = task.get("roles")
        if not isinstance(roles, list) or not roles:
            defects.append(f"task {task.get('id')!r} has no roles")
        depends = task.get("depends_on")
        if isinstance(depends, list):
            for dependency in depends:
                if dependency not in task_ids:
                    defects.append(
                        f"task {task.get('id')!r} depends on unknown task {dependency!r}"
                    )
    if defects:
        return InvariantOutcome("decomposition_sanity", False, "; ".join(defects))
    return InvariantOutcome(
        "decomposition_sanity",
        True,
        f"tasks={len(tasks)} roles={len(role_pairs)} actors={actor_count}",
    )


def check_capability_coverage(
    case: object,
    record: dict[str, object],
    require_capabilities: tuple[str, ...],
) -> InvariantOutcome:
    """Check that role requirements cover the case's expected capabilities.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        require_capabilities: Canonical capability identities that must appear
            among the union of role requirements.

    Returns:
        The invariant outcome listing any missing capability identity.
    """
    if not require_capabilities:
        return InvariantOutcome("capability_coverage", True, "no capability expectations")
    roles = collect_roles(record)
    present: set[str] = set()
    for _, role in roles:
        present.update(_capability_identities(role))
    missing = [identity for identity in require_capabilities if identity not in present]
    if not missing:
        return InvariantOutcome(
            "capability_coverage",
            True,
            f"required capabilities covered: {sorted(require_capabilities)}",
        )
    return InvariantOutcome(
        "capability_coverage",
        False,
        f"missing required capabilities {missing}; plan declared {sorted(present)}",
    )


def check_integrated_operation(
    case: object,
    record: dict[str, object],
    integrated_operation: str | None,
    forbid_patterns: tuple[str, ...],
) -> InvariantOutcome:
    """Check that an integrated operation survives as one semantic goal.

    When a case expects an integrated operation (for example
    ``object.relocate@v1``), some role's execution intent must use exactly
    that operation, and no task description may match the case's forbidden
    decomposition patterns (evidence that the planner wrongly split an
    integrated Local EAIOS goal into primitive steps).

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        integrated_operation: The expected operation identity, or ``None``.
        forbid_patterns: Regex patterns that task descriptions must not match.

    Returns:
        The invariant outcome describing the operation usage or the offending
        decomposition.
    """
    if integrated_operation is None:
        return InvariantOutcome("integrated_operation", True, "no integrated expectation")
    roles = collect_roles(record)
    used = [
        (task_id, _execution_identity(role))
        for task_id, role in roles
        if _execution_identity(role) is not None
    ]
    if not any(identity == integrated_operation for _, identity in used):
        return InvariantOutcome(
            "integrated_operation",
            False,
            f"expected operation {integrated_operation} not used; execution intents: "
            f"{[identity for _, identity in used]}",
        )
    plan = _plan(record)
    tasks = plan.get("tasks")
    offenders: list[str] = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict):
            continue
        description = str(task.get("description", ""))
        for pattern in forbid_patterns:
            if re.search(pattern, description, flags=re.IGNORECASE):
                offenders.append(f"task {task.get('id')!r} matches {pattern!r}")
    if offenders:
        return InvariantOutcome(
            "integrated_operation",
            False,
            "integrated operation present but decomposition leaked: " + "; ".join(offenders),
        )
    return InvariantOutcome(
        "integrated_operation",
        True,
        f"operation {integrated_operation} preserved as one semantic goal",
    )


def check_resource_kinds(
    case: object, record: dict[str, object], resource_kinds: tuple[str, ...]
) -> InvariantOutcome:
    """Check that declared resource kinds appear in role requirements.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        resource_kinds: Resource kinds (``space`` / ``compute`` / ``time``)
            that must appear in at least one role.

    Returns:
        The invariant outcome listing any missing resource kind.
    """
    if not resource_kinds:
        return InvariantOutcome("resource_kinds", True, "no resource expectations")
    roles = collect_roles(record)
    present: set[str] = set()
    for _, role in roles:
        requirements = role.get("requirements")
        if not isinstance(requirements, dict):
            continue
        resources = requirements.get("resources")
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if isinstance(resource, dict) and isinstance(resource.get("kind"), str):
                present.add(str(resource["kind"]))
    missing = [kind for kind in resource_kinds if kind not in present]
    if not missing:
        return InvariantOutcome(
            "resource_kinds", True, f"required resource kinds covered: {sorted(resource_kinds)}"
        )
    return InvariantOutcome(
        "resource_kinds",
        False,
        f"missing resource kinds {missing}; plan declared {sorted(present)}",
    )


def check_timing_presence(
    case: object, record: dict[str, object], timing_required: bool
) -> InvariantOutcome:
    """Check that timing constraints survive into tasks when expected.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        timing_required: Whether the case expects at least one task to carry
            a timing block.

    Returns:
        The invariant outcome describing how many tasks carry timing.
    """
    if not timing_required:
        return InvariantOutcome("timing_presence", True, "no timing expectation")
    plan = _plan(record)
    tasks = plan.get("tasks")
    with_timing = 0
    total = 0
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict):
            continue
        total += 1
        if isinstance(task.get("timing"), dict):
            with_timing += 1
    if with_timing:
        return InvariantOutcome(
            "timing_presence", True, f"{with_timing}/{total} task(s) carry timing constraints"
        )
    return InvariantOutcome(
        "timing_presence",
        False,
        f"user declared timing constraints but 0/{total} tasks carry a timing block",
    )


def check_local_how_leakage(
    case: object,
    record: dict[str, object],
    extra_patterns: tuple[str, ...],
) -> InvariantOutcome:
    """Check that the plan stays at semantic level without Local How leakage.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.
        extra_patterns: Case-specific leakage regexes beyond the global list.

    Returns:
        The invariant outcome listing every leaking string and its pattern.
    """
    strings = harvest_strings(_plan(record))
    offenders: list[str] = []
    for pattern in GLOBAL_FORBIDDEN_PATTERNS + extra_patterns:
        compiled = re.compile(pattern, flags=re.IGNORECASE)
        for text in strings:
            match = compiled.search(text)
            if match:
                offenders.append(f"{text[:80]!r} ~ {pattern!r}")
                break
    if not offenders:
        return InvariantOutcome(
            "local_how_leakage",
            True,
            f"no Local How leakage across {len(strings)} plan strings",
        )
    return InvariantOutcome(
        "local_how_leakage",
        False,
        f"{len(offenders)} leakage site(s): " + "; ".join(offenders[:6]),
    )


def check_review_repair_convergence(case: object, record: dict[str, object]) -> InvariantOutcome:
    """Check that the Reviewer/Repair loop converges instead of exhausting.

    Args:
        case: The eval case (used only for naming).
        record: The serialized MissionRequestRecord.

    Returns:
        The invariant outcome; a run that ends ``Failed`` after exhausting
        repair attempts is treated as a Reviewer/Repair effectiveness defect.
    """
    attempts = record.get("repair_attempts")
    attempt_count = attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 0
    review_history = record.get("review_history")
    review_count = len(review_history) if isinstance(review_history, list) else 0
    lifecycle = str(record.get("lifecycle", "unknown"))
    issues = record.get("issues")
    issue_list = [str(issue) for issue in issues] if isinstance(issues, list) else []
    if attempt_count > 0 and lifecycle == "Failed":
        exhausted = any("exhausted" in issue for issue in issue_list)
        detail = (
            f"repair exhausted after {attempt_count} attempt(s), {review_count} review(s)"
            if exhausted
            else f"failed after {attempt_count} repair attempt(s), {review_count} review(s)"
        )
        return InvariantOutcome("review_repair_convergence", False, detail)
    if review_count == 0:
        return InvariantOutcome(
            "review_repair_convergence", True, "no review cycle engaged for this case"
        )
    return InvariantOutcome(
        "review_repair_convergence",
        True,
        f"{review_count} review(s), {attempt_count} repair(s), converged to {lifecycle}",
    )
