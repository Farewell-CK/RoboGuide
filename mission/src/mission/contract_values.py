"""Shared JSON vocabulary and validation primitives for Mission contracts."""

from __future__ import annotations

import re
from typing import Final

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

MISSION_PLAN_VERSION: Final = "roboguide.mission-plan/v0.7"
MISSION_PLAN_COMPAT_VERSION: Final = "roboguide.mission-plan/v0.3"
MISSION_PLAN_COUPLING_VERSION: Final = "roboguide.mission-plan/v0.4"
MISSION_PLAN_SCHEDULING_VERSION: Final = "roboguide.mission-plan/v0.5"
MISSION_PLAN_SATISFACTION_VERSION: Final = "roboguide.mission-plan/v0.6"
CAPABILITIES: Final = frozenset({"mobility", "transport", "compute", "observation"})
RESOURCE_KINDS: Final = frozenset({"space", "compute", "time"})
U32_MAX: Final = (1 << 32) - 1
U64_MAX: Final = (1 << 64) - 1
RELATION_KINDS: Final = frozenset(
    {
        "requires-active",
        "group-member-state",
        "shared-spatial-reference",
        "relative-pose",
        "relative-distance",
        "state-requirement",
        "freshness-requirement",
    }
)
# This is an implementation profile, not the MissionPlan schema vocabulary.
EXECUTABLE_RELATION_KINDS: Final = frozenset({"requires-active", "shared-spatial-reference"})
COUPLING_MODES: Final = frozenset(
    {
        "independent",
        "sequential-handoff",
        "concurrent-cooperation",
        "tightly-coupled-cooperation",
    }
)
GROUP_VIEW_FIELDS: Final = frozenset({"pose", "velocity", "execution"})
MAP_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class MissionPlanError(ValueError):
    """Report a Mission Plan contract or graph invariant violation."""


def _object(value: JSONValue, path: str) -> JSONObject:
    """Return a JSON object or reject the value with its contract path."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionPlanError(f"{path} must be an object")
    return value


def _array(value: JSONValue, path: str) -> list[JSONValue]:
    """Return a JSON array or reject the value with its contract path."""
    if not isinstance(value, list):
        raise MissionPlanError(f"{path} must be an array")
    return value


def _text(value: JSONValue, path: str) -> str:
    """Return nonblank text or reject the value with its contract path."""
    if not isinstance(value, str) or not value.strip():
        raise MissionPlanError(f"{path} must be nonblank text")
    return value


def _exact_keys(value: JSONObject, required: set[str], path: str) -> None:
    """Reject missing or unknown keys so contract drift is explicit."""
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        raise MissionPlanError(f"{path} keys mismatch: missing={missing}, unknown={unknown}")


def _bounded_keys(value: JSONObject, required: set[str], optional: set[str], path: str) -> None:
    """Reject missing required keys and keys outside the versioned optional set."""
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        raise MissionPlanError(f"{path} keys mismatch: missing={missing}, unknown={unknown}")
