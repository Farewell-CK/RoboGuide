"""Enroll one independently provisioned manual verifier without actions or evidence publication.

Run on the Controller host. An interrupted run keeps its original profile snapshots,
command and receipt; rerun the identical arguments and output directory to recover.
There are no redirects, proxies, automatic retries, certificate signing or robot calls.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Protocol

MAX_BYTES = 2 * 1024 * 1024
MAX_U64 = 18446744073709551615
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROFILE_SCHEMA = "roboguide.operator-authorization/v0.3"
_REGISTRY_SCHEMA = "roboguide.semantic-verifier-registry/v0.1"


class EnrollmentError(ValueError):
    """Preserve partial enrollment evidence and refuse an ambiguous or unsafe continuation."""


def encode(value: Any) -> bytes:
    """Encode finite canonical JSON for durable comparisons and typed command digests."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    """Return the exact bytes' qualified SHA256 without disclosing credential content."""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate members instead of silently rewriting authority inputs."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EnrollmentError("duplicate JSON member")
        result[key] = value
    return result


def decode(raw: bytes) -> dict[str, Any]:
    """Decode one bounded finite object and reject unknown JSON framing failures."""
    if len(raw) > MAX_BYTES:
        raise EnrollmentError("document exceeds 2 MiB")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise EnrollmentError("JSON object required")
        encode(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise EnrollmentError("invalid bounded JSON object") from error


def read_bytes(path: Path) -> bytes:
    """Read at most the accepted document budget, failing before any mutation."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise EnrollmentError("file exceeds 2 MiB: " + path.name)
    return raw


def atomic_write(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    """Durably replace a local file while preserving explicitly supplied access permissions."""
    descriptor, temporary = tempfile.mkstemp(prefix=".arrival-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def retain(path: Path, raw: bytes) -> None:
    """Persist an immutable journal member or require an exact previous original."""
    if path.exists():
        if read_bytes(path) != raw:
            raise EnrollmentError("retained enrollment file conflicts: " + path.name)
    else:
        atomic_write(path, raw)


def certificate_digest(path: Path) -> str:
    """Hash the exact DER leaf represented by a single independently issued PEM certificate."""
    pem = read_bytes(path).decode("ascii")
    if pem.count("-----BEGIN CERTIFICATE-----") != 1 or pem.count("-----END CERTIFICATE-----") != 1:
        raise EnrollmentError("exactly one PEM leaf certificate required")
    return digest(ssl.PEM_cert_to_DER_cert(pem))


def _text(value: Any) -> bool:
    """Accept explicit bounded authority identifiers without whitespace or control ambiguity."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 128
        and all(ord(character) >= 32 for character in value)
    )


def validate_operator_profile(profile: dict[str, Any]) -> None:
    """Validate versioned existing grants without normalizing or removing historical fields."""
    if (
        set(profile) != {"schema_version", "registry_id", "revision", "grants"}
        or profile["schema_version"]
        not in {
            "roboguide.operator-authorization/v0.1",
            "roboguide.operator-authorization/v0.2",
            _PROFILE_SCHEMA,
        }
        or not _text(profile["registry_id"])
        or type(profile["revision"]) is not int
        or not 1 <= profile["revision"] <= MAX_U64
        or not isinstance(profile["grants"], dict)
        or not 1 <= len(profile["grants"]) <= 4096
    ):
        raise EnrollmentError("valid versioned operator authorization required")
    for leaf, grant in profile["grants"].items():
        if (
            not _DIGEST.fullmatch(leaf)
            or not isinstance(grant, dict)
            or not {"principal_id", "role", "tenant_ids"} <= set(grant)
            or set(grant)
            - {"principal_id", "role", "tenant_ids", "not_before_ms", "expires_at_ms", "revoked"}
            or not _text(grant["principal_id"])
            or grant["role"]
            not in {"viewer", "operator", "recovery-admin", "metrics-reader", "verifier"}
            or not isinstance(grant["tenant_ids"], list)
            or not 1 <= len(grant["tenant_ids"]) <= 64
            or any(not _text(tenant) for tenant in grant["tenant_ids"])
            or len(set(grant["tenant_ids"])) != len(grant["tenant_ids"])
            or "*" in grant["tenant_ids"]
            and grant["role"] not in {"recovery-admin", "metrics-reader"}
            or grant["role"] == "metrics-reader"
            and grant["tenant_ids"] != ["*"]
            or grant["role"] == "verifier"
            and profile["schema_version"] != _PROFILE_SCHEMA
            or "revoked" in grant
            and type(grant["revoked"]) is not bool
        ):
            raise EnrollmentError("malformed existing operator grant")
        for name in ("not_before_ms", "expires_at_ms"):
            if type(grant.get(name, 0)) is not int or not 0 <= grant.get(name, 0) <= MAX_U64:
                raise EnrollmentError("invalid existing grant validity")
        if grant.get("expires_at_ms", 0) and grant["expires_at_ms"] <= grant.get(
            "not_before_ms", 0
        ):
            raise EnrollmentError("invalid existing grant validity interval")


def prepare_operator_profile(
    original: dict[str, Any], leaf: str, config: dict[str, Any]
) -> dict[str, Any]:
    """Add one narrow independent verifier grant, or reuse its exact already enrolled leaf."""
    validate_operator_profile(original)
    principal, tenant = config["producer_principal"], config["tenant_id"]
    if not _text(principal) or not _text(tenant) or "*" in principal or "*" in tenant:
        raise EnrollmentError("explicit independent principal and tenant required")
    if not _DIGEST.fullmatch(leaf):
        raise EnrollmentError("qualified DER certificate digest required")
    grants = original["grants"]
    for previous_leaf, grant in grants.items():
        if previous_leaf != leaf and grant["principal_id"] == principal:
            raise EnrollmentError("verifier principal already belongs to another certificate")
    expected = {
        "principal_id": principal,
        "role": "verifier",
        "tenant_ids": [tenant],
        "not_before_ms": 0,
        "expires_at_ms": 0,
        "revoked": False,
    }
    if leaf in grants:
        actual = {"not_before_ms": 0, "expires_at_ms": 0, "revoked": False, **grants[leaf]}
        if actual != expected or original["schema_version"] != _PROFILE_SCHEMA:
            raise EnrollmentError(
                "existing certificate is not the exact independent verifier grant"
            )
        return decode(encode(original))
    if original["revision"] == MAX_U64 or len(grants) == 4096:
        raise EnrollmentError("operator authorization capacity exhausted")
    result = decode(encode(original))
    result["schema_version"] = _PROFILE_SCHEMA
    result["revision"] += 1
    result["grants"][leaf] = expected
    validate_operator_profile(result)
    return result


def expected_source(config: dict[str, Any]) -> dict[str, Any]:
    """Use only the manual publisher's exact admitted predicates and finite source contract."""
    return {
        "source_id": config["source_id"],
        "revision": config["revision"],
        "tenant_id": config["tenant_id"],
        "producer_principals": [config["producer_principal"]],
        "verifier": config["verifier"],
        "predicates": sorted(t["predicate"] for t in config["targets"]),
        "max_age_ms": 5000,
        "enabled": True,
        "continuous": False,
        "stop_timeout_ms": 5000,
        "require_artifact": False,
    }


def prepare_command(current: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Preserve all other sources, reject conflicts and build one typed registry command."""
    source = expected_source(config)
    registry: dict[str, Any]
    if current is None:
        registry = {
            "schema_version": _REGISTRY_SCHEMA,
            "registry_id": "onsite-arrival-"
            + hashlib.sha256(encode([config["tenant_id"], config["source_id"]])).hexdigest()[:24],
            "revision": 1,
            "sources": {config["source_id"]: source},
        }
        previous_revision = 0
    else:
        if (
            not isinstance(current, dict)
            or set(current) != {"schema_version", "registry_id", "revision", "sources"}
            or current["schema_version"] != _REGISTRY_SCHEMA
            or not _text(current["registry_id"])
            or type(current["revision"]) is not int
            or not 1 <= current["revision"] <= MAX_U64
            or not isinstance(current["sources"], dict)
        ):
            raise EnrollmentError("unknown or malformed existing verifier registry")
        registry = decode(encode(current))
        previous_revision = current["revision"]
        existing = registry["sources"].get(config["source_id"])
        if existing is not None and existing != source:
            raise EnrollmentError(
                "existing verifier source differs; explicit separate migration required"
            )
        for identity, other in registry["sources"].items():
            if not isinstance(other, dict):
                raise EnrollmentError("malformed existing verifier source")
            if (
                identity != config["source_id"]
                and other.get("enabled") is True
                and other.get("tenant_id") == source["tenant_id"]
                and other.get("verifier") == source["verifier"]
                and set(other.get("predicates", [])) & set(source["predicates"])
            ):
                raise EnrollmentError(
                    "another enabled source would make a patrol predicate ambiguous"
                )
        if existing is None:
            if previous_revision == MAX_U64 or len(registry["sources"]) >= 128:
                raise EnrollmentError("verifier registry capacity exhausted")
            registry["revision"] += 1
            registry["sources"][config["source_id"]] = source
    return {
        "command_id": "enroll-onsite-arrival-" + uuid.uuid4().hex,
        "expected_revision": previous_revision,
        "registry": registry,
    }


class EnrollmentClient(Protocol):
    """Expose only bounded enrollment exchanges to transport-independent recovery logic."""

    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Return one original status and bounded JSON envelope without automatic retry."""
        ...


class HttpsClient:
    """Send only bounded allowed Controller requests with exact mTLS and no transport retries."""

    def __init__(
        self,
        origin: str,
        *,
        ca: Path,
        certificate: Path,
        private_key: Path,
        tenant: str,
        administrator: bool,
    ) -> None:
        """Load independently provided credentials and reject redirects or alternate origins."""
        parsed = urllib.parse.urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise EnrollmentError("an exact HTTPS Controller origin is required")
        self.host, self.port = parsed.hostname, parsed.port or 443
        self.tenant, self.administrator = tenant, administrator
        self.context = ssl.create_default_context(cafile=str(ca))
        self.context.load_cert_chain(str(certificate), str(private_key))

    def request(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Perform one direct request; no Mission, action, cancel, stop or evidence write exists."""
        allowed_gets = {"/readyz", "/v1/inventory", "/v1/verifier-sources", "/v1/missions"}
        if self.administrator:
            allowed_gets.add("/v1/resource-allocations")
        if not (
            method == "GET"
            and path in allowed_gets
            and body is None
            or self.administrator
            and method == "POST"
            and path == "/v1/verifier-sources"
            and body is not None
        ):
            raise EnrollmentError("request outside enrollment allowlist")
        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=10, context=self.context
        )
        try:
            headers = {"Content-Type": "application/json"}
            if not self.administrator:
                headers["X-RoboGuide-Tenant"] = self.tenant
            connection.request(
                method,
                path,
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            raw = response.read(MAX_BYTES + 1)
            return response.status, decode(raw)
        finally:
            connection.close()


def require_response(client: EnrollmentClient, path: str) -> dict[str, Any]:
    """Require a successful exact GET instead of interpreting outages as an empty view."""
    status, value = client.request("GET", path)
    if status != 200:
        raise EnrollmentError(f"GET {path} failed with HTTP {status}")
    return value


def require_idle(admin: EnrollmentClient) -> None:
    """Require ready storage with no Unknown attempts and no live resource allocations."""
    ready = require_response(admin, "/readyz")
    allocations = require_response(admin, "/v1/resource-allocations")
    if (
        ready.get("ready") is not True
        or ready.get("degraded") is not False
        or type(ready.get("unknown_attempts")) is not int
        or ready["unknown_attempts"] != 0
        or allocations.get("allocations") != []
    ):
        raise EnrollmentError("Controller is not ready and idle; enrollment withheld")


def registry_view(admin: EnrollmentClient) -> Any:
    """Read the exact authenticated generic verifier registry envelope, including explicit null."""
    view = require_response(admin, "/v1/verifier-sources")
    if set(view) != {"schema_version", "registry"} or view["schema_version"] != (
        "roboguide.semantic-verifier-view/v0.1"
    ):
        raise EnrollmentError("unknown verifier registry view")
    return view["registry"]


def enroll(
    *,
    config: dict[str, Any],
    leaf: str,
    admin_leaf: str,
    operator_path: Path,
    output: Path,
    origin: str,
    admin: EnrollmentClient,
    verifier: EnrollmentClient,
) -> dict[str, Any]:
    """Journal before every write and recover only by reusing the original command and receipt."""
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = output / ".enrollment.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(descriptor)
        retain(
            output / "enrollment-input.json",
            encode(
                {
                    "schema_version": "roboguide.arrival-enrollment-input/v0.1",
                    "source_profile": config,
                    "certificate_digest": leaf,
                    "admin_certificate_digest": admin_leaf,
                    "operator_role_map": str(operator_path.resolve()),
                    "controller_origin": origin,
                }
            ),
        )
        original_raw = read_bytes(operator_path)
        original = decode(original_raw)
        validate_operator_profile(original)
        admin_grant = original["grants"].get(admin_leaf, {})
        if (
            leaf == admin_leaf
            or admin_grant.get("role") != "recovery-admin"
            or admin_grant.get("revoked", False) is not False
            or admin_grant.get("principal_id") == config["producer_principal"]
        ):
            raise EnrollmentError(
                "independent verifier and current recovery-admin certificates required"
            )
        require_idle(admin)
        before_path, after_path = output / "operator-before.json", output / "operator-after.json"
        if before_path.exists() or after_path.exists():
            if not before_path.exists():
                raise EnrollmentError(
                    "incomplete operator backup; retain files for explicit review"
                )
            if not after_path.exists():
                if read_bytes(before_path) != original_raw:
                    raise EnrollmentError("operator changed after interrupted backup preparation")
                retain(after_path, encode(prepare_operator_profile(original, leaf, config)))
            before, after = read_bytes(before_path), read_bytes(after_path)
            if original_raw not in {before, after}:
                raise EnrollmentError("operator authorization changed outside this enrollment")
            candidate = decode(after)
            if prepare_operator_profile(candidate, leaf, config) != candidate:
                raise EnrollmentError("retained verifier grant is no longer exact")
        else:
            candidate = prepare_operator_profile(original, leaf, config)
            after = encode(candidate)
            retain(before_path, original_raw)
            retain(after_path, after)
        command_path = output / "verifier-source-command.json"
        current_registry = registry_view(admin)
        if command_path.exists():
            command_raw = read_bytes(command_path)
            command = decode(command_raw)
            if (
                set(command) != {"command_id", "expected_revision", "registry"}
                or not isinstance(command["command_id"], str)
                or not command["command_id"].startswith("enroll-onsite-arrival-")
                or len(command["command_id"]) > 256
                or type(command["expected_revision"]) is not int
                or not 0 <= command["expected_revision"] <= MAX_U64
                or not isinstance(command["registry"], dict)
                or command.get("registry", {}).get("sources", {}).get(config["source_id"])
                != expected_source(config)
            ):
                raise EnrollmentError("retained command does not grant the exact configured source")
            if current_registry != command["registry"]:
                expected = prepare_command(current_registry, config)
                if any(command[key] != expected[key] for key in ("expected_revision", "registry")):
                    raise EnrollmentError(
                        "registry changed outside the retained enrollment command"
                    )
        else:
            command = prepare_command(current_registry, config)
            command_raw = encode(command)
            retain(command_path, command_raw)
        if read_bytes(operator_path) != original_raw:
            raise EnrollmentError("operator role map changed before installation")
        if original_raw != after:
            atomic_write(operator_path, after, mode=stat.S_IMODE(operator_path.stat().st_mode))
        require_idle(admin)
        inventory = require_response(verifier, "/v1/inventory")
        if inventory.get("schema_version") != "roboguide.inventory/v0.1":
            raise EnrollmentError("verifier inventory probe returned an unknown schema")
        probes: dict[str, int] = {"/v1/inventory": 200}
        for path in ("/v1/verifier-sources", "/v1/missions"):
            status, _ = verifier.request("GET", path)
            if status != 403:
                raise EnrollmentError("restricted verifier unexpectedly accessed " + path)
            probes[path] = status
        retain(output / "verifier-permission-probes.json", encode(probes))
        status, receipt = admin.request("POST", "/v1/verifier-sources", command_raw)
        retained = receipt.get("receipt", {})
        if (
            status != 200
            or receipt.get("schema_version") != "roboguide.registry-command-receipt/v0.1"
            or retained.get("command_id") != command["command_id"]
            or retained.get("kind") != "semantic_verifier"
            or retained.get("input_digest") != digest(encode(command))
            or retained.get("registry_digest") != digest(encode(command["registry"]))
            or retained.get("revision") != command["registry"]["revision"]
            or retained.get("authority_subject") != admin_grant["principal_id"]
            or type(retained.get("accepted_at_ms")) is not int
        ):
            raise EnrollmentError("registration reply is not the original typed command receipt")
        retain(output / "verifier-source-receipt.json", encode(receipt))
        installed = registry_view(admin)
        if installed != command["registry"]:
            raise EnrollmentError("registry readback differs; original command retained for review")
        retain(output / "verifier-source-readback.json", encode(installed))
        result = {
            "schema_version": "roboguide.arrival-enrollment-result/v0.1",
            "status": "registered_permission_checked",
            "certificate_digest": leaf,
            "source_id": config["source_id"],
            "registry_revision": installed["revision"],
            "motion_sent": False,
            "verdict_published": False,
            "automatic_arrival_assessment": False,
        }
        retain(output / "result.json", encode(result))
        return result
    finally:
        lock.unlink()


def main() -> int:
    """Load explicit host-local deployment inputs and perform one reviewable enrollment attempt."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "source-profile",
        "operator-role-map",
        "certificate",
        "private-key",
        "client-ca",
        "admin-cert",
        "admin-key",
        "output-dir",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--controller-origin", required=True)
    arguments = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from robonix_task.arrival_verifier import validate_config

    try:
        config = validate_config(decode(read_bytes(arguments.source_profile)))
        admin = HttpsClient(
            arguments.controller_origin,
            ca=arguments.client_ca,
            certificate=arguments.admin_cert,
            private_key=arguments.admin_key,
            tenant=config["tenant_id"],
            administrator=True,
        )
        verifier = HttpsClient(
            arguments.controller_origin,
            ca=arguments.client_ca,
            certificate=arguments.certificate,
            private_key=arguments.private_key,
            tenant=config["tenant_id"],
            administrator=False,
        )
        result = enroll(
            config=config,
            leaf=certificate_digest(arguments.certificate),
            admin_leaf=certificate_digest(arguments.admin_cert),
            operator_path=arguments.operator_role_map,
            output=arguments.output_dir,
            origin=arguments.controller_origin,
            admin=admin,
            verifier=verifier,
        )
        print(encode(result).decode())
        return 0
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(
            "Enrollment failed; preserve output and current profile: " + str(error), file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
