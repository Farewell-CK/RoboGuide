"""Tests for external Mission configuration and credential safety."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from mission.config import MissionConfigError, load_settings


def test_repository_configuration_selects_luna_without_a_secret() -> None:
    """The committed configuration selects Luna but never embeds an API key."""
    path = Path("config/mission.toml")
    settings = load_settings(path, repository_root=Path.cwd())
    assert settings.llm.model == "gpt-5.6-luna"
    assert settings.llm.review_model == "gpt-5.6-luna"
    assert settings.prompts.version == "v0"
    assert settings.prompts.interpreter_path.is_file()
    assert settings.prompts.planner_path.is_file()
    assert settings.prompts.reviewer_path.is_file()
    assert settings.provider.api_key_env == "OPENAI_API_KEY"
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_remote_plaintext_provider_is_rejected_by_default() -> None:
    """Credentials cannot cross a remote plaintext HTTP endpoint accidentally."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    with pytest.raises(MissionConfigError, match="remote plaintext"):
        settings.provider.endpoint({})


def test_local_tunnel_endpoint_is_allowed() -> None:
    """A localhost HTTP endpoint is valid for an SSH tunnel or local gateway."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    provider = replace(settings.provider, base_url="http://127.0.0.1:8080")
    assert provider.endpoint({}) == "http://127.0.0.1:8080/responses"


def test_required_api_key_is_read_only_from_environment() -> None:
    """Authenticated providers reject startup when their configured environment key is absent."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    with pytest.raises(MissionConfigError, match="OPENAI_API_KEY"):
        settings.provider.api_key({})


def test_prompts_reject_meta_tasks_and_keep_planning_authority_bounded() -> None:
    """Versioned prompts must demand executable tasks without stealing Control authority."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    planner_prompt = settings.prompts.planner_path.read_text(encoding="utf-8")
    interpreter_prompt = settings.prompts.interpreter_path.read_text(encoding="utf-8")
    reviewer_prompt = settings.prompts.reviewer_path.read_text(encoding="utf-8")
    assert "Do not emit meta-tasks" in planner_prompt
    assert "Do not create Tasks" in interpreter_prompt
    assert "must not select concrete nodes" in planner_prompt
    assert "Reject meta-tasks" in reviewer_prompt
