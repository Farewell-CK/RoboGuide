"""Provider-only MissionPlan DTO adaptation for strict structured output.

The canonical MissionPlan schema remains the semantic contract. Strict providers
represent a canonical optional property as a required nullable property; output
normalization converts only those adapter-added null sentinels back to omission.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

from mission.contract_values import (
    MISSION_PLAN_ACTOR_VERSION,
    MISSION_PLAN_CAPACITY_VERSION,
    MISSION_PLAN_MEMORY_VERSION,
    MISSION_PLAN_SCOPED_MEMORY_VERSION,
    MISSION_PLAN_VERSION,
    JSONObject,
    JSONValue,
)

ECHO_OBJECTIVE_PROFILE = "echo-objective-v1"
HOST_OBJECTIVE_PROFILE = "host-objective-v1"
PROVIDER_PLAN_PROFILES = frozenset({ECHO_OBJECTIVE_PROFILE, HOST_OBJECTIVE_PROFILE})


class ProviderMissionPlanError(ValueError):
    """Report malformed provider schemas or MissionPlan DTO output."""


def _canonical_provider_version(schema: JSONObject) -> str:
    """Require an explicitly selected maintained canonical DTO version before any provider call."""
    properties = _object(schema.get("properties"), "schema.properties")
    version = _object(properties.get("schema_version"), "schema.properties.schema_version")
    selected = version.get("const")
    if selected not in (
        MISSION_PLAN_VERSION,
        MISSION_PLAN_MEMORY_VERSION,
        MISSION_PLAN_CAPACITY_VERSION,
        MISSION_PLAN_SCOPED_MEMORY_VERSION,
    ):
        raise ProviderMissionPlanError(
            "provider MissionPlan adapter requires "
            f"{MISSION_PLAN_VERSION}, explicit {MISSION_PLAN_MEMORY_VERSION} "
            f"or {MISSION_PLAN_CAPACITY_VERSION, MISSION_PLAN_SCOPED_MEMORY_VERSION}"
        )
    return selected


def adapt_mission_plan_schema_for_provider(schema: JSONObject) -> JSONObject:
    """Replace the canonical dynamic parameter map with strict key/value entries.

    The returned schema is provider-facing only. The input schema is copied and
    remains the canonical MissionPlan contract used for final validation.
    """
    adapted = deepcopy(schema)
    _canonical_provider_version(adapted)

    definitions = _object(adapted.get("$defs"), "schema.$defs")
    role_schema = _object(definitions.get("role"), "schema.$defs.role")
    role_properties = _object(role_schema.get("properties"), "schema.$defs.role.properties")
    intent_schema = _object(
        role_properties.get("execution_intent"),
        "schema.$defs.role.properties.execution_intent",
    )
    intent_properties = _object(
        intent_schema.get("properties"),
        "schema.$defs.role.properties.execution_intent.properties",
    )
    parameter_schema = _object(
        intent_properties.get("parameters"),
        "schema.$defs.role.properties.execution_intent.properties.parameters",
    )
    if parameter_schema.get("type") != "object" or not isinstance(
        parameter_schema.get("additionalProperties"), dict
    ):
        raise ProviderMissionPlanError(
            "canonical execution_intent.parameters is not the expected scalar map"
        )

    intent_properties["parameters"] = {
        "type": "array",
        "description": (
            "Provider DTO entries for the canonical parameters map; keys must be unique."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "value"],
            "properties": {
                "key": {"type": "string"},
                "value": {
                    "anyOf": [
                        {"type": "boolean"},
                        {"type": "integer"},
                        {"type": "number"},
                        {"type": "string"},
                    ]
                },
            },
        },
    }
    return adapted


def build_mission_plan_provider_schema(
    schema: JSONObject,
    *,
    mission_id: str,
    profile: str = ECHO_OBJECTIVE_PROFILE,
    bind_mission_id: bool = True,
    compact_planner_output: bool = False,
) -> JSONObject:
    """Build a strict provider DTO fenced to one request's Mission ID.

    The canonical schema stays generic. The request-specific constraint is
    placed only on the copied provider projection, before any model call.
    """
    if profile not in PROVIDER_PLAN_PROFILES:
        raise ProviderMissionPlanError("unsupported provider plan profile")
    adapted = adapt_mission_plan_schema_for_provider(schema)
    projected = _project_strict_provider_schema(adapted)
    provider = _object(projected, "provider schema")
    if not mission_id:
        raise ProviderMissionPlanError("provider Mission identity must be nonblank")
    root_properties = _object(provider.get("properties"), "provider schema.properties")
    mission_schema = _object(root_properties.get("mission"), "provider schema.mission")
    mission_properties = _object(
        mission_schema.get("properties"), "provider schema.mission.properties"
    )
    mission_id_schema = _object(
        mission_properties.get("id"), "provider schema.mission.properties.id"
    )
    if mission_id_schema.get("$ref") != "#/$defs/id":
        raise ProviderMissionPlanError("provider Mission identity schema is unexpected")
    if bind_mission_id:
        mission_properties["id"] = {"type": "string", "enum": [mission_id]}
    if profile == HOST_OBJECTIVE_PROFILE:
        if mission_properties.get("objective") != {"type": "string"}:
            raise ProviderMissionPlanError("canonical Mission objective schema is unexpected")
        del mission_properties["objective"]
        mission_schema["required"] = [
            key
            for key in _array(mission_schema.get("required"), "mission.required")
            if key != "objective"
        ]
        root_properties["provider_profile"] = {"type": "string", "enum": [profile]}
        required = _array(provider.get("required"), "provider.required")
        provider["required"] = [*required, "provider_profile"]
    if compact_planner_output:
        _check_compact_planner_contract(schema, profile)
        _project_compact_planner_fields(provider)
    return provider


def _check_compact_planner_contract(schema: JSONObject, profile: str) -> None:
    """Limit the compact Planner wire format to the exact maintained contract."""
    if (
        _canonical_provider_version(schema) != MISSION_PLAN_CAPACITY_VERSION
        or profile != HOST_OBJECTIVE_PROFILE
    ):
        raise ProviderMissionPlanError(
            "compact Planner output requires v0.10 and host-objective-v1"
        )


def _project_compact_planner_fields(provider: JSONObject) -> None:
    """Replace repeated neutral fields with closed alternatives in the strict DTO."""
    definitions = _object(provider.get("$defs"), "schema.$defs")
    task = _object(definitions.get("task"), "schema.$defs.task")
    task_properties = _object(task.get("properties"), "schema.$defs.task.properties")
    task_properties["timing"] = {
        "description": "Use null only when the intent has no Task timing constraints. Otherwise "
        "emit all timing fields, including every requested time bound.",
        "anyOf": [task_properties["timing"], {"type": "null"}],
    }

    satisfaction = _object(definitions.get("satisfaction"), "schema.$defs.satisfaction")
    properties = _object(satisfaction.get("properties"), "satisfaction.properties")
    verifier = _object(properties.get("verifier"), "satisfaction.verifier")
    verifier_options = _array(verifier.get("anyOf"), "satisfaction.verifier.anyOf")
    verifier_policy = next(
        (
            option
            for option in verifier_options
            if isinstance(option, dict) and option.get("type") == "object"
        ),
        None,
    )
    if verifier_policy is None:
        raise ProviderMissionPlanError("unexpected schema for compact satisfaction.verifier")
    report_properties: JSONObject = {
        "expected_effect": properties["expected_effect"],
        "basis": {"enum": ["execution-report"]},
    }
    verified_properties: JSONObject = {
        "expected_effect": properties["expected_effect"],
        "basis": {"enum": ["verifier-evidence"]},
        "verifier": verifier_policy,
    }
    definitions["satisfaction"] = {
        "anyOf": [_closed_object(report_properties), _closed_object(verified_properties)]
    }

    role = _object(definitions.get("role"), "schema.$defs.role")
    role_properties = _object(role.get("properties"), "schema.$defs.role.properties")
    requirements = _object(role_properties.get("requirements"), "role.requirements")
    requirement_properties = _object(requirements.get("properties"), "requirements.properties")
    memories = _object(requirement_properties.get("memories"), "requirements.memories")
    memory_options = _array(memories.get("anyOf"), "requirements.memories.anyOf")
    memory_list = next(
        (
            option
            for option in memory_options
            if isinstance(option, dict) and option.get("type") == "array"
        ),
        None,
    )
    if memory_list is None:
        raise ProviderMissionPlanError("unexpected schema for compact requirements.memories")
    without_memories = {
        key: value for key, value in requirement_properties.items() if key != "memories"
    }
    role_properties["requirements"] = {
        "description": "Omit memories only if the intent declares no Memory prerequisites.",
        "anyOf": [
            _closed_object(without_memories),
            _closed_object({**without_memories, "memories": memory_list}),
        ],
    }


def _closed_object(properties: JSONObject) -> JSONObject:
    """Describe one strict provider object variant with an exact set of keys."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _expand_compact_planner_fields(value: JSONObject) -> None:
    """Restore neutral canonical values without inferring timing, evidence, or prerequisites."""
    for index, item in enumerate(_array(value.get("tasks"), "tasks")):
        path = f"tasks[{index}]"
        task = _object(item, path)
        if "timing" in task and task["timing"] is None:
            task["timing"] = {
                "earliest_start_offset_ms": 0,
                "latest_start_offset_ms": None,
                "completion_deadline_offset_ms": None,
            }
        satisfaction = _object(task.get("satisfaction"), f"{path}.satisfaction")
        if satisfaction.get("basis") == "execution-report":
            if set(satisfaction) != {"expected_effect", "basis"}:
                raise ProviderMissionPlanError(f"{path}.satisfaction must omit the verifier")
            satisfaction["verifier"] = None
        for role_index, item in enumerate(_array(task.get("roles"), f"{path}.roles")):
            role_path = f"{path}.roles[{role_index}]"
            role = _object(item, role_path)
            requirements = _object(role.get("requirements"), f"{role_path}.requirements")
            if "memories" in requirements and not isinstance(requirements["memories"], list):
                raise ProviderMissionPlanError(
                    f"{role_path}.requirements.memories must be an array"
                )


def normalize_mission_plan_provider_output(
    value: JSONObject,
    canonical_schema: JSONObject,
    *,
    profile: str = ECHO_OBJECTIVE_PROFILE,
    objective: str | None = None,
    compact_planner_output: bool = False,
) -> JSONObject:
    """Normalize provider null sentinels and parameter entries to canonical form.

    Provider output must target the current canonical contract; the v0.7 actor
    contract remains compatible only with the v0.8 DTO. Explicit v0.9 requires
    its exact version, preserving Memory prerequisites. It fails closed on other versions and
    malformed, duplicated, blank, or non-scalar entries. Canonical required-nullable
    values and optional properties whose canonical schema admits null are preserved.
    """
    if profile not in PROVIDER_PLAN_PROFILES:
        raise ProviderMissionPlanError("unsupported provider plan profile")
    normalized = deepcopy(value)
    selected = _canonical_provider_version(canonical_schema)
    accepted = (
        {MISSION_PLAN_ACTOR_VERSION, MISSION_PLAN_VERSION}
        if selected == MISSION_PLAN_VERSION and profile == ECHO_OBJECTIVE_PROFILE
        else {selected}
    )
    if normalized.get("schema_version") not in accepted:
        raise ProviderMissionPlanError(
            f"provider MissionPlan output must use {' or '.join(sorted(accepted))}"
        )

    if profile == HOST_OBJECTIVE_PROFILE:
        if normalized.get("provider_profile") != profile:
            raise ProviderMissionPlanError("provider plan profile is missing or mismatched")
        mission = _object(normalized.get("mission"), "mission")
        if "objective" in mission:
            raise ProviderMissionPlanError("host-owned mission.objective must be omitted")
        if not isinstance(objective, str) or not objective.strip():
            raise ProviderMissionPlanError("host-owned objective requires the frozen original")
        del normalized["provider_profile"]
        mission["objective"] = objective
    elif "provider_profile" in normalized:
        raise ProviderMissionPlanError("legacy provider output cannot carry provider_profile")

    if compact_planner_output:
        _check_compact_planner_contract(canonical_schema, profile)
        _expand_compact_planner_fields(normalized)
    _normalize_optional_null_sentinels(normalized, canonical_schema, canonical_schema)
    tasks = _array(normalized.get("tasks"), "tasks")
    for task_index, task_value in enumerate(tasks):
        task = _object(task_value, f"tasks[{task_index}]")
        roles = _array(task.get("roles"), f"tasks[{task_index}].roles")
        for role_index, role_value in enumerate(roles):
            path = f"tasks[{task_index}].roles[{role_index}].execution_intent.parameters"
            role = _object(role_value, f"tasks[{task_index}].roles[{role_index}]")
            intent = _object(
                role.get("execution_intent"),
                f"tasks[{task_index}].roles[{role_index}].execution_intent",
            )
            entries = _array(intent.get("parameters"), path)
            parameters: dict[str, str | int | float | bool] = {}
            for entry_index, entry_value in enumerate(entries):
                entry_path = f"{path}[{entry_index}]"
                entry = _object(entry_value, entry_path)
                if set(entry) != {"key", "value"}:
                    raise ProviderMissionPlanError(
                        f"{entry_path} must contain exactly key and value"
                    )
                key = entry["key"]
                if not isinstance(key, str) or not key.strip():
                    raise ProviderMissionPlanError(f"{entry_path}.key must be nonblank text")
                if key in parameters:
                    raise ProviderMissionPlanError(f"{path} contains duplicate key {key!r}")
                parameter = entry["value"]
                if parameter is None or isinstance(parameter, list | dict):
                    raise ProviderMissionPlanError(
                        f"{entry_path}.value must be a scalar string, number, or boolean"
                    )
                if isinstance(parameter, float) and not math.isfinite(parameter):
                    raise ProviderMissionPlanError(f"{entry_path}.value must be finite")
                parameters[key] = parameter
            intent["parameters"] = {key: parameters[key] for key in sorted(parameters)}
    return normalized


def _project_strict_provider_schema(value: JSONValue) -> JSONValue:
    """Project one schema node into the provider's closed structured-output subset."""
    if isinstance(value, list):
        return [_project_strict_provider_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    unsupported = {
        "$schema",
        "$id",
        "title",
        "minLength",
        "pattern",
        "uniqueItems",
        "allOf",
        "if",
        "then",
        "else",
    }
    projected: JSONObject = {}
    for key, item in value.items():
        if key in unsupported:
            continue
        if key == "const":
            projected["enum"] = [_project_strict_provider_schema(item)]
            continue
        projected[key] = _project_strict_provider_schema(item)
    properties = projected.get("properties")
    if projected.get("type") == "object" and isinstance(properties, dict):
        required_value = value.get("required", [])
        originally_required = (
            {item for item in required_value if isinstance(item, str)}
            if isinstance(required_value, list)
            else set()
        )
        for name, property_schema in list(properties.items()):
            if name not in originally_required:
                properties[name] = _nullable_schema(property_schema)
        projected["required"] = list(properties)
    return projected


def _nullable_schema(value: JSONValue) -> JSONValue:
    """Accept null for one provider-required representation of an optional property."""
    if isinstance(value, dict):
        type_value = value.get("type")
        if type_value == "null" or (isinstance(type_value, list) and "null" in type_value):
            return value
        alternatives = value.get("anyOf")
        if isinstance(alternatives, list) and any(
            isinstance(item, dict) and item.get("type") == "null" for item in alternatives
        ):
            return value
    return {"anyOf": [value, {"type": "null"}]}


def _normalize_optional_null_sentinels(
    value: JSONValue,
    schema_value: JSONValue,
    root_schema: JSONObject,
) -> None:
    """Remove provider null sentinels by following canonical schema structure."""
    if not isinstance(schema_value, dict):
        return
    schema = _resolve_schema(schema_value, root_schema)

    for keyword in ("anyOf", "oneOf", "allOf"):
        alternatives = schema.get(keyword)
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if _schema_matches_value(alternative, value, root_schema):
                    _normalize_optional_null_sentinels(value, alternative, root_schema)

    properties = schema.get("properties")
    if isinstance(value, dict) and isinstance(properties, dict):
        required_value = schema.get("required", [])
        required = (
            {item for item in required_value if isinstance(item, str)}
            if isinstance(required_value, list)
            else set()
        )
        for name, property_schema in properties.items():
            if name not in value:
                continue
            if (
                value[name] is None
                and name not in required
                and not _schema_allows_null(property_schema, root_schema)
            ):
                del value[name]
                continue
            _normalize_optional_null_sentinels(value[name], property_schema, root_schema)

    items = schema.get("items")
    if isinstance(value, list) and items is not None:
        for item in value:
            _normalize_optional_null_sentinels(item, items, root_schema)


def _resolve_schema(schema: JSONObject, root_schema: JSONObject) -> JSONObject:
    """Resolve a chain of local JSON Schema references or reject unsupported references."""
    resolved = schema
    seen: set[str] = set()
    while "$ref" in resolved:
        reference = resolved["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ProviderMissionPlanError("canonical schema uses an unsupported reference")
        if reference in seen:
            raise ProviderMissionPlanError(
                f"canonical schema contains a reference cycle: {reference}"
            )
        seen.add(reference)
        target: JSONValue = root_schema
        for encoded_part in reference[2:].split("/"):
            part = encoded_part.replace("~1", "/").replace("~0", "~")
            target = _object(target, f"canonical schema reference {reference}").get(part)
        resolved = _object(target, f"canonical schema reference {reference}")
    return resolved


def _schema_allows_null(schema_value: JSONValue, root_schema: JSONObject) -> bool:
    """Return whether the canonical schema explicitly admits a semantic null value."""
    if isinstance(schema_value, bool):
        return schema_value
    if not isinstance(schema_value, dict):
        return False
    schema = _resolve_schema(schema_value, root_schema)
    type_value = schema.get("type")
    if type_value == "null" or (isinstance(type_value, list) and "null" in type_value):
        return True
    if "const" in schema and schema["const"] is None:
        return True
    enum_value = schema.get("enum")
    if isinstance(enum_value, list) and None in enum_value:
        return True
    for keyword in ("anyOf", "oneOf"):
        alternatives = schema.get(keyword)
        if isinstance(alternatives, list) and any(
            _schema_allows_null(alternative, root_schema) for alternative in alternatives
        ):
            return True
    conjuncts = schema.get("allOf")
    if isinstance(conjuncts, list):
        return all(_schema_allows_null(conjunct, root_schema) for conjunct in conjuncts)
    return not any(
        keyword in schema
        for keyword in ("type", "const", "enum", "anyOf", "oneOf", "allOf", "$ref")
    )


def _schema_matches_value(
    schema_value: JSONValue, value: JSONValue, root_schema: JSONObject
) -> bool:
    """Match a value's JSON shape for selecting a canonical union branch to traverse."""
    if isinstance(schema_value, bool):
        return schema_value
    if not isinstance(schema_value, dict):
        return False
    schema = _resolve_schema(schema_value, root_schema)
    type_value = schema.get("type")
    types = (
        {type_value}
        if isinstance(type_value, str)
        else {item for item in type_value if isinstance(item, str)}
        if isinstance(type_value, list)
        else set()
    )
    if value is None:
        return _schema_allows_null(schema, root_schema)
    if isinstance(value, dict):
        return not types or "object" in types
    if isinstance(value, list):
        return not types or "array" in types
    if isinstance(value, bool):
        return not types or "boolean" in types
    if isinstance(value, int):
        return not types or bool(types & {"integer", "number"})
    if isinstance(value, float):
        return not types or "number" in types
    return not types or "string" in types


def _object(value: JSONValue | object, path: str) -> JSONObject:
    """Return a string-keyed object or reject a malformed provider boundary value."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ProviderMissionPlanError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: JSONValue | object, path: str) -> list[JSONValue]:
    """Return an array or reject a malformed provider boundary value."""
    if not isinstance(value, list):
        raise ProviderMissionPlanError(f"{path} must be an array")
    return cast(list[JSONValue], value)
