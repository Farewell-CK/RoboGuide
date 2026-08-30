"""Validated Mission planner configuration with environment-only credentials."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class MissionConfigError(ValueError):
    """Report an invalid or unsafe Mission planner configuration."""


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Describe one Responses-compatible provider without storing its credential."""

    name: str
    base_url: str
    responses_path: str
    wire_api: str
    requires_openai_auth: bool
    api_key_env: str

    def endpoint(self, environment: Mapping[str, str]) -> str:
        """Build a safe Responses endpoint and reject remote plaintext HTTP by default."""
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MissionConfigError(f"invalid provider base_url: {self.base_url}")
        is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        allow_insecure = environment.get("ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP") == "1"
        if parsed.scheme == "http" and not is_local and not allow_insecure:
            raise MissionConfigError(
                "remote plaintext LLM endpoint is disabled; use HTTPS, a localhost tunnel, "
                "or explicitly set ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1"
            )
        return f"{self.base_url.rstrip('/')}/{self.responses_path.lstrip('/')}"

    def api_key(self, environment: Mapping[str, str]) -> str | None:
        """Read the provider credential from its configured environment variable."""
        value = environment.get(self.api_key_env)
        if self.requires_openai_auth and not value:
            raise MissionConfigError(f"required credential is missing: {self.api_key_env}")
        return value


@dataclass(frozen=True, slots=True)
class LlmSettings:
    """Configure model selection and bounded Responses request behavior."""

    model_provider: str
    model: str
    review_model: str
    reasoning_effort: str
    disable_response_storage: bool
    network_access: str
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class PromptSettings:
    """Select versioned interpreter, planner, and reviewer prompt assets independently of code."""

    version: str
    interpreter_path: Path
    planner_path: Path
    reviewer_path: Path


@dataclass(frozen=True, slots=True)
class MissionSettings:
    """Contain validated Mission planner, contract, model, and provider settings."""

    planner: str
    contract_version: str
    schema_path: Path
    review_enabled: bool
    prompts: PromptSettings
    llm: LlmSettings
    provider: ProviderSettings


def _table(value: object, path: str) -> dict[str, object]:
    """Return a TOML table or reject the value with a configuration path."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionConfigError(f"{path} must be a table")
    return value


def _string(table: Mapping[str, object], key: str, path: str) -> str:
    """Read required nonblank text from one TOML table."""
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissionConfigError(f"{path}.{key} must be nonblank text")
    return value


def _boolean(table: Mapping[str, object], key: str, path: str) -> bool:
    """Read a required Boolean without accepting integer coercion."""
    value = table.get(key)
    if not isinstance(value, bool):
        raise MissionConfigError(f"{path}.{key} must be a boolean")
    return value


def _positive_number(table: Mapping[str, object], key: str, path: str) -> float:
    """Read a positive numeric request bound from one TOML table."""
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise MissionConfigError(f"{path}.{key} must be positive")
    return float(value)


def load_settings(
    path: Path,
    *,
    repository_root: Path | None = None,
) -> MissionSettings:
    """Load Mission settings while resolving contract assets against the repository root."""
    with path.open("rb") as config_file:
        raw = tomllib.load(config_file)
    mission = _table(raw.get("mission"), "mission")
    prompts = _table(mission.get("prompts"), "mission.prompts")
    llm = _table(mission.get("llm"), "mission.llm")
    provider_name = _string(llm, "model_provider", "mission.llm")
    providers = _table(raw.get("model_providers"), "model_providers")
    provider = _table(providers.get(provider_name), f"model_providers.{provider_name}")
    root = repository_root if repository_root is not None else path.parent.parent
    max_tokens = _positive_number(llm, "max_output_tokens", "mission.llm")
    if not max_tokens.is_integer():
        raise MissionConfigError("mission.llm.max_output_tokens must be an integer")
    wire_api = _string(provider, "wire_api", f"model_providers.{provider_name}")
    if wire_api != "responses":
        raise MissionConfigError(f"unsupported provider wire_api: {wire_api}")
    return MissionSettings(
        planner=_string(mission, "planner", "mission"),
        contract_version=_string(mission, "contract_version", "mission"),
        schema_path=root / _string(mission, "schema_path", "mission"),
        review_enabled=_boolean(mission, "review_enabled", "mission"),
        prompts=PromptSettings(
            version=_string(prompts, "version", "mission.prompts"),
            interpreter_path=root / _string(prompts, "interpreter_path", "mission.prompts"),
            planner_path=root / _string(prompts, "planner_path", "mission.prompts"),
            reviewer_path=root / _string(prompts, "reviewer_path", "mission.prompts"),
        ),
        llm=LlmSettings(
            model_provider=provider_name,
            model=_string(llm, "model", "mission.llm"),
            review_model=_string(llm, "review_model", "mission.llm"),
            reasoning_effort=_string(llm, "model_reasoning_effort", "mission.llm"),
            disable_response_storage=_boolean(llm, "disable_response_storage", "mission.llm"),
            network_access=_string(llm, "network_access", "mission.llm"),
            max_output_tokens=int(max_tokens),
            timeout_seconds=_positive_number(llm, "timeout_seconds", "mission.llm"),
        ),
        provider=ProviderSettings(
            name=_string(provider, "name", f"model_providers.{provider_name}"),
            base_url=_string(provider, "base_url", f"model_providers.{provider_name}"),
            responses_path=_string(provider, "responses_path", f"model_providers.{provider_name}"),
            wire_api=wire_api,
            requires_openai_auth=_boolean(
                provider, "requires_openai_auth", f"model_providers.{provider_name}"
            ),
            api_key_env=_string(provider, "api_key_env", f"model_providers.{provider_name}"),
        ),
    )


def load_default_settings() -> MissionSettings:
    """Load the repository's default Mission configuration from the current workspace."""
    return load_settings(Path("config/mission.toml"), repository_root=Path.cwd())


def current_environment() -> Mapping[str, str]:
    """Expose the process environment behind a read-only mapping boundary."""
    return os.environ
