"""Validated deployment configuration for the Mission Request service."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


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
    max_request_bytes: int
    approval_required_contracts: frozenset[str]


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
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise MissionServiceConfigError(
            "service.controller_endpoint must be a fixed HTTP(S) origin"
        )
    contracts_value = service.get("approval_required_contracts")
    if not isinstance(contracts_value, list) or not all(
        isinstance(contract, str) and _valid_contract(contract) for contract in contracts_value
    ):
        raise MissionServiceConfigError(
            "service.approval_required_contracts must contain canonical contracts"
        )
    root = repository_root if repository_root is not None else path.parent.parent
    return MissionServiceSettings(
        listen_host=host,
        listen_port=port,
        state_db=(root / _text(service, "state_db")).resolve(),
        controller_endpoint=endpoint,
        controller_timeout_seconds=_positive_number(service, "controller_timeout_seconds"),
        max_request_bytes=_positive_integer(service, "max_request_bytes"),
        approval_required_contracts=frozenset(contracts_value),
    )


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


def _positive_integer(table: Mapping[str, object], key: str) -> int:
    """Read one strictly positive integer without Boolean coercion."""
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MissionServiceConfigError(f"service.{key} must be a positive integer")
    return value


def _positive_number(table: Mapping[str, object], key: str) -> float:
    """Read one strictly positive request timeout."""
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise MissionServiceConfigError(f"service.{key} must be positive")
    return float(value)


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
