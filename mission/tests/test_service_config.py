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
    assert "spatial.map.import@v0" in settings.approval_required_contracts
    assert "password" not in path.read_text(encoding="utf-8").lower()


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
