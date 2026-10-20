"""Immutable deployment verifier support and execution-lifetime facts for MI.

These are published declarations, never live Node inventory, reservations,
route feasibility or affirmative world truth. Control retains admission and
resource authority; an advertised verifier still needs an actual verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import cast

from mission.models import JSONObject, MissionPlan, TaskSatisfactionBasis
from mission.plan_validation import MAX_PLAN_ISSUES, PlanValidationError
from mission.semantic_evidence import AuthoritativeSemanticEvidence

EXECUTION_PLANNING_SCHEMA = "roboguide.execution-planning-evidence/v0.2"
LEGACY_EXECUTION_PLANNING_SCHEMA = "roboguide.execution-planning-evidence/v0.1"
MAX_EXECUTION_PLANNING_BYTES = 65_536
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_IDENTITY_KEYS = {"run_id", "episode_id", "scene_id", "dataset_revision", "dataset_sha256"}


class ExecutionPlanningEvidenceError(ValueError):
    """Reject incomplete, unsourced or contradictory published execution facts."""


def _object(value: object, keys: set[str], path: str) -> JSONObject:
    """Require a closed JSON object, preventing inventory fields from entering MI."""
    if not isinstance(value, dict) or set(value) != keys:
        raise ExecutionPlanningEvidenceError(f"{path} fields are invalid")
    return cast(JSONObject, value)


def _text(value: object, path: str, limit: int = 2048) -> str:
    """Require bounded nonblank public text without coercing missing values."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ExecutionPlanningEvidenceError(f"{path} must be bounded nonblank text")
    return value


def _digest(value: object) -> str:
    """Hash strict public JSON using the existing cross-language source encoding."""
    return "sha256:" + hashlib.sha256(_encode(value).encode()).hexdigest()


def _encode(value: object) -> str:
    """Bound finite JSON before accepting it as immutable deliberation evidence."""
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise ExecutionPlanningEvidenceError(
            "execution planning evidence is not strict JSON"
        ) from error
    if len(raw.encode()) > MAX_EXECUTION_PLANNING_BYTES:
        raise ExecutionPlanningEvidenceError("execution planning evidence exceeds its byte budget")
    return raw


def _integer(value: object, path: str, maximum: int) -> int:
    """Require a positive integer declaration, excluding booleans and fractions."""
    if type(value) is not int or not 1 <= value <= maximum:
        raise ExecutionPlanningEvidenceError(f"{path} must be an integer in 1..{maximum}")
    return value


def validate_execution_topology(value: object) -> None:
    """Validate explicit deployment support; an older artifact grants no missing features."""
    topology = _object(
        value,
        {
            "supported_coupling_modes",
            "supports_task_dependencies",
            "supports_context_relations",
            "actor_binding",
        },
        "execution topology",
    )
    modes = topology["supported_coupling_modes"]
    vocabulary = {
        "independent",
        "sequential-handoff",
        "concurrent-cooperation",
        "tightly-coupled-cooperation",
    }
    if (
        not isinstance(modes, list)
        or not modes
        or any(not isinstance(mode, str) or mode not in vocabulary for mode in modes)
        or modes != sorted(set(cast(list[str], modes)))
    ):
        raise ExecutionPlanningEvidenceError("supported coupling modes are invalid")
    if (
        any(
            type(topology[key]) is not bool
            for key in ("supports_task_dependencies", "supports_context_relations")
        )
        or topology["actor_binding"] != "session-stable"
    ):
        raise ExecutionPlanningEvidenceError("execution topology support is invalid")


@dataclass(frozen=True, slots=True)
class ExecutionPlanningEvidence:
    """Retain one bounded canonical artifact; all returned JSON is a defensive copy."""

    encoded_document: str

    def __post_init__(self) -> None:
        """Validate source digests, identities and the supported closed lifetime profile."""
        try:
            decoded: object = json.loads(self.encoded_document)
        except (ValueError, RecursionError) as error:
            raise ExecutionPlanningEvidenceError(
                "execution planning evidence is malformed"
            ) from error
        schema = decoded.get("schema_version") if isinstance(decoded, dict) else None
        item = _object(
            decoded,
            {
                "schema_version",
                "authority",
                "identity",
                "verifier_source",
                "execution_lifetime",
                "digest",
            }
            | ({"execution_topology"} if schema == EXECUTION_PLANNING_SCHEMA else set()),
            "execution planning evidence",
        )
        _encode(item)
        if (
            not isinstance(schema, str)
            or schema not in {EXECUTION_PLANNING_SCHEMA, LEGACY_EXECUTION_PLANNING_SCHEMA}
            or item["authority"] != "deployment-published"
        ):
            raise ExecutionPlanningEvidenceError(
                "unsupported execution planning schema or authority"
            )
        if schema == EXECUTION_PLANNING_SCHEMA:
            validate_execution_topology(item["execution_topology"])
        identity = _object(
            item["identity"], _IDENTITY_KEYS | {"semantic_evidence_digest"}, "identity"
        )
        for key in _IDENTITY_KEYS:
            _text(identity[key], "identity." + key)
        dataset = cast(str, identity["dataset_sha256"])
        if re.fullmatch(r"[a-f0-9]{64}", dataset) is None:
            raise ExecutionPlanningEvidenceError("dataset digest is invalid")
        semantic_digest = _text(identity["semantic_evidence_digest"], "semantic digest")
        if _DIGEST.fullmatch(semantic_digest) is None:
            raise ExecutionPlanningEvidenceError("semantic digest is invalid")
        source = _object(
            item["verifier_source"],
            {
                "schema_version",
                "source_id",
                "source_revision",
                "identity",
                "verifier",
                "supported_predicates",
                "verdict_finality",
                "digest",
            },
            "verifier source",
        )
        if (
            source["schema_version"] != "roboguide.task-verifier-source/v0.1"
            or source["verdict_finality"] != "terminal"
        ):
            raise ExecutionPlanningEvidenceError("unsupported terminal verifier source")
        if source["source_id"] != "habitat-official-pddl":
            raise ExecutionPlanningEvidenceError("verifier source is not Habitat official PDDL")
        if source["source_revision"] != semantic_digest:
            raise ExecutionPlanningEvidenceError(
                "verifier source revision differs from semantic evidence"
            )
        source_identity = _object(source["identity"], _IDENTITY_KEYS, "verifier source identity")
        if any(source_identity[key] != identity[key] for key in _IDENTITY_KEYS):
            raise ExecutionPlanningEvidenceError("verifier source world identity differs")
        contract = _object(
            source["verifier"], {"namespace", "name", "version"}, "verifier contract"
        )
        for key, value in contract.items():
            _text(value, "verifier contract." + key, 128)
        predicates = source["supported_predicates"]
        if not isinstance(predicates, list) or len(predicates) > 32:
            raise ExecutionPlanningEvidenceError("verifier predicate list is invalid")
        for predicate in predicates:
            _text(predicate, "supported predicate", 8192)
        if len(predicates) != len(set(cast(list[str], predicates))):
            raise ExecutionPlanningEvidenceError("duplicate verifier predicate")
        if source["digest"] != _digest(
            {key: value for key, value in source.items() if key != "digest"}
        ):
            raise ExecutionPlanningEvidenceError("verifier source digest does not match")
        lifetime = _object(
            item["execution_lifetime"],
            {
                "declaration_digest",
                "configured_endpoint_count",
                "endpoint_capacity",
                "exclusive_resource_kind",
                "max_slots",
                "resource_release",
                "verifier_publication",
            },
            "execution lifetime",
        )
        if _DIGEST.fullmatch(_text(lifetime["declaration_digest"], "declaration digest")) is None:
            raise ExecutionPlanningEvidenceError("declaration digest is invalid")
        _integer(lifetime["configured_endpoint_count"], "configured endpoint count", 4)
        _integer(lifetime["max_slots"], "maximum slots", 4096)
        if type(lifetime["endpoint_capacity"]) is not int or lifetime["endpoint_capacity"] != 1:
            raise ExecutionPlanningEvidenceError("this profile requires capacity-one endpoints")
        _text(lifetime["exclusive_resource_kind"], "exclusive resource kind", 128)
        if (
            lifetime["resource_release"] != "after-task-satisfaction"
            or lifetime["verifier_publication"] != "after-all-session-slots"
        ):
            raise ExecutionPlanningEvidenceError("unsupported execution lifetime declaration")
        if item["digest"] != _digest(
            {key: value for key, value in item.items() if key != "digest"}
        ):
            raise ExecutionPlanningEvidenceError("execution planning digest does not match")

    @classmethod
    def from_json(cls, value: object) -> ExecutionPlanningEvidence:
        """Parse fixed-source evidence without retaining caller-owned mutable containers."""
        return cls(_encode(value))

    def to_json(self) -> JSONObject:
        """Return a defensive copy of exactly the digest-bound published artifact."""
        return cast(JSONObject, json.loads(self.encoded_document))

    def validate_world(self, semantic: AuthoritativeSemanticEvidence | None) -> None:
        """Bind declarations to the actual request snapshot, never an invented world."""
        if semantic is None:
            raise ExecutionPlanningEvidenceError(
                "execution planning evidence needs authoritative semantic identity"
            )
        identity = cast(JSONObject, self.to_json()["identity"])
        expected = {
            "run_id": semantic.run_id,
            "episode_id": semantic.episode_id,
            "scene_id": semantic.world_context.get("scene_id"),
            "dataset_revision": semantic.dataset_revision,
            "dataset_sha256": semantic.dataset_sha256,
            "semantic_evidence_digest": semantic.evidence_digest,
        }
        if identity != expected:
            raise ExecutionPlanningEvidenceError(
                "execution planning evidence differs from the frozen semantic world"
            )

    def validate_plan(self, plan: MissionPlan) -> None:
        """Reject only proven source/slot-lifetime incompatibilities before submission.

        This is a necessary deployment bound, not matching or a reservation.
        Independent Tasks may reuse an endpoint after Control releases it.
        This explicit final-world source retains verifier-backed endpoints until
        all slots execute. These necessary bounds do not replace Controller checks.
        Unknown physical route and current availability remain Control concerns.
        A rejected draft must be regenerated from its real semantics, never
        automatically rewritten into another Actor topology or weaker evidence.
        """
        issues = self.plan_issues(plan)
        if issues:
            raise PlanValidationError(issues)

    def plan_issues(self, plan: MissionPlan) -> list[JSONObject]:
        """Collect bounded logical conflicts without choosing bindings or rewriting a draft."""
        document = self.to_json()
        source = cast(JSONObject, document["verifier_source"])
        lifetime = cast(JSONObject, document["execution_lifetime"])
        issues: list[JSONObject] = []

        def report(code: str, path: str, message: str) -> None:
            """Keep independent exact-path findings within the public feedback bound."""
            issue: JSONObject = {"code": code, "path": path, "message": message}
            if len(issues) < MAX_PLAN_ISSUES and not any(
                existing["code"] == code and existing["path"] == path for existing in issues
            ):
                issues.append(issue)

        topology = document.get("execution_topology")
        if isinstance(topology, dict):
            contexts = {
                context.context_id: (index, context) for index, context in enumerate(plan.contexts)
            }
            for index, task in enumerate(plan.tasks):
                context_index, context = contexts[task.context_id]
                mode = task.coupling_mode or context.coupling_mode
                if mode not in cast(list[str], topology["supported_coupling_modes"]):
                    path = (
                        f"/tasks/{index}/coupling_mode"
                        if task.coupling_mode is not None
                        else f"/contexts/{context_index}/coupling_mode"
                    )
                    report(
                        "deployment.coupling_mode_unsupported",
                        path,
                        f"Effective coupling mode {mode!r} is outside the published modes "
                        f"{topology['supported_coupling_modes']}. Task dependencies are "
                        "separate from execution cooperation; preserve real cooperation "
                        "requirements when reassessing the mode.",
                    )
                if context.relations and not topology["supports_context_relations"]:
                    report(
                        "deployment.context_relations_unsupported",
                        f"/contexts/{context_index}/relations",
                        "The published deployment does not support Context execution relations. "
                        "Preserve required relations and expose the deployment gap; do not "
                        "delete a real cooperation requirement to pass admission.",
                    )
                if task.depends_on and not topology["supports_task_dependencies"]:
                    report(
                        "deployment.dependencies_unsupported",
                        f"/tasks/{index}/depends_on",
                        "Task dependencies are not supported by this published deployment.",
                    )
        if sum(len(task.roles) for task in plan.tasks) > cast(int, lifetime["max_slots"]):
            report(
                "deployment.slot_limit_exceeded",
                "/tasks",
                "plan exceeds the published deployment execution-slot bound",
            )
        capacity = cast(int, lifetime["configured_endpoint_count"])
        endpoint_capacity = cast(int, lifetime["endpoint_capacity"])
        context_units_by_role: dict[tuple[str, str], int] = {}
        for index, task in enumerate(plan.tasks):
            if task.satisfaction.basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE:
                verifier = task.satisfaction.verifier
                if (
                    verifier is None
                    or verifier.contract.to_json() != source["verifier"]
                    or verifier.predicate not in cast(list[str], source["supported_predicates"])
                ):
                    report(
                        "verifier.source_unsupported",
                        f"/tasks/{index}/satisfaction",
                        f"tasks[{index}].satisfaction: published verifier source does not "
                        "support the declared contract/predicate",
                    )
            task_units = 0
            task_context_units: dict[tuple[str, str], int] = {}
            for role_index, role in enumerate(task.roles):
                for resource in role.resources:
                    if resource.kind != lifetime["exclusive_resource_kind"]:
                        continue
                    if role.resource_scope == "context":
                        if role.context_role is None:
                            report(
                                "deployment.resource_requirement_invalid",
                                f"/tasks/{index}/roles/{role_index}/resource_scope",
                                f"tasks[{index}] context-scoped resource has no ContextRole",
                            )
                            continue
                        key = (task.context_id, role.context_role)
                        context_units_by_role[key] = max(
                            context_units_by_role.get(key, 0), resource.units
                        )
                        task_context_units[key] = max(
                            task_context_units.get(key, 0), resource.units
                        )
                    else:
                        task_units += resource.units
            simultaneous_units = task_units + sum(task_context_units.values())
            oversized_role = next(
                (
                    resource.units
                    for role in task.roles
                    for resource in role.resources
                    if resource.kind == lifetime["exclusive_resource_kind"]
                    and resource.units > endpoint_capacity
                ),
                None,
            )
            if oversized_role is not None:
                report(
                    "deployment.endpoint_capacity_exceeded",
                    f"/tasks/{index}/roles",
                    f"tasks[{index}] requires {oversized_role} units from each "
                    f"capacity-{endpoint_capacity} {lifetime['exclusive_resource_kind']} "
                    "endpoint; preserve the operation contract or use a deployment whose "
                    "published endpoint capacity supports it.",
                )
            if simultaneous_units > capacity:
                report(
                    "deployment.concurrent_capacity_exceeded",
                    f"/tasks/{index}/roles",
                    f"tasks[{index}] requires {simultaneous_units} units of "
                    f"{lifetime['exclusive_resource_kind']}, but the deployment publishes "
                    f"only {capacity} capacity-one endpoints. Split the real work into "
                    "valid dependencies or expose a deployment with sufficient capacity; "
                    "do not invent executors or weaken final verification.",
                )
        context_units = sum(context_units_by_role.values())
        if context_units > capacity:
            report(
                "deployment.context_capacity_exceeded",
                "/tasks",
                f"plan retains {context_units} units of context-scoped "
                f"{lifetime['exclusive_resource_kind']} resources, but the deployment publishes "
                f"only {capacity} capacity-one endpoints. Preserve the Context lifetime or use "
                "a deployment with sufficient capacity; do not invent executors.",
            )
        retained = sum(
            len(task.roles)
            for task in plan.tasks
            if task.satisfaction.basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE
        )
        if retained > capacity:
            report(
                "verifier.retained_capacity_exceeded",
                "/tasks",
                f"terminal verifier retains {retained} slots until all session slots execute, "
                f"but the deployment publishes only {capacity} endpoints",
            )
        for index, task in enumerate(plan.tasks):
            if task.satisfaction.basis is TaskSatisfactionBasis.VERIFIER_EVIDENCE and any(
                task.task_id in other.depends_on for other in plan.tasks
            ):
                report(
                    "verifier.blocks_dependent_work",
                    f"/tasks/{index}/satisfaction",
                    f"tasks[{index}].satisfaction waits for all session slots "
                    "before a dependent Task can execute",
                )
        if isinstance(topology, dict):
            context_actors = {
                (context.context_id, role.role_id): role.actor_id
                for context in plan.contexts
                for role in context.roles
                if role.actor_id is not None
            }
            actors_by_task = {
                task.task_id: {
                    context_actors[(task.context_id, role.context_role)]
                    for role in task.roles
                    if role.context_role is not None
                    and (task.context_id, role.context_role) in context_actors
                }
                for task in plan.tasks
            }
            tasks_by_id = {task.task_id: task for task in plan.tasks}
            for index, task in enumerate(plan.tasks):
                if task.satisfaction.basis is not TaskSatisfactionBasis.VERIFIER_EVIDENCE:
                    continue
                predecessors: set[str] = set()
                pending = list(task.depends_on)
                while pending:
                    dependency = pending.pop()
                    if dependency not in predecessors:
                        predecessors.add(dependency)
                        pending.extend(tasks_by_id[dependency].depends_on)
                if any(
                    other.task_id != task.task_id
                    and other.task_id not in predecessors
                    and actors_by_task[task.task_id] & actors_by_task[other.task_id]
                    for other in plan.tasks
                ):
                    report(
                        "verifier.blocks_actor_reuse",
                        f"/tasks/{index}/satisfaction",
                        f"Task {task.task_id} needs a final verifier verdict but its Actor "
                        "endpoint is needed by unordered work before the same terminal world "
                        "can close",
                    )
        return issues
