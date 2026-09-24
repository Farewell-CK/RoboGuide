"""Tests for Mission Service deployment and risk-policy configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from mission.service_config import MissionServiceConfigError, load_service_settings


def test_repository_service_configuration_is_local_and_nonsecret() -> None:
    """The committed service config uses local endpoints and canonical risk contracts."""
    path = Path("config/mission-service.toml")
    settings = load_service_settings(path, repository_root=Path.cwd())
    assert settings.listen_port == 8070
    assert settings.controller_endpoint == "http://127.0.0.1:8080"
    assert settings.artifact_endpoint == "http://127.0.0.1:8090"
    assert settings.grounding_acquisition_attempts == 2
    assert settings.max_grounding_state_evidence == 64
    assert settings.max_grounding_memory_evidence == 32
    assert settings.max_grounding_gaps == 32
    assert settings.grounding_world_payload_schemas == frozenset()
    assert "spatial.map.import@v0" in settings.approval_required_contracts
    assert [rule.rule_id for rule in settings.approval_policy.rules] == [
        "mobility-move",
        "spatial-map-build",
        "spatial-map-import",
    ]
    assert "password" not in path.read_text(encoding="utf-8").lower()


def test_shared_world_service_configuration_selects_deployment_execution_profile() -> None:
    """The shared-world Mission Service loads its fixed profile through deployment config."""
    settings = load_service_settings(
        Path("scenarios/e1-shared-world-episode-51/mission-service-b1.toml"),
        repository_root=Path.cwd(),
    )
    assert (
        settings.execution_profile_path
        == (Path.cwd() / "scenarios/e1-shared-world-episode-51/execution-profile.json").resolve()
    )
    assert (
        settings.planning_profile_path
        == (Path.cwd() / "scenarios/e1-shared-world-episode-51/planning-profile.json").resolve()
    )
    assert (
        settings.grounding_planning_world_evidence_path
        == (Path.cwd() / "PLANNING_WORLD_EVIDENCE_PLACEHOLDER").resolve()
    )
    assert settings.grounding_planning_world_evidence_required is True


@pytest.mark.parametrize("raw", ["true", '"yes"'])
def test_required_planning_world_configuration_needs_exact_boolean_and_path(
    tmp_path: Path, raw: str
) -> None:
    """Required evidence cannot be enabled ambiguously or without its fixed source."""
    path = tmp_path / "mission-service.toml"
    text = Path("config/mission-service.toml").read_text(encoding="utf-8")
    path.write_text(
        text.replace("[service]", f"[service]\ngrounding_planning_world_evidence_required = {raw}"),
        encoding="utf-8",
    )
    with pytest.raises(MissionServiceConfigError, match="required planning world|Boolean"):
        load_service_settings(path, repository_root=tmp_path)


def test_service_configuration_rejects_ambiguous_risk_contract(tmp_path: Path) -> None:
    """Risk policy cannot use a dotted name that disagrees with Node Config parsing."""
    path = tmp_path / "mission-service.toml"
    path.write_text(
        """
[service]
listen_host = "127.0.0.1"
listen_port = 8070
state_db = "requests.sqlite3"
controller_endpoint = "http://127.0.0.1:8080"
controller_timeout_seconds = 30
max_request_bytes = 1024
approval_required_contracts = ["spatial@map.build@v0"]
""",
        encoding="utf-8",
    )
    with pytest.raises(MissionServiceConfigError, match="canonical contracts"):
        load_service_settings(path, repository_root=tmp_path)


def test_service_configuration_loads_context_aware_approval_rule(tmp_path: Path) -> None:
    """Deployment policy preserves typed parameter and semantic context predicates."""
    path = tmp_path / "mission-service.toml"
    path.write_text(
        """
[service]
listen_host = "127.0.0.1"
listen_port = 8070
state_db = "requests.sqlite3"
controller_endpoint = "http://127.0.0.1:8080"
controller_timeout_seconds = 30
max_request_bytes = 1024

[[service.approval_rules]]
id = "hazardous-relocation"
operation = "object.relocate@v1"
parameter_equals = { object = "hazardous-device" }
objective_contains = ["restricted area"]
grounded_constraint_contains = ["supervisor approval"]
""",
        encoding="utf-8",
    )

    settings = load_service_settings(path, repository_root=tmp_path)
    rule = settings.approval_policy.rules[0]
    assert rule.parameter_equals == (("object", "hazardous-device"),)
    assert rule.objective_contains == ("restricted area",)
    assert rule.grounded_constraint_contains == ("supervisor approval",)


def test_service_configuration_rejects_duplicate_grounding_schema_admission(
    tmp_path: Path,
) -> None:
    """Global Mission grounding admission must be explicit and unambiguous."""
    path = tmp_path / "mission-service.toml"
    path.write_text(
        """
[service]
listen_host = "127.0.0.1"
listen_port = 8070
state_db = "requests.sqlite3"
controller_endpoint = "http://127.0.0.1:8080"
controller_timeout_seconds = 30
grounding_world_payload_schemas = ["place/v1", "place/v1"]
max_request_bytes = 1024
approval_required_contracts = []
""",
        encoding="utf-8",
    )

    with pytest.raises(MissionServiceConfigError, match="duplicates"):
        load_service_settings(path, repository_root=tmp_path)


def test_service_configuration_rejects_zero_grounding_attempts(tmp_path: Path) -> None:
    """Grounding acquisition must retain at least one strictly bounded read attempt."""
    path = tmp_path / "mission-service.toml"
    path.write_text(
        """
[service]
listen_host = "127.0.0.1"
listen_port = 8070
state_db = "requests.sqlite3"
controller_endpoint = "http://127.0.0.1:8080"
controller_timeout_seconds = 30
grounding_acquisition_attempts = 0
max_request_bytes = 1024
approval_required_contracts = []
""",
        encoding="utf-8",
    )

    with pytest.raises(MissionServiceConfigError, match="positive integer"):
        load_service_settings(path, repository_root=tmp_path)
