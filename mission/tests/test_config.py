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


def test_prompts_separate_availability_from_required_participation() -> None:
    """All MI stages distinguish candidates from confirmed participation constraints."""
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
    assert (
        "it neither creates an all-participants\n  obligation nor waives one"
        in prompts["interpreter"]
    )
    assert "not from the number of available embodiments" in prompts["planner"]
    assert "Do not omit required\n  participation" in prompts["planner"]
    assert "do not require one logical Actor for every available participant" in prompts["reviewer"]
    assert "preserves every confirmed identity" in prompts["reviewer"]
    assert "Pairwise distinct eventual bindings do not name or select" in prompts["reviewer"]
    assert "do not add placeholder Actors" in prompts["repairer"]
    assert "allocation discretion cannot waive them" in prompts["repairer"]
    assert "do not require setting Actor `physical_entity`" in prompts["repairer"]
    assert all("Episode51" not in prompt for prompt in prompts.values())


def test_prompts_preserve_joint_effects_without_inventing_physical_identity() -> None:
    """All MI stages fence destructive Actor reuse separately from hard distinctness."""
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
    normalized = {name: " ".join(prompt.split()) for name, prompt in prompts.items()}

    assert "every conjunct hold at the same final state" in normalized["interpreter"]
    assert "later operation may invalidate an earlier effect" in normalized["interpreter"]
    assert "without claiming same-participant feasibility" in normalized["interpreter"]
    for role in ("planner", "reviewer", "repairer"):
        prompt = normalized[role]
        assert "all of its effects to coexist at the terminal state" in prompt
        assert "perform an effect-interference check" in prompt
        assert "later operation can invalidate an earlier required effect" in prompt
        assert "preserve enough logical participation capacity" in prompt
        assert "does not by itself prove" in prompt
        assert "`distinct-physical-entities` constraint" in prompt
    assert "point to the Actor/ContextRole/Task references" in normalized["reviewer"]
    assert "Do not misreport this as a need for `requires-active`" in normalized["reviewer"]
    assert "identifies effect-interfering reuse of one Actor" in normalized["repairer"]
    assert "introduce only the logical participation capacity" in normalized["repairer"]
    benchmark_terms = ("Episode3", "Episode51", "any_targets", "Spot", "Fetch")
    assert all(term not in prompt for prompt in prompts.values() for term in benchmark_terms)
