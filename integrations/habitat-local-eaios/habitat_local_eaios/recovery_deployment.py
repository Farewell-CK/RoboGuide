"""Freeze shared-world recovery configuration and verify its read-only deployment facts.

This startup tool runs with RoboGuide Python, before Node registration. It only
changes the recovery metadata in copied run-local Node configs. Simulator,
Mission, operation arguments, resources and capability facts remain outside it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tomllib
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .evidence_io import write_text_atomic
from .execution_recovery import execution_recovery_profile
from .model import RELOCATION_OPERATION, SUPPORTED_OPERATIONS, IntegrationError

SCHEMA = "roboguide.habitat-recovery-deployment/v0.1"
METADATA_KEY = "roboguide.execution-recovery"
SUPPORT_PATH = "/v1/executions/recovery-support"


def _owner(config: dict[str, Any]) -> dict[str, Any]:
    """Require one explicit LocalSystem owner for every supported canonical operation."""
    operations = config.get("operations", [])
    if not isinstance(operations, list) or any(not isinstance(item, dict) for item in operations):
        raise IntegrationError("recovery deployment operations must be an array of declarations")
    supported = set(SUPPORTED_OPERATIONS)
    if any(item.get("operation") == RELOCATION_OPERATION for item in operations):
        supported.add(RELOCATION_OPERATION)
    selected = [item for item in operations if item.get("operation") in supported]
    if len(selected) != len(supported) or {item.get("operation") for item in selected} != supported:
        raise IntegrationError("recovery deployment needs exact supported operation coverage")
    owners = {item.get("owner") for item in selected}
    systems = config.get("local_systems", [])
    if not isinstance(systems, list) or any(not isinstance(item, dict) for item in systems):
        raise IntegrationError("recovery deployment LocalSystems must be explicit declarations")
    systems = [item for item in systems if item.get("id") in owners]
    if (
        len(owners) != 1
        or any(not isinstance(owner, str) or not owner.strip() for owner in owners)
        or len(systems) != 1
    ):
        raise IntegrationError("recovery deployment needs one unambiguous operation owner")
    return dict(systems[0])


def render_node_config(text: str, retain_stopped_session: bool) -> str:
    """Derive only owner recovery metadata and reject ambiguous or unsupported source declarations.

    The maintained templates use one single-line TOML string for this metadata.
    A full parsed-object comparison fences textual replacement so it cannot
    accidentally change a different LocalSystem or any other configuration.
    """
    config = tomllib.loads(text)
    if config.get("schema") != "roboguide.node-config/v0.7":
        raise IntegrationError("recovery deployment requires Node config v0.7")
    owner = _owner(config)
    metadata = owner.get("metadata", {})
    if metadata.get("roboguide.execution-session") != "roboguide.execution-session/v0.1":
        raise IntegrationError("recovery deployment requires explicit session transport")
    try:
        current = json.loads(metadata[METADATA_KEY])
    except (KeyError, TypeError, ValueError) as failure:
        raise IntegrationError(
            "recovery deployment source declaration is missing or invalid"
        ) from failure
    relocation = any(item.get("operation") == RELOCATION_OPERATION for item in config["operations"])
    if relocation and retain_stopped_session:
        raise IntegrationError("relocation does not support retained cancellation continuation")
    defaults = execution_recovery_profile(shared_world=True, enable_relocation=relocation)
    retained = (
        execution_recovery_profile(shared_world=True, retain_stopped_session=True)
        if not relocation
        else defaults
    )
    if current not in (defaults, retained):
        raise IntegrationError("recovery deployment source declaration is unsupported")
    desired = retained if retain_stopped_session else defaults
    if current == desired:
        return text
    pattern = re.compile(r'^\s*"roboguide\.execution-recovery"\s*=.*$', re.MULTILINE)
    if len(pattern.findall(text)) != 1:
        raise IntegrationError(
            "recovery metadata must have one unambiguous single-line declaration"
        )
    encoded = json.dumps(json.dumps(desired, separators=(",", ":")))
    rendered = pattern.sub(lambda _: f'"{METADATA_KEY}" = {encoded}', text)
    expected = copy.deepcopy(config)
    expected_owner = _owner(expected)
    # _owner copies the outer mapping, while metadata remains the actual nested object.
    expected_owner["metadata"][METADATA_KEY] = json.dumps(desired, separators=(",", ":"))
    if tomllib.loads(rendered) != expected:
        raise IntegrationError("recovery metadata derivation changed unrelated configuration")
    return rendered


def _node_evidence(path: Path, text: str, enabled: bool) -> dict[str, Any]:
    """Bind exact Node bytes and its operation-owned loopback HTTP support route."""
    if render_node_config(text, enabled) != text:
        raise IntegrationError("Node recovery configuration differs from deployment mode")
    config = tomllib.loads(text)
    node_id = config.get("node_id")
    if not isinstance(node_id, str) or not node_id.strip():
        raise IntegrationError("recovery deployment requires a nonblank Node identity")
    owner = _owner(config)
    connections = [
        item
        for item in config.get("connections", [])
        if item.get("local_system") == owner["id"] and item.get("driver") == "http"
    ]
    if len(connections) != 1:
        raise IntegrationError("recovery support requires one fixed owner HTTP connection")
    endpoint = connections[0].get("endpoint")
    if not isinstance(endpoint, str):
        raise IntegrationError("recovery support endpoint is missing")
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost"}
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or parts.port is None
    ):
        raise IntegrationError("recovery support must use a fixed loopback endpoint")
    return {
        "node_id": node_id,
        "config_path": str(path.resolve()),
        "config_digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "local_system_id": owner["id"],
        "endpoint": endpoint.rstrip("/"),
        "profile": json.loads(owner["metadata"][METADATA_KEY]),
    }


def prepare_deployment(paths: list[Path], snapshot: Path, enabled: bool) -> None:
    """Validate all configs before writing them and one immutable startup snapshot."""
    if snapshot.exists() or not 1 <= len(paths) <= 32:
        raise IntegrationError("recovery deployment requires a fresh snapshot and bounded Node set")
    prepared = [
        (path, render_node_config(path.read_bytes().decode("utf-8"), enabled)) for path in paths
    ]
    nodes = [_node_evidence(path, text, enabled) for path, text in prepared]
    if len({node["node_id"] for node in nodes}) != len(nodes) or len(
        {node["config_path"] for node in nodes}
    ) != len(nodes):
        raise IntegrationError("recovery deployment Node identities and paths must be distinct")
    for path, text in prepared:
        write_text_atomic(path, text)
    write_text_atomic(
        snapshot,
        json.dumps(
            {"schema_version": SCHEMA, "retain_stopped_session": enabled, "nodes": nodes},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def verify_deployment(snapshot: Path) -> None:
    """Fence registration on changed source bytes or mismatched read-only HTTP evidence."""
    document = json.loads(snapshot.read_text())
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "retain_stopped_session", "nodes"}
        or document["schema_version"] != SCHEMA
        or not isinstance(document["retain_stopped_session"], bool)
        or not isinstance(document["nodes"], list)
        or not 1 <= len(document["nodes"]) <= 32
    ):
        raise IntegrationError("invalid recovery deployment snapshot")
    nodes = document["nodes"]
    if any(
        not isinstance(node, dict)
        or set(node)
        != {"node_id", "config_path", "config_digest", "local_system_id", "endpoint", "profile"}
        or not all(
            isinstance(node[key], str)
            for key in ("node_id", "config_path", "config_digest", "local_system_id", "endpoint")
        )
        for node in nodes
    ):
        raise IntegrationError("invalid recovery deployment Node evidence")
    if len({node["node_id"] for node in nodes}) != len(nodes):
        raise IntegrationError("duplicate recovery deployment Node identity")
    # Verify all source identities before any HTTP call; never regenerate a damaged snapshot.
    for node in nodes:
        path = Path(node["config_path"])
        if (
            _node_evidence(
                path, path.read_bytes().decode("utf-8"), document["retain_stopped_session"]
            )
            != node
        ):
            raise IntegrationError("recovery deployment source changed after startup freeze")
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for node in nodes:
        with client.open(node["endpoint"] + SUPPORT_PATH, timeout=2) as response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise IntegrationError("invalid or oversized recovery support response")
        if json.loads(raw) != node["profile"]:
            raise IntegrationError("Node recovery declaration differs from actual adapter support")


def main() -> None:
    """Prepare run-local registration or verify it before Controller/Node startup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", action="append", type=Path, default=[])
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--retain-stopped-session", action="store_true")
    parser.add_argument("--verify-live", action="store_true")
    args = parser.parse_args()
    if args.verify_live:
        verify_deployment(args.snapshot)
    else:
        prepare_deployment(args.node, args.snapshot, args.retain_stopped_session)


if __name__ == "__main__":
    main()
