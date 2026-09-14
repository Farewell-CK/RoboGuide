"""Provider-only MissionPlan DTO adaptation for strict structured output."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

from mission.contract_values import MISSION_PLAN_VERSION, JSONObject, JSONValue


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


def normalize_mission_plan_provider_output(value: JSONObject) -> JSONObject:
    """Normalize provider parameter entries back to the canonical v0.7 map.

    Provider output must target the current canonical contract. It fails closed
    on other versions and malformed, duplicated, blank, or non-scalar entries.
    """
    normalized = deepcopy(value)
    if normalized.get("schema_version") != MISSION_PLAN_VERSION:
        raise ProviderMissionPlanError(
            f"provider MissionPlan output must use {MISSION_PLAN_VERSION}"
        )

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
