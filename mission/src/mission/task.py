"""Mission Task timing, satisfaction, and Role aggregation contract values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mission.contract_values import (
    COUPLING_MODES,
    MISSION_PLAN_COMPAT_VERSION,
    MISSION_PLAN_COUPLING_VERSION,
    MISSION_PLAN_SATISFACTION_VERSION,
    MISSION_PLAN_SCHEDULING_VERSION,
    MISSION_PLAN_VERSION,
    U64_MAX,
    JSONObject,
    JSONValue,
    MissionPlanError,
    _array,
    _bounded_keys,
    _exact_keys,
    _object,
    _text,
)
from mission.role import CapabilityContractRef, RoleRequirement


@dataclass(frozen=True, slots=True)
class TaskTiming:
    """Declare receive-time scheduling constraints relative to Mission acceptance."""

    earliest_start_offset_ms: int
    latest_start_offset_ms: int | None
    completion_deadline_offset_ms: int | None
    estimated_duration_ms: int | None

    @classmethod
    def from_json(
        cls, value: JSONValue, path: str, *, duration_is_planner_evidence: bool = True
    ) -> TaskTiming:
        """Parse one closed timing declaration and enforce its local bounds."""
        item = _object(value, path)
        _exact_keys(
            item,
            {"earliest_start_offset_ms", "latest_start_offset_ms", "completion_deadline_offset_ms"}
            | ({"estimated_duration_ms"} if duration_is_planner_evidence else set()),
            path,
        )

        def optional_nonnegative(name: str) -> int | None:
            """Validate one nullable nonnegative millisecond value."""
            value = item[name]
            if value is None:
                return None
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > U64_MAX
            ):
                raise MissionPlanError(
                    f"{path}.{name} must be null or a nonnegative integer within 64-bit range"
                )
            return value

        earliest = optional_nonnegative("earliest_start_offset_ms")
        if earliest is None:
            raise MissionPlanError(
                f"{path}.earliest_start_offset_ms must be a nonnegative integer within 64-bit range"
            )
        latest = optional_nonnegative("latest_start_offset_ms")
        deadline = optional_nonnegative("completion_deadline_offset_ms")
        duration = (
            optional_nonnegative("estimated_duration_ms") if duration_is_planner_evidence else None
        )
        if duration_is_planner_evidence and duration is None:
            raise MissionPlanError(f"{path} requires estimated duration")
        if duration == 0:
            raise MissionPlanError(f"{path}.estimated_duration_ms must be positive when present")
        if duration is not None and earliest > U64_MAX - duration:
            raise MissionPlanError(f"{path}.estimated_duration_ms overflows earliest start")
        if latest is not None and latest < earliest:
            raise MissionPlanError(f"{path}.latest_start_offset_ms precedes earliest start")
        if deadline is not None and duration is not None:
            if deadline < earliest + duration:
                raise MissionPlanError(f"{path}.completion_deadline_offset_ms is infeasible")
        return cls(earliest, latest, deadline, duration)

    def to_json(self, *, include_estimate: bool = True) -> JSONObject:
        """Serialize relative timing without converting it to a wall-clock timestamp."""
        result: JSONObject = {
            "earliest_start_offset_ms": self.earliest_start_offset_ms,
            "latest_start_offset_ms": self.latest_start_offset_ms,
            "completion_deadline_offset_ms": self.completion_deadline_offset_ms,
        }
        if include_estimate:
            result["estimated_duration_ms"] = self.estimated_duration_ms
        return result


class TaskSatisfactionBasis(StrEnum):
    """Name the evidence policy used to accept one Task's semantic outcome."""

    EXECUTION_REPORT = "execution-report"
    VERIFIER_EVIDENCE = "verifier-evidence"


@dataclass(frozen=True, slots=True)
class VerifierSatisfactionPolicy:
    """Require fresh evidence from one canonical external verifier contract."""

    contract: CapabilityContractRef
    predicate: str
    max_evidence_age_ms: int

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> VerifierSatisfactionPolicy:
        """Parse one external verifier policy with a positive freshness bound."""
        item = _object(value, path)
        _exact_keys(item, {"contract", "predicate", "max_evidence_age_ms"}, path)
        max_age = item["max_evidence_age_ms"]
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 0 < max_age <= U64_MAX:
            raise MissionPlanError(f"{path}.max_evidence_age_ms must be a positive integer")
        return cls(
            contract=CapabilityContractRef.from_json(item["contract"], f"{path}.contract"),
            predicate=_text(item["predicate"], f"{path}.predicate"),
            max_evidence_age_ms=max_age,
        )

    def to_json(self) -> JSONObject:
        """Serialize one source-aware external verifier policy."""
        return {
            "contract": self.contract.to_json(),
            "predicate": self.predicate,
            "max_evidence_age_ms": self.max_evidence_age_ms,
        }


@dataclass(frozen=True, slots=True)
class TaskSatisfaction:
    """Declare the expected semantic effect and evidence basis for one Task."""

    expected_effect: str
    basis: TaskSatisfactionBasis
    verifier: VerifierSatisfactionPolicy | None = None

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> TaskSatisfaction:
        """Parse v0.7 completion semantics or normalize the v0.6 compatibility basis."""
        item = _object(value, path)
        if version == MISSION_PLAN_VERSION:
            _exact_keys(item, {"expected_effect", "basis", "verifier"}, path)
        else:
            _exact_keys(item, {"basis"}, path)
        basis_value = _text(item["basis"], f"{path}.basis")
        try:
            basis = TaskSatisfactionBasis(basis_value)
        except ValueError as error:
            raise MissionPlanError(f"{path}.basis is unsupported: {basis_value}") from error
        if version != MISSION_PLAN_VERSION:
            if basis is not TaskSatisfactionBasis.EXECUTION_REPORT:
                raise MissionPlanError(f"{path}.basis is unsupported before MissionPlan v0.7")
            return cls("Legacy Task description defines the expected effect", basis)
        verifier_value = item["verifier"]
        verifier = (
            None
            if verifier_value is None
            else VerifierSatisfactionPolicy.from_json(verifier_value, f"{path}.verifier")
        )
        if (basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE) != (verifier is not None):
            raise MissionPlanError(
                f"{path}.verifier must be present exactly for verifier-evidence basis"
            )
        return cls(
            expected_effect=_text(item["expected_effect"], f"{path}.expected_effect"),
            basis=basis,
            verifier=verifier,
        )

    def to_json(self, version: str) -> JSONObject:
        """Serialize completion semantics without conflating execution and satisfaction."""
        if version != MISSION_PLAN_VERSION:
            return {"basis": self.basis.value}
        return {
            "expected_effect": self.expected_effect,
            "basis": self.basis.value,
            "verifier": self.verifier.to_json() if self.verifier is not None else None,
        }


@dataclass(frozen=True, slots=True)
class MissionTask:
    """Describe one task node, its dependencies, and execution requirements."""

    task_id: str
    description: str
    depends_on: tuple[str, ...]
    roles: tuple[RoleRequirement, ...]
    context_id: str
    coupling_mode: str | None = None
    timing: TaskTiming | None = None
    satisfaction: TaskSatisfaction = TaskSatisfaction(
        "Legacy Task description defines the expected effect",
        TaskSatisfactionBasis.EXECUTION_REPORT,
    )

    @classmethod
    def from_json(cls, value: JSONValue, path: str, version: str) -> MissionTask:
        """Parse one versioned task and reject empty or duplicate role requirements."""
        item = _object(value, path)
        base_keys = {"id", "description", "depends_on", "roles", "context_id"}
        if version == MISSION_PLAN_COMPAT_VERSION:
            _exact_keys(item, base_keys, path)
        elif version == MISSION_PLAN_SCHEDULING_VERSION:
            _bounded_keys(item, base_keys | {"timing"}, {"coupling_mode"}, path)
        elif version in {MISSION_PLAN_SATISFACTION_VERSION, MISSION_PLAN_VERSION}:
            _bounded_keys(
                item,
                base_keys | {"timing", "satisfaction"},
                {"coupling_mode"},
                path,
            )
        else:
            _bounded_keys(item, base_keys, {"coupling_mode"}, path)
        dependencies = tuple(
            _text(dependency, f"{path}.depends_on[{index}]")
            for index, dependency in enumerate(_array(item["depends_on"], f"{path}.depends_on"))
        )
        if len(set(dependencies)) != len(dependencies):
            raise MissionPlanError(f"{path}.depends_on contains duplicates")
        roles = tuple(
            RoleRequirement.from_json(role, f"{path}.roles[{index}]", version)
            for index, role in enumerate(_array(item["roles"], f"{path}.roles"))
        )
        if not roles:
            raise MissionPlanError(f"{path}.roles must not be empty")
        role_ids = [role.role_id for role in roles]
        if len(set(role_ids)) != len(role_ids):
            raise MissionPlanError(f"{path}.roles contains duplicate ids")
        coupling_mode_value = item.get("coupling_mode")
        coupling_mode = (
            None
            if coupling_mode_value is None
            else _text(coupling_mode_value, f"{path}.coupling_mode")
        )
        if coupling_mode is not None and coupling_mode not in COUPLING_MODES:
            raise MissionPlanError(f"{path}.coupling_mode is unsupported: {coupling_mode}")
        timing = (
            TaskTiming.from_json(
                item["timing"],
                f"{path}.timing",
                duration_is_planner_evidence=version != MISSION_PLAN_VERSION,
            )
            if version
            in {
                MISSION_PLAN_SCHEDULING_VERSION,
                MISSION_PLAN_SATISFACTION_VERSION,
                MISSION_PLAN_VERSION,
            }
            else None
        )
        if version in {MISSION_PLAN_SATISFACTION_VERSION, MISSION_PLAN_VERSION}:
            satisfaction = TaskSatisfaction.from_json(
                item["satisfaction"], f"{path}.satisfaction", version
            )
        else:
            satisfaction = TaskSatisfaction(
                "Legacy Task description defines the expected effect",
                TaskSatisfactionBasis.EXECUTION_REPORT,
            )
        return cls(
            task_id=_text(item["id"], f"{path}.id"),
            description=_text(item["description"], f"{path}.description"),
            depends_on=dependencies,
            roles=roles,
            context_id=_text(item["context_id"], f"{path}.context_id"),
            coupling_mode=coupling_mode,
            timing=timing,
            satisfaction=satisfaction,
        )

    def to_json(self, version: str) -> JSONObject:
        """Serialize one task in its declared MissionPlan version."""
        result: JSONObject = {
            "id": self.task_id,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "roles": [role.to_json(version) for role in self.roles],
            "context_id": self.context_id,
        }
        if (
            version
            in {
                MISSION_PLAN_COUPLING_VERSION,
                MISSION_PLAN_SCHEDULING_VERSION,
                MISSION_PLAN_SATISFACTION_VERSION,
                MISSION_PLAN_VERSION,
            }
            and self.coupling_mode is not None
        ):
            result["coupling_mode"] = self.coupling_mode
        if version in {
            MISSION_PLAN_SCHEDULING_VERSION,
            MISSION_PLAN_SATISFACTION_VERSION,
            MISSION_PLAN_VERSION,
        }:
            if self.timing is None:
                raise MissionPlanError(f"task {self.task_id} lacks required scheduling timing")
            result["timing"] = self.timing.to_json(include_estimate=version != MISSION_PLAN_VERSION)
        if version in {MISSION_PLAN_SATISFACTION_VERSION, MISSION_PLAN_VERSION}:
            result["satisfaction"] = self.satisfaction.to_json(version)
        return result

    @property
    def satisfaction_basis(self) -> TaskSatisfactionBasis:
        """Return the compatibility basis view used by existing request policy."""
        return self.satisfaction.basis
