"""Project positive reset witnesses to neutral, optional initial search costs.

This data conversion neither accepts a MissionPlan nor selects an executor.
The B1 runner validates the original archive before invoking this projection.
Null cost preserves unknown/missing witnesses as eligible scheduling fallbacks.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .preassignment_feasibility import preassignment_digest

SCHEMA = "roboguide.deployment-initial-operation-preferences/v0.1"
MAX_RECORDS = 128
MAX_COST_MICROMETERS = 1_000_000_000_000


def _sealed(document: dict[str, Any], schema: str) -> None:
    """Reject wrong versions or content identities before projecting evidence."""
    body = {key: value for key, value in document.items() if key != "digest"}
    if document.get("schema_version") != schema or document.get("digest") != preassignment_digest(
        body
    ):
        raise ValueError("initial preferences source schema or digest is invalid")


def build_initial_operation_preferences(
    routes: dict[str, Any], feasibility: dict[str, Any]
) -> dict[str, Any]:
    """Preserve exact source coverage while projecting only positive static costs.

    Invalid identity, duplicates or incomplete available coverage fail closed.
    An honest unavailable scope yields neutral costs for every covered intent.
    The source remains a consistency-checked deployment observation, not proof
    that execution will succeed or that null-cost candidates are impossible.
    """
    _sealed(routes, "roboguide.deployment-reset-route-support/v0.1")
    _sealed(feasibility, "roboguide.deployment-intent-feasibility/v0.3")
    identity = routes["identity"]
    if (
        any(identity.get(key) != value for key, value in feasibility["identity"].items())
        or identity.get("preassignment_digest") != feasibility["digest"]
        or routes.get("purpose") != "diagnostic_only"
        or routes.get("initial_agent_positions") != feasibility.get("initial_agent_positions")
        or routes.get("scope_status") not in {"available", "unavailable"}
    ):
        raise ValueError("initial preferences sources refer to different reset worlds")
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for record in routes["records"]:
        route_key = (record["node_id"], record["destination"])
        if route_key in observed or record["status"] not in {
            "supported",
            "not_found",
            "unavailable",
        }:
            raise ValueError("initial preferences route coverage or status is invalid")
        observed[route_key] = record
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in feasibility["records"]:
        intent_key = (record["operation"], record["destination"], record["node_id"])
        if intent_key in entries:
            raise ValueError("initial preferences feasibility records are duplicated")
        witness = observed.get((record["node_id"], record["destination"]))
        if witness is not None and witness["agent_id"] != record["agent_id"]:
            raise ValueError("initial preferences endpoint identity differs")
        cost: int | None = None
        if witness is not None and witness["status"] == "supported":
            length = witness["selection"]["path_length_m"]
            if (
                isinstance(length, bool)
                or not isinstance(length, (int, float))
                or not math.isfinite(length)
                or not 0 <= length <= MAX_COST_MICROMETERS / 1_000_000
            ):
                raise ValueError("initial preferences witness cost is invalid")
            cost = round(length * 1_000_000)
        entries[intent_key] = {
            "operation": record["operation"],
            "parameters": {"destination": record["destination"]},
            "node_id": record["node_id"],
            "cost_micrometers": cost,
        }
    expected = {(key[2], key[1]) for key in entries}
    if (
        not 0 < len(entries) <= MAX_RECORDS
        or not set(observed) <= expected
        or (routes["scope_status"] == "available" and set(observed) != expected)
        or (routes["scope_status"] == "unavailable" and observed)
    ):
        raise ValueError("initial preferences source coverage is incomplete or exceeds budget")
    body = {
        "schema_version": SCHEMA,
        "authority": "deployment-observed-reset-state",
        "scope": "initial_world_before_first_dispatch",
        "source_digest": routes["digest"],
        "feasibility_digest": feasibility["digest"],
        "records": [entries[key] for key in sorted(entries)],
    }
    return {**body, "digest": preassignment_digest(body)}


def _load(path: Path) -> dict[str, Any]:
    """Read a bounded JSON source without importing Habitat or executing it."""
    with path.open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("initial preferences source is empty or exceeds 1 MiB")
    value: Any = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("initial preferences source must be an object")
    return value


def main() -> None:
    """Write a new run-local projection; never overwrite archived evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-support", type=Path, required=True)
    parser.add_argument("--feasibility", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    document = build_initial_operation_preferences(
        _load(arguments.route_support), _load(arguments.feasibility)
    )
    with arguments.output.open("x", encoding="utf-8") as target:
        json.dump(document, target, sort_keys=True, allow_nan=False, indent=2)
        target.write("\n")


if __name__ == "__main__":
    main()
