"""Check full typed-map content policy against owned real mutually authenticated services."""

from __future__ import annotations

import copy
import hashlib
import json
import ssl
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import call


def digest(value: dict[str, Any]) -> str:
    """Hash sorted JSON metadata; the real API verifies compatibility with its Rust encoding."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def check(
    exchange: Callable[[str, str, Any], tuple[int, Any]],
    origin: str,
    cas: str,
    contexts: dict[str, ssl.SSLContext],
    manifest: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    """Narrow original policy, reject digest changes and check current TLS readers."""
    path = f"/v1/maps/{manifest['map_id']}/revisions/{manifest['revision_id']}"
    generic_path = f"/v1/memories/{manifest['map_id']}/revisions/{manifest['revision_id']}"
    status, response = exchange(cas, generic_path, None)
    assert status == 200, response
    projection = response["manifest"]
    projection.pop("typed_extension")
    projection.pop("status")
    projection["schema"] = "roboguide.memory-manifest/v0.1"
    key = manifest["map_id"] + "/" + manifest["revision_id"]
    policy: dict[str, Any] = {
        "policy_id": "source-map-" + uuid.uuid4().hex,
        "selector": projection["selector"],
        "manifest_digest": digest(projection),
        "tenant_id": "local",
        "principals": {"independent-file-reader": ["discover", "read"]},
        "groups": [],
        "revoked": False,
    }
    registry: dict[str, Any] = {
        "schema_version": "roboguide.memory-authorization/v0.1",
        "registry_id": "complete-map-" + uuid.uuid4().hex,
        "revision": 1,
        "policies": {key: policy},
    }
    receipts = []
    status, response = exchange(
        origin,
        "/v1/memory-authorizations",
        {"command_id": uuid.uuid4().hex, "expected_revision": 0, "registry": registry},
    )
    assert status == 200, response
    receipts.append({"case": "explicit-legacy-policy", "status": status, "receipt": response})
    status, response = exchange(cas, path, None)
    assert status == 403, (status, response)
    receipts.append({"case": "legacy-projection-cannot-read-map", "status": status})
    registry["schema_version"] = "roboguide.memory-authorization/v0.2"
    registry["revision"] = 2
    policy["typed_map_manifest_digest"] = digest(manifest)
    status, response = exchange(
        origin,
        "/v1/memory-authorizations",
        {"command_id": uuid.uuid4().hex, "expected_revision": 1, "registry": registry},
    )
    assert status == 200, response
    receipts.append({"case": "explicit-full-map-upgrade", "status": status, "receipt": response})
    status, response = exchange(cas, path, None)
    assert status == 200 and response["manifest"] == manifest, (status, response)
    status, response = call(cas, contexts["enrolment-first"], "GET", path)
    assert status == 403, (status, response)
    receipts.append({"case": "uploader-has-no-read-grant", "status": status})
    for case in ("changed-frame-digest", "removed-full-digest", "schema-downgrade"):
        proposed = copy.deepcopy(registry)
        proposed["revision"] = 3
        if case == "changed-frame-digest":
            changed = copy.deepcopy(manifest)
            changed["root_frame"] += "-changed"
            proposed["policies"][key]["typed_map_manifest_digest"] = digest(changed)
        else:
            proposed["policies"][key].pop("typed_map_manifest_digest")
            if case == "schema-downgrade":
                proposed["schema_version"] = "roboguide.memory-authorization/v0.1"
        status, response = exchange(
            origin,
            "/v1/memory-authorizations",
            {"command_id": uuid.uuid4().hex, "expected_revision": 2, "registry": proposed},
        )
        assert status == 409, (case, status, response)
        receipts.append({"case": case, "status": status, "receipt": response})
    status, response = exchange(origin, "/v1/memory-authorizations", None)
    assert status == 200 and response["registry"] == registry, response
    (output / "memory-authorization-v0.2.json").write_text(json.dumps(registry, indent=2) + "\n")
    result = {"passed": True, "receipts": receipts, "projection": projection, "policy": policy}
    (output / "full-map-policy.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
