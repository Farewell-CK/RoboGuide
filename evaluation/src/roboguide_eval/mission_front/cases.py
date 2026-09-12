"""Mission Front-half Eval case definitions: loading, validation, and access.

A case describes one real-model probe of the Mission Intelligence front half
(Dialogue → Interpreter → Planner → Capability Catalog v0.3 → MissionPlan
v0.7 → Validator → Reviewer → Repair): the user instruction, optional
clarification follow-ups, and the semantic expectations the harness checks
afterwards. Expectations are semantic invariants (coverage, capability
identities, lifecycle) — never exact DAG matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import yaml

CASES_SCHEMA: Final = "roboguide-eval.mission-front-cases/v0.1"
CASE_CATEGORIES: Final = frozenset(
    {
        "normal",
        "ambiguity",
        "heterogeneous",
        "integrated",
        "cross_node",
        "resource_timing",
        "multi_role",
    }
)
FINAL_LIFECYCLES: Final = frozenset(
    {"Accepted", "NeedsClarification", "Failed", "AwaitingApproval"}
)


class MissionFrontCaseError(ValueError):
    """Report an invalid Mission Front-half eval case definition."""


@dataclass(frozen=True, slots=True)
class CaseExpectations:
    """Declare the semantic expectations checked after one case finishes."""

    final_lifecycle: str = "Accepted"
    clarify_first_pass: bool = False
    must_cover_objective: tuple[str, ...] = ()
    min_tasks: int = 1
    min_roles: int = 1
    min_actors: int = 1
    require_capabilities: tuple[str, ...] = ()
    integrated_operation: str | None = None
    forbid_task_description_patterns: tuple[str, ...] = ()
    resource_kinds: tuple[str, ...] = ()
    timing_required: bool = False
    extra_forbidden_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MissionFrontCase:
    """Define one front-half evaluation probe with its semantic expectations."""

    case_id: str
    category: str
    instruction: str
    follow_ups: tuple[str, ...] = ()
    expectations: CaseExpectations = field(default_factory=CaseExpectations)


def load_cases(path: Path) -> tuple[MissionFrontCase, ...]:
    """Load and fully validate one Mission Front-half case file.

    Args:
        path: YAML file containing the versioned ``cases`` list.

    Returns:
        The validated cases in declaration order.

    Raises:
        MissionFrontCaseError: If the file cannot be read, is not a mapping,
            uses a wrong schema, or any case violates the case contract.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise MissionFrontCaseError(f"cannot read case file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise MissionFrontCaseError(f"case file {path} is not valid YAML: {error}") from error
    if not isinstance(document, dict) or not all(isinstance(key, str) for key in document):
        raise MissionFrontCaseError(f"{path} must be a mapping")
    if document.get("schema") != CASES_SCHEMA:
        raise MissionFrontCaseError(
            f"{path} schema must be {CASES_SCHEMA!r}, got {document.get('schema')!r}"
        )
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise MissionFrontCaseError(f"{path}.cases must be a nonempty list")
    cases: list[MissionFrontCase] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_cases):
        case = _parse_case(item, f"{path}.cases[{index}]")
        if case.case_id in seen:
            raise MissionFrontCaseError(f"{path}: duplicate case id {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def _parse_case(item: object, path: str) -> MissionFrontCase:
    """Parse and validate one case mapping.

    Args:
        item: The decoded YAML mapping for one case.
        path: Dotted path for error messages.

    Returns:
        The validated :class:`MissionFrontCase`.

    Raises:
        MissionFrontCaseError: If required fields are missing, types are
            wrong, or enum-like fields carry unsupported values.
    """
    if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
        raise MissionFrontCaseError(f"{path} must be a mapping")
    allowed = {
        "id",
        "category",
        "instruction",
        "follow_ups",
        "expect",
    }
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise MissionFrontCaseError(f"{path} has unknown keys: {unknown}")
    case_id = item.get("id")
    category = item.get("category")
    instruction = item.get("instruction")
    if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", case_id):
        raise MissionFrontCaseError(f"{path}.id must match [a-z0-9][a-z0-9-]{{2,63}}")
    if not isinstance(category, str) or category not in CASE_CATEGORIES:
        raise MissionFrontCaseError(f"{path}.category must be one of {sorted(CASE_CATEGORIES)}")
    if not isinstance(instruction, str) or not instruction.strip():
        raise MissionFrontCaseError(f"{path}.instruction must be nonblank text")
    follow_ups_value = item.get("follow_ups", [])
    if not isinstance(follow_ups_value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in follow_ups_value
    ):
        raise MissionFrontCaseError(f"{path}.follow_ups must be a list of nonblank strings")
    expectations = _parse_expectations(item.get("expect", {}), f"{path}.expect")
    return MissionFrontCase(
        case_id=case_id,
        category=category,
        instruction=instruction,
        follow_ups=tuple(follow_ups_value),
        expectations=expectations,
    )


def _parse_expectations(value: object, path: str) -> CaseExpectations:
    """Parse the ``expect`` block of one case.

    Args:
        value: The decoded expectations mapping (may be empty).
        path: Dotted path for error messages.

    Returns:
        The validated :class:`CaseExpectations`.

    Raises:
        MissionFrontCaseError: If any expectation field has the wrong type
            or value.
    """
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionFrontCaseError(f"{path} must be a mapping")
    allowed = {
        "final_lifecycle",
        "clarify_first_pass",
        "must_cover_objective",
        "min_tasks",
        "min_roles",
        "min_actors",
        "require_capabilities",
        "integrated_operation",
        "forbid_task_description_patterns",
        "resource_kinds",
        "timing_required",
        "extra_forbidden_patterns",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise MissionFrontCaseError(f"{path} has unknown keys: {unknown}")

    def terms(key: str) -> tuple[str, ...]:
        """Read one list-of-strings expectation field.

        Args:
            key: The expectation field name.

        Returns:
            The tuple of nonblank strings.

        Raises:
            MissionFrontCaseError: If the field is not a string list.
        """
        entries = value.get(key, [])
        if not isinstance(entries, list) or not all(
            isinstance(entry, str) and entry for entry in entries
        ):
            raise MissionFrontCaseError(f"{path}.{key} must be a list of nonblank strings")
        return tuple(entries)

    def bounded_int(key: str) -> int:
        """Read one nonnegative integer expectation field.

        Args:
            key: The expectation field name.

        Returns:
            The nonnegative integer value.

        Raises:
            MissionFrontCaseError: If the field is not a nonnegative integer.
        """
        entry = value.get(key, 1)
        if isinstance(entry, bool) or not isinstance(entry, int) or entry < 0:
            raise MissionFrontCaseError(f"{path}.{key} must be a nonnegative integer")
        return entry

    final_lifecycle = value.get("final_lifecycle", "Accepted")
    if not isinstance(final_lifecycle, str) or final_lifecycle not in FINAL_LIFECYCLES:
        raise MissionFrontCaseError(
            f"{path}.final_lifecycle must be one of {sorted(FINAL_LIFECYCLES)}"
        )
    clarify = value.get("clarify_first_pass", False)
    if not isinstance(clarify, bool):
        raise MissionFrontCaseError(f"{path}.clarify_first_pass must be a boolean")
    timing_required = value.get("timing_required", False)
    if not isinstance(timing_required, bool):
        raise MissionFrontCaseError(f"{path}.timing_required must be a boolean")
    integrated = value.get("integrated_operation")
    if integrated is not None and (not isinstance(integrated, str) or not integrated):
        raise MissionFrontCaseError(f"{path}.integrated_operation must be text or null")
    patterns = terms("forbid_task_description_patterns") + terms("extra_forbidden_patterns")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            raise MissionFrontCaseError(
                f"{path} has an invalid forbidden pattern {pattern!r}: {error}"
            ) from error
    return CaseExpectations(
        final_lifecycle=final_lifecycle,
        clarify_first_pass=clarify,
        must_cover_objective=terms("must_cover_objective"),
        min_tasks=bounded_int("min_tasks"),
        min_roles=bounded_int("min_roles"),
        min_actors=bounded_int("min_actors"),
        require_capabilities=terms("require_capabilities"),
        integrated_operation=integrated,
        forbid_task_description_patterns=terms("forbid_task_description_patterns"),
        resource_kinds=terms("resource_kinds"),
        timing_required=timing_required,
        extra_forbidden_patterns=tuple(terms("extra_forbidden_patterns")),
    )
