"""Revisioned certificate grants with stable principals and durable anti-rollback authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mission.models import JSONObject
from mission.service_config import MissionServiceConfigError

AUTHORIZATION_SCHEMA = "roboguide.mission-authorization/v0.2"
LEGACY_AUTHORIZATION_SCHEMA = "roboguide.mission-authorization/v0.1"
ROLES = frozenset({"viewer", "submitter", "approver", "operator", "admin", "metrics-reader"})


@dataclass(frozen=True, slots=True)
class CertificateGrant:
    """Bind a verified leaf to a stable principal, one tenant and an explicit validity window."""

    principal_id: str
    role: str
    tenant_id: str
    not_before_ms: int = 0
    expires_at_ms: int = 0
    revoked: bool = False

    def active(self, now_ms: int) -> bool:
        """Check deployment validity without extending actual X.509 validity."""
        return (
            not self.revoked
            and now_ms >= self.not_before_ms
            and (self.expires_at_ms == 0 or now_ms < self.expires_at_ms)
        )


@dataclass(frozen=True, slots=True)
class AuthorizationProfile:
    """Contain the exact deployment-owned grant document and immutable certificate attribution."""

    profile_id: str | None
    revision: int
    grants: dict[str, CertificateGrant]
    document: JSONObject


def load_authorization(path: Path | None) -> AuthorizationProfile:
    """Read one bounded profile, accepting static mappings as an explicit compatibility input."""
    if path is None:
        return AuthorizationProfile(None, 0, {}, {})
    if path.stat().st_size > 2 * 1024 * 1024:
        raise MissionServiceConfigError("authorization file exceeds its byte limit")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or not value:
        raise MissionServiceConfigError("authorization must be a non-empty object")
    profile_id: str | None = None
    revision = 0
    if "schema_version" in value:
        if set(value) != {"schema_version", "profile_id", "revision", "grants"} or value[
            "schema_version"
        ] not in {AUTHORIZATION_SCHEMA, LEGACY_AUTHORIZATION_SCHEMA}:
            raise MissionServiceConfigError("unsupported authorization profile")
        profile_id = _text(value["profile_id"], "profile_id")
        revision = _integer(value["revision"], "revision", positive=True)
        raw_grants = value["grants"]
    else:
        raw_grants = value
    if not isinstance(raw_grants, dict) or not 1 <= len(raw_grants) <= 4096:
        raise MissionServiceConfigError("authorization grants must be bounded and non-empty")
    grants: dict[str, CertificateGrant] = {}
    for digest, raw in raw_grants.items():
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise MissionServiceConfigError("authorization certificate digest is invalid")
        if profile_id is None:
            if isinstance(raw, str):
                raw = {"role": raw, "tenant_id": "default"}
            if not isinstance(raw, dict) or set(raw) != {"role", "tenant_id"}:
                raise MissionServiceConfigError("static certificate grant is invalid")
            raw = {**raw, "principal_id": digest}
        if (
            not isinstance(raw, dict)
            or not {"principal_id", "role", "tenant_id"} <= set(raw)
            or set(raw)
            - {"principal_id", "role", "tenant_id", "not_before_ms", "expires_at_ms", "revoked"}
        ):
            raise MissionServiceConfigError("certificate grant contains invalid fields")
        principal = _text(raw["principal_id"], "principal_id")
        tenant = _text(raw["tenant_id"], "tenant_id")
        role = raw["role"]
        if (
            not isinstance(role, str)
            or role not in ROLES
            or tenant == "*"
            and role not in {"admin", "metrics-reader"}
            or role == "metrics-reader"
            and (tenant != "*" or value.get("schema_version") != AUTHORIZATION_SCHEMA)
        ):
            raise MissionServiceConfigError("certificate grant role is invalid")
        not_before = _integer(raw.get("not_before_ms", 0), "not_before_ms")
        expires = _integer(raw.get("expires_at_ms", 0), "expires_at_ms")
        revoked = raw.get("revoked", False)
        if not isinstance(revoked, bool) or expires and expires <= not_before:
            raise MissionServiceConfigError("certificate grant validity is invalid")
        grants[digest] = CertificateGrant(principal, role, tenant, not_before, expires, revoked)
    tenants: dict[str, str] = {}
    for grant in grants.values():
        if tenants.setdefault(grant.principal_id, grant.tenant_id) != grant.tenant_id:
            raise MissionServiceConfigError("one stable principal cannot change tenants")
    return AuthorizationProfile(profile_id, revision, grants, cast(JSONObject, value))


def _text(value: object, name: str) -> str:
    """Validate a bounded authority identity rather than deriving it from client headers."""
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise MissionServiceConfigError(f"authorization {name} is invalid")
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    """Reject Boolean timestamps, negative revisions and unbounded SQL integers."""
    if type(value) is not int or not int(positive) <= value <= 2**63 - 1:
        raise MissionServiceConfigError(f"authorization {name} is invalid")
    return value
