"""Validated deployment configuration for the Mission Request service."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from mission.approval import ApprovalPolicy, ApprovalRule, ApprovalScalar

_DEFAULT_ARTIFACT_ENDPOINT = "http://127.0.0.1:8090"
_DEFAULT_GROUNDING_TIMEOUT_SECONDS = 5.0
_DEFAULT_MAX_GROUNDING_STATE_EVIDENCE = 64
_DEFAULT_MAX_GROUNDING_MEMORY_EVIDENCE = 32


class MissionServiceConfigError(ValueError):
    """Report an invalid Mission Service deployment setting."""


@dataclass(frozen=True, slots=True)
class MissionServiceSettings:
    """Contain fixed listener, storage, Controller, and risk-policy settings."""

    listen_host: str
    listen_port: int
    state_db: Path
    controller_endpoint: str
    controller_timeout_seconds: float
    artifact_endpoint: str
    grounding_timeout_seconds: float
    max_grounding_state_evidence: int
    max_grounding_memory_evidence: int
    grounding_world_payload_schemas: frozenset[str]
    max_request_bytes: int
    approval_policy: ApprovalPolicy

    @property
    def approval_required_contracts(self) -> frozenset[str]:
        """Return unconditional operation rules through the legacy diagnostics view."""
        return frozenset(
            rule.operation
            for rule in self.approval_policy.rules
            if not rule.parameter_equals
            and not rule.objective_contains
            and not rule.grounded_constraint_contains
        )


def load_service_settings(
    path: Path, *, repository_root: Path | None = None
) -> MissionServiceSettings:
    """Load deployment settings and reject unsafe endpoints or ambiguous contracts."""
    with path.open("rb") as source:
        raw = tomllib.load(source)
    service = _table(raw.get("service"), "service")
    host = _text(service, "listen_host")
    port = _positive_integer(service, "listen_port")
    if port > 65_535:
        raise MissionServiceConfigError("service.listen_port exceeds 65535")
    endpoint = _text(service, "controller_endpoint").rstrip("/")
    _validate_origin(endpoint, "controller_endpoint")
    artifact_endpoint = _optional_text(
        service, "artifact_endpoint", _DEFAULT_ARTIFACT_ENDPOINT
    ).rstrip("/")
    _validate_origin(artifact_endpoint, "artifact_endpoint")
    approval_policy = _approval_policy(service)
    root = repository_root if repository_root is not None else path.parent.parent
    return MissionServiceSettings(
        listen_host=host,
        listen_port=port,
        state_db=(root / _text(service, "state_db")).resolve(),
        controller_endpoint=endpoint,
        controller_timeout_seconds=_positive_number(service, "controller_timeout_seconds"),
        artifact_endpoint=artifact_endpoint,
        grounding_timeout_seconds=_optional_positive_number(
            service, "grounding_timeout_seconds", _DEFAULT_GROUNDING_TIMEOUT_SECONDS
        ),
        max_grounding_state_evidence=_optional_positive_integer(
            service, "max_grounding_state_evidence", _DEFAULT_MAX_GROUNDING_STATE_EVIDENCE
        ),
        max_grounding_memory_evidence=_optional_positive_integer(
            service, "max_grounding_memory_evidence", _DEFAULT_MAX_GROUNDING_MEMORY_EVIDENCE
        ),
        grounding_world_payload_schemas=_optional_text_set(
            service, "grounding_world_payload_schemas"
        ),
        max_request_bytes=_positive_integer(service, "max_request_bytes"),
        approval_policy=approval_policy,
    )


def _validate_origin(value: str, key: str) -> None:
    """Reject credentials, paths, redirects-by-configuration, and ambiguous HTTP origins."""
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise MissionServiceConfigError(f"service.{key} must be a fixed HTTP(S) origin")


def _table(value: object, path: str) -> dict[str, object]:
    """Return a string-keyed TOML table or reject the deployment file."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionServiceConfigError(f"{path} must be a table")
    return value


def _text(table: Mapping[str, object], key: str) -> str:
    """Read one required nonblank deployment text value."""
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissionServiceConfigError(f"service.{key} must be nonblank text")
    return value


def _optional_text(table: Mapping[str, object], key: str, default: str) -> str:
    """Read an additive text setting while preserving compatible local defaults."""
    if key not in table:
        return default
    return _text(table, key)


def _positive_integer(table: Mapping[str, object], key: str) -> int:
    """Read one strictly positive integer without Boolean coercion."""
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MissionServiceConfigError(f"service.{key} must be a positive integer")
    return value


def _optional_positive_integer(table: Mapping[str, object], key: str, default: int) -> int:
    """Read an additive positive integer setting or its bounded default."""
    if key not in table:
        return default
    return _positive_integer(table, key)


def _positive_number(table: Mapping[str, object], key: str) -> float:
    """Read one strictly positive request timeout."""
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise MissionServiceConfigError(f"service.{key} must be positive")
    return float(value)


def _optional_positive_number(table: Mapping[str, object], key: str, default: float) -> float:
    """Read an additive positive numeric setting or its bounded default."""
    if key not in table:
        return default
    return _positive_number(table, key)


def _optional_text_set(table: Mapping[str, object], key: str) -> frozenset[str]:
    """Read an optional duplicate-free set of explicitly admitted schema identities."""
    if key not in table:
        return frozenset()
    value = table.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise MissionServiceConfigError(f"service.{key} must contain nonblank text")
    if len(value) != len(set(value)):
        raise MissionServiceConfigError(f"service.{key} must not contain duplicates")
    return frozenset(value)


def _valid_contract(value: str) -> bool:
    """Return whether a configured risk selector uses canonical last-dot identity."""
    qualified, separator, version = value.rpartition("@")
    namespace, dot, name = qualified.rpartition(".")
    return bool(
        separator
        and dot
        and version
        and "@" not in qualified
        and "@" not in version
        and name
        and "." not in name
        and namespace
        and all(
            segment and not any(character.isspace() for character in segment)
            for segment in namespace.split(".")
        )
        and not any(character.isspace() for character in name + version)
    )


def _approval_policy(service: Mapping[str, object]) -> ApprovalPolicy:
    """Parse current structured rules or normalize the legacy contract list."""
    rules_value = service.get("approval_rules")
    contracts_value = service.get("approval_required_contracts")
    if rules_value is not None and contracts_value is not None:
        raise MissionServiceConfigError(
            "service cannot define both approval_rules and approval_required_contracts"
        )
    if rules_value is None:
        if not isinstance(contracts_value, list) or not all(
            isinstance(contract, str) and _valid_contract(contract) for contract in contracts_value
        ):
            raise MissionServiceConfigError(
                "service.approval_required_contracts must contain canonical contracts"
            )
        return ApprovalPolicy.from_contracts(frozenset(contracts_value))
    if not isinstance(rules_value, list):
        raise MissionServiceConfigError("service.approval_rules must be an array of tables")
    rules = tuple(
        _approval_rule(value, f"service.approval_rules[{index}]")
        for index, value in enumerate(rules_value)
    )
    try:
        return ApprovalPolicy(rules)
    except ValueError as error:
        raise MissionServiceConfigError(str(error)) from error


def _approval_rule(value: object, path: str) -> ApprovalRule:
    """Parse one closed context-aware approval rule from deployment configuration."""
    rule = _table(value, path)
    expected = {
        "id",
        "operation",
        "parameter_equals",
        "objective_contains",
        "grounded_constraint_contains",
    }
    if set(rule) != expected:
        raise MissionServiceConfigError(f"{path} fields must be {sorted(expected)}")
    operation = rule.get("operation")
    if not isinstance(operation, str) or not _valid_contract(operation):
        raise MissionServiceConfigError(f"{path}.operation must be canonical")
    parameters = _table(rule.get("parameter_equals"), f"{path}.parameter_equals")
    parameter_values: list[tuple[str, ApprovalScalar]] = []
    for name, parameter in parameters.items():
        if (
            not name.strip()
            or isinstance(parameter, (list, dict))
            or parameter is None
            or not isinstance(parameter, str | int | float | bool)
        ):
            raise MissionServiceConfigError(f"{path}.parameter_equals must contain scalars")
        parameter_values.append((name, parameter))
    try:
        return ApprovalRule(
            rule_id=_rule_text(rule, "id", path),
            operation=operation,
            parameter_equals=tuple(sorted(parameter_values)),
            objective_contains=_rule_text_array(rule, "objective_contains", path),
            grounded_constraint_contains=_rule_text_array(
                rule, "grounded_constraint_contains", path
            ),
        )
    except ValueError as error:
        raise MissionServiceConfigError(str(error)) from error


def _rule_text(rule: Mapping[str, object], key: str, path: str) -> str:
    """Read one nonblank approval-rule string with an exact diagnostic path."""
    value = rule.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissionServiceConfigError(f"{path}.{key} must be nonblank text")
    return value


def _rule_text_array(rule: Mapping[str, object], key: str, path: str) -> tuple[str, ...]:
    """Read one approval-rule text array while rejecting empty predicates."""
    value = rule.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise MissionServiceConfigError(f"{path}.{key} must contain nonblank text")
    return tuple(value)
