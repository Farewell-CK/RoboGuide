"""Independently check original typed projections without manufacturing map or use authority."""

from __future__ import annotations

from typing import Any


def validate_typed_input(
    typed: object, manifest: dict[str, Any], group: object, scoped_enabled: bool
) -> bool:
    """Require an exact original projection or explicitly negotiated same-root scoped alias.

    Returns whether this is a scoped child. The original typed manifest itself remains complete
    in the durable import and use preparation; projection equality never establishes localization.
    """
    if not isinstance(typed, dict) or typed.get("schema") not in {
        "roboguide.map-manifest/v0.1",
        "roboguide.map-manifest/v0.2",
    }:
        raise ValueError("complete original typed map is unavailable")
    if not typed.get("producer_local_system_id"):
        raise ValueError("original typed producer owner is unavailable")
    projection = {
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
        projection[key] = typed.get(key)
    if manifest == projection:
        return False
    if not scoped_enabled or not isinstance(group, str) or not group:
        raise ValueError("scoped typed input requires its original negotiated Group context")
    if (
        manifest.get("scope") != {"kind": "execution_group", "execution_group_id": group}
        or manifest.get("selector") == projection["selector"]
        or type(manifest.get("created_at")) is not int
        or manifest["created_at"] < projection["created_at"]
        or (manifest | {key: projection[key] for key in ("selector", "scope", "created_at")})
        != projection
    ):
        raise ValueError(
            "scoped typed input changes original root, owner, bytes, contracts or provenance"
        )
    return True
