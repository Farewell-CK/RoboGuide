"""Freeze declared acquisition syntax without querying live Node inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model import COMPOUND_OBSERVATION_FORMAT, IntegrationError

SCHEMA = "roboguide.observation-condition-declaration/v0.1"
LEGACY_FORMAT = "detected(entity-ref)"


def build_condition_declaration(sources: tuple[tuple[int, Path], ...]) -> dict[str, Any]:
    """Read exact startup Node bytes; missing acquisition grants no condition support."""
    import tomllib

    records = []
    for agent, path in sources:
        raw = path.read_bytes()
        node = tomllib.loads(raw.decode())
        profiles = [
            item
            for item in node.get("capability_profiles", [])
            if item.get("contract") == "observation.acquire@v1"
        ]
        if len(profiles) > 1:
            raise IntegrationError("acquisition condition profile is ambiguous")
        condition_format = (
            profiles[0].get("attributes", {}).get("condition-format") if profiles else None
        )
        if profiles and (
            not isinstance(condition_format, str)
            or condition_format not in {LEGACY_FORMAT, COMPOUND_OBSERVATION_FORMAT}
        ):
            raise IntegrationError("acquisition condition format is unsupported")
        records.append(
            {
                "agent_id": agent,
                "node_config_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "condition_format": condition_format,
            }
        )
    return {"schema_version": SCHEMA, "agents": records}


def read_condition_declaration(path: Path, registry: dict[str, Any]) -> dict[int, str | None]:
    """Validate finite exact source coverage under the real Python 3.9 worker."""
    with path.open("rb") as source:
        raw = source.read(65_537)
    if len(raw) > 65_536:
        raise IntegrationError("observation condition declaration exceeds byte budget")
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "agents"}
        or document["schema_version"] != SCHEMA
        or not isinstance(document["agents"], list)
        or len(document["agents"]) != len(registry["endpoints"])
    ):
        raise IntegrationError("observation condition declaration coverage is invalid")
    result: dict[int, str | None] = {}
    for record, endpoint in zip(document["agents"], registry["endpoints"]):  # noqa: B905 - Python 3.9; exact lengths checked above
        if (
            not isinstance(record, dict)
            or set(record) != {"agent_id", "node_config_digest", "condition_format"}
            or type(record["agent_id"]) is not int
            or record["agent_id"] != endpoint["agent_id"]
            or record["node_config_digest"] != endpoint["node_config_digest"]
        ):
            raise IntegrationError("observation condition declaration differs from Node sources")
        value = record["condition_format"]
        if (
            (value is not None and not isinstance(value, str))
            or value not in {None, LEGACY_FORMAT, COMPOUND_OBSERVATION_FORMAT}
            or (value is not None) != ("observation.acquire@v1" in endpoint["operations"])
        ):
            raise IntegrationError("observation condition support differs from declared operations")
        result[record["agent_id"]] = value
    return result
