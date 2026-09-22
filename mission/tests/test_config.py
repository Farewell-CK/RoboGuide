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
    assert settings.prompts.repairer_path.is_file()
    assert settings.max_repair_attempts == 2
    assert settings.contract_version == "roboguide.mission-plan/v0.8"
    assert settings.schema_path == Path.cwd() / "contracts/mission/v0.8/mission-plan.schema.json"
    assert settings.capability_catalog_path == (
        Path.cwd() / "contracts/capability/v0.3/catalog.json"
    )
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
    repairer_prompt = settings.prompts.repairer_path.read_text(encoding="utf-8")
    assert "Do not emit meta-tasks" in planner_prompt
    assert "Do not create Tasks" in interpreter_prompt
    assert "must not select concrete nodes" in planner_prompt
    assert "Reject meta-tasks" in reviewer_prompt
    assert "do not invent user facts" in repairer_prompt
    assert "never invent an estimated duration" in planner_prompt
    assert "Local EAIOS workflow steps are not over-decomposed" in reviewer_prompt
    assert "Operation is what to execute" in planner_prompt
    assert "do not consult or infer live Node inventory" in planner_prompt


def test_prompts_share_mission_semantic_consistency_rules() -> None:
    """Interpreter, Planner, Reviewer, and Repairer retain the four corrected boundaries."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    planner_prompt = settings.prompts.planner_path.read_text(encoding="utf-8")
    interpreter_prompt = settings.prompts.interpreter_path.read_text(encoding="utf-8")
    reviewer_prompt = settings.prompts.reviewer_path.read_text(encoding="utf-8")
    repairer_prompt = settings.prompts.repairer_path.read_text(encoding="utf-8")

    assert "earliest_start_offset_ms = 0" in planner_prompt
    assert "earliest_start_offset_ms = 0" in reviewer_prompt
    assert "earliest_start_offset_ms = 0" in repairer_prompt
    assert "physical expected effect alone does not require" in planner_prompt
    assert "physical expected effect alone is not a reason" in reviewer_prompt
    assert "physical expected effect alone does not require" in repairer_prompt
    assert "Freshness is evidence, not an automatic truth" in interpreter_prompt
    assert "do not\n  select one claim merely because it is `Fresh`" in interpreter_prompt
    assert "Never say that later planning" in interpreter_prompt
    assert "selected-by-later-planning" in planner_prompt
    assert "selected-by-later-planning" in reviewer_prompt
    assert "selected-by-later-planning" in repairer_prompt


def test_prompts_separate_available_participants_from_mandatory_execution() -> None:
    """Participant availability cannot silently become an all-executors requirement."""
    settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
    prompts = {
        name: path.read_text(encoding="utf-8")
        for name, path in {
            "interpreter": settings.prompts.interpreter_path,
            "planner": settings.prompts.planner_path,
            "reviewer": settings.prompts.reviewer_path,
            "repairer": settings.prompts.repairer_path,
        }.items()
    }

    assert "availability does not require every participant" in prompts["interpreter"]
    assert "delegates division of work" in prompts["interpreter"]
    assert "not from the number of available embodiments" in prompts["planner"]
    assert "do not require one logical Actor for every available participant" in prompts["reviewer"]
    assert "do not add placeholder Actors" in prompts["repairer"]
    assert all("Episode51" not in prompt for prompt in prompts.values())
