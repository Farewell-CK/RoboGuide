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
    MISSION_PLAN_VERSION,
    JSONObject,
    JSONValue,
)


class ProviderMissionPlanError(ValueError):
    """Report malformed provider schemas or MissionPlan DTO output."""


def adapt_mission_plan_schema_for_provider(schema: JSONObject) -> JSONObject:
    """Replace the canonical dynamic parameter map with strict key/value entries.

    The returned schema is provider-facing only. The input schema is copied and
    remains the canonical MissionPlan contract used for final validation.
    """
    adapted = deepcopy(schema)
    root_properties = _object(adapted.get("properties"), "schema.properties")
    version_schema = _object(
        root_properties.get("schema_version"), "schema.properties.schema_version"
    )
    if version_schema.get("const") != MISSION_PLAN_VERSION:
        raise ProviderMissionPlanError(
            f"provider MissionPlan adapter requires {MISSION_PLAN_VERSION}"
        )

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


def build_mission_plan_provider_schema(schema: JSONObject) -> JSONObject:
    """Build the strict provider DTO schema from one canonical MissionPlan schema."""
    adapted = adapt_mission_plan_schema_for_provider(schema)
    projected = _project_strict_provider_schema(adapted)
    return _object(projected, "provider schema")


def normalize_mission_plan_provider_output(
    value: JSONObject, canonical_schema: JSONObject
) -> JSONObject:
    """Normalize provider null sentinels and parameter entries to canonical form.

    Provider output must target the current canonical contract; the v0.7 actor
    contract remains an accepted compatibility output because the DTO shape is
    identical without binding semantics. It fails closed on other versions and
    malformed, duplicated, blank, or non-scalar entries. Canonical required-nullable
    values and optional properties whose canonical schema admits null are preserved.
    """
    normalized = deepcopy(value)
    if normalized.get("schema_version") not in {
        MISSION_PLAN_ACTOR_VERSION,
        MISSION_PLAN_VERSION,
    }:
        raise ProviderMissionPlanError(
            "provider MissionPlan output must use "
            f"{MISSION_PLAN_ACTOR_VERSION} or {MISSION_PLAN_VERSION}"
        )

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
