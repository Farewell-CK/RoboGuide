"""Verify scoped typed ancestry and exact retained preparation under the original bundle locks."""

from __future__ import annotations

import sqlite3
from typing import Any

from .capacity_recovery import _checkpoint

WRAPPER = "roboguide.controller-checkpoint/v57"
PREPARATION = "roboguide.memory-preparation/v0.3"
AVAILABILITY = "roboguide.memory-provider-availability/v0.2"


def _revisions(wrapper: dict[str, Any]) -> dict[str, Any]:
    """Read serde's omitted-empty revision history without accepting malformed present authority."""
    result = wrapper.get("runtime_revisions", {})
    if not isinstance(result, dict):
        raise ValueError("scoped recovery runtime revision history must be an object")
    return result


def _projection(typed: dict[str, Any]) -> dict[str, Any]:
    """Reproduce immutable global metadata while retaining the complete typed root."""
    if typed.get("schema") not in {
        "roboguide.map-manifest/v0.1",
        "roboguide.map-manifest/v0.2",
    } or not typed.get("producer_local_system_id"):
        raise ValueError("scoped recovery lacks the original typed production owner")
    result = {
        "schema": "roboguide.memory-manifest/v0.1",
        "selector": {"memory_id": typed["map_id"], "revision_id": typed["revision_id"]},
        "kind": "spatial",
        "provider_id": typed.get("producer_provider_id", "typed-map-catalog"),
        "owner": {
            "owner": "node",
            "node_id": typed["producer_node_id"],
            "local_system_id": typed["producer_local_system_id"],
        },
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": typed.get("payload_schema", "roboguide.map-manifest/v0.1"),
        "media_type": typed["media_type"],
        "artifact": {"content_digest": typed["content_digest"], "byte_size": typed["byte_size"]},
        "source_mission_id": typed["source_mission_id"],
        "created_at": typed["created_at_ms"],
    }
    for key in ("source_execution_id", "source_task_ref"):
        result[key] = typed.get(key)
    return result


def _key(manifest: dict[str, Any]) -> tuple[str, str]:
    """Use exact selector components, independently of filesystem paths and display labels."""
    selector = manifest["selector"]
    if (
        not isinstance(selector, dict)
        or set(selector) != {"memory_id", "revision_id"}
        or any(not isinstance(value, str) or not value for value in selector.values())
    ):
        raise ValueError("scoped recovery selector is not complete")
    return selector["memory_id"], selector["revision_id"]


def _alias(typed: dict[str, Any], child: dict[str, Any], group: str) -> None:
    """Refuse altered owner, bytes, source, contracts or original root behind a scoped child."""
    root = _projection(typed)
    if (
        child.get("scope") != {"kind": "execution_group", "execution_group_id": group}
        or _key(child) == _key(root)
        or type(child.get("created_at")) is not int
        or child["created_at"] < root["created_at"]
    ):
        raise ValueError("scoped recovery changes original selector, scope or time")
    if (child | {key: root[key] for key in ("selector", "scope", "created_at")}) != root:
        raise ValueError("scoped recovery changed original typed production provenance")


def _source(
    preparations: list[dict[str, Any]],
    request: dict[str, Any],
    parent: dict[str, Any],
    group: str,
    ids: list[str] | None,
) -> None:
    """Require original source preparation without asserting that retired use is live."""
    typed = request["typed_map"]
    scoped = parent != _projection(typed)
    if not any(
        preparation["group_id"] == group
        and (ids is None or preparation["execution_id"] in ids)
        and any(
            item["manifest"] == parent
            and (item.get("typed_map") == typed or scoped and item.get("typed_map") is None)
            for item in preparation["inputs"]
        )
        for preparation in preparations
    ):
        raise ValueError("scoped transfer lacks its exact original source preparation")


def validate(connection: sqlite3.Connection) -> None:
    """Validate every retained publication edge and new prepared input before backup or verify.

    Existing Catalog/Memory scans independently verify all currently required actual CAS bytes.
    This check preserves released history and never demands content that was legitimately deleted.
    """
    wrapper = _checkpoint(connection)
    if wrapper is None or wrapper["schema"] not in {
        WRAPPER,
        "roboguide.controller-checkpoint/v58",
        "roboguide.controller-checkpoint/v59",
        "roboguide.controller-checkpoint/v60",
    }:
        return
    preparations = [
        record["preparation"]
        for record in wrapper["memory_preparation"]["records"].values()
        if record.get("preparation") is not None
    ]
    edges: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for record in wrapper["memory_handoffs"].values():
        request, parent = record["request"], record["parent"]
        typed = request.get("typed_map")
        if request["schema_version"] == "roboguide.memory-handoff/v0.3":
            if not isinstance(typed, dict):
                raise ValueError("scoped handoff lost its complete typed root")
            _source(
                preparations,
                request,
                parent,
                request["source_group_id"],
                request["source_execution_ids"],
            )
        elif typed is not None and (
            request["schema_version"] != "roboguide.memory-handoff/v0.2"
            or parent != _projection(typed)
        ):
            raise ValueError("legacy handoff cannot hide scoped-parent typed authority")
        if typed is not None and record["published_at_ms"] is not None:
            key = _key(request["child"])
            if key in edges:
                raise ValueError("scoped typed publication has ambiguous original owners")
            edges[key] = request["child"], parent, typed
    for state in _revisions(wrapper).values():
        applied = {
            switch["ticket_id"] for switch in state["switches"] if switch["phase"] == "applied"
        }
        for ticket, candidate in state["candidates"].items():
            for record in candidate.get("memory", {}).values():
                request, parent = record["request"], record["parent"]
                typed = request.get("typed_map")
                if typed is not None and parent.get("scope", {}).get("kind") == "execution_group":
                    _alias(typed, parent, record["source_group"])
                    _source(preparations, request, parent, record["source_group"], None)
                if typed is not None and ticket in applied:
                    key = _key(request["child"])
                    if key in edges:
                        raise ValueError("scoped typed publication has ambiguous original owners")
                    edges[key] = request["child"], parent, typed
    for child, parent, typed in edges.values():
        _alias(typed, child, child["scope"]["execution_group_id"])
        if parent["created_at"] > child["created_at"]:
            raise ValueError("scoped publication regresses original creation time")
        seen = {_key(child)}
        while parent != _projection(typed):
            key = _key(parent)
            if key in seen or key not in edges:
                raise ValueError(
                    "scoped typed ancestry is cyclic or lacks its original publication"
                )
            seen.add(key)
            original, next_parent, root = edges[key]
            if original != parent or root != typed:
                raise ValueError("scoped typed ancestry changed its immutable original root")
            parent = next_parent
    for preparation in preparations:
        scoped_count = 0
        for item in preparation["inputs"]:
            typed, child = item.get("typed_map"), item["manifest"]
            if typed is not None and child != _projection(typed):
                _alias(typed, child, preparation["group_id"])
                if (
                    edges.get(_key(child)) is None
                    or edges[_key(child)][0] != child
                    or edges[_key(child)][2] != typed
                    or item["requirement"].get("source_contract") != "scoped_typed_map"
                ):
                    raise ValueError(
                        "scoped prepared input lacks its exact published typed ancestry"
                    )
                scoped_count += 1
            if (
                preparation["schema"] == PREPARATION
                and item["consumer"].get("preparation_contract") != AVAILABILITY
            ):
                raise ValueError("scoped preparation lacks its negotiated actual provider contract")
        if (scoped_count > 0) != (preparation["schema"] == PREPARATION):
            raise ValueError("scoped typed meaning is hidden in an incompatible preparation schema")


def validate_catalog(
    connection: sqlite3.Connection, publications: dict[tuple[str, str], tuple[str, dict[str, Any]]]
) -> None:
    """Bind typed roots to original hot/archived publication without re-encoding floats."""
    wrapper = _checkpoint(connection)
    if wrapper is None or wrapper["schema"] not in {
        WRAPPER,
        "roboguide.controller-checkpoint/v58",
        "roboguide.controller-checkpoint/v59",
        "roboguide.controller-checkpoint/v60",
    }:
        return
    roots = [record["request"].get("typed_map") for record in wrapper["memory_handoffs"].values()]
    roots += [
        record["request"].get("typed_map")
        for state in _revisions(wrapper).values()
        for candidate in state["candidates"].values()
        for record in candidate.get("memory", {}).values()
    ]
    roots += [
        item.get("typed_map")
        for record in wrapper["memory_preparation"]["records"].values()
        if record.get("preparation") is not None
        for item in record["preparation"]["inputs"]
    ]
    for typed in roots:
        if typed is not None and publications.get((typed["map_id"], typed["revision_id"])) != (
            "MapArtifactPublished",
            typed,
        ):
            raise ValueError(
                "scoped typed root differs from its original complete Catalog publication"
            )
