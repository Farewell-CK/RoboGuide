"""Validate current legacy-Catalog adoption against exact original persisted envelopes."""

from __future__ import annotations

import json
import re
from typing import Any

from .snapshot_references import canonical, digest

SCHEMA = "roboguide.recovery-profile/v0.15"
SUPPORTED = {
    SCHEMA,
    "roboguide.recovery-profile/v0.16",
    "roboguide.recovery-profile/v0.17",
    "roboguide.recovery-profile/v0.18",
    "roboguide.recovery-profile/v0.19",
    "roboguide.recovery-profile/v0.20",
}
WRAPPER = "roboguide.controller-checkpoint/v51"
WRAPPERS = {WRAPPER, "roboguide.controller-checkpoint/v52", "roboguide.controller-checkpoint/v53"}
CODEC = "domain.EventPayload.json/v30"


def validate(receipt: Any, original: dict[str, Any], reference: dict[str, Any]) -> None:
    """Preserve unknown historical attribution and reject any replacement original publication."""
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "schema",
            "command_id",
            "publication",
            "authority",
            "historical_publisher",
            "historical_tenant",
            "record",
        }
        or receipt["schema"] != "roboguide.artifact-catalog-adoption/v0.1"
        or not isinstance(receipt["command_id"], str)
        or not 0 < len(receipt["command_id"]) <= 128
        or "*" in receipt["command_id"]
        or any(ord(c) < 32 for c in receipt["command_id"])
        or receipt["publication"] != original
        or receipt["record"] != reference
        or receipt["historical_publisher"] is not None
        or receipt["historical_tenant"] is not None
        or reference["retirement"] is not None
        or reference["admitted_at_ms"] < original["timestamp_ms"]
    ):
        raise ValueError("original legacy Catalog adoption input/history changed")
    authority = receipt["authority"]
    if (
        not isinstance(authority, dict)
        or set(authority)
        != {"principal_id", "tenant_id", "certificate_digest", "canonical_grant_json"}
        or authority["principal_id"] != reference["principal_id"]
        or authority["tenant_id"] != reference["tenant_id"]
        or not isinstance(authority["canonical_grant_json"], str)
        or len(authority["canonical_grant_json"].encode()) > 65536
        or reference["authority_digest"]
        != digest(canonical([receipt["command_id"], original, authority]))
    ):
        raise ValueError("legacy adoption lost current original administrative evidence")
    grant = json.loads(authority["canonical_grant_json"])
    if (
        not isinstance(grant, dict)
        or canonical(grant).decode() != authority["canonical_grant_json"]
    ):
        raise ValueError("legacy adoption current original grant evidence is not canonical")
    leaf = authority["certificate_digest"]
    if leaf is not None and (
        not isinstance(leaf, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", leaf) is None
    ):
        raise ValueError("legacy adoption actual current leaf is invalid")
