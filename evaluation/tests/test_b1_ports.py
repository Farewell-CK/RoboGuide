"""Offline transport-isolation tests for the production B1 launcher."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from roboguide_eval.b1_ports import PORT_DEFAULTS, deployment_ports, render_deployment_configs

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios/e1-shared-world-episode-51"


def test_legacy_ports_remain_default() -> None:
    """An unconfigured single run keeps every existing transport address."""
    assert deployment_ports({}) == PORT_DEFAULTS


@pytest.mark.parametrize("value", ["0", "65536", "-1", "x", "1;exit", "１２"])
def test_malformed_ports_fail_closed(value: str) -> None:
    """Bad ports cannot become shell text or start a conflicting deployment."""
    with pytest.raises(ValueError):
        deployment_ports({"ROBOGUIDE_B1_CONTROLLER_PORT": value})


def test_duplicate_ports_fail_closed() -> None:
    """Independent services cannot silently receive the same configured port."""
    with pytest.raises(ValueError):
        deployment_ports({"ROBOGUIDE_B1_CONTROLLER_PORT": "25060"})


@pytest.mark.parametrize("offset", [0, 100])
def test_configs_route_every_endpoint_consistently(tmp_path: Path, offset: int) -> None:
    """Node workflows, Controller transport and Mission ingress share one port mapping."""
    ports = {name: 40000 + index + offset for index, name in enumerate(PORT_DEFAULTS)}
    render_deployment_configs(tmp_path, SCENARIO, ports)
    for name in ("node-a.toml", "node-b.toml", "mission-service-b1.toml"):
        text = (tmp_path / name).read_text()
        tomllib.loads(text)
        assert "PLACEHOLDER" not in text
        assert not any(f"127.0.0.1:{port}" in text for port in PORT_DEFAULTS.values())
    service = tomllib.loads((tmp_path / "mission-service-b1.toml").read_text())
    assert service["service"]["listen_port"] == ports["MISSION_PORT"]
    assert service["service"]["controller_endpoint"] == (
        f"http://127.0.0.1:{ports['CONTROLLER_PORT']}"
    )


def test_endpoint_replacements_do_not_cascade(tmp_path: Path) -> None:
    """A deliberate permutation of default ports still maps each original endpoint once."""
    names = list(PORT_DEFAULTS)
    values = list(PORT_DEFAULTS.values())
    ports = dict(zip(names, values[1:] + values[:1], strict=True))
    render_deployment_configs(tmp_path, SCENARIO, ports)
    node = (tmp_path / "node-a.toml").read_text()
    assert f"127.0.0.1:{ports['CONTROLLER_GRPC_PORT']}" in node
    assert f"127.0.0.1:{ports['HABITAT_PORT']}" in node


def test_launcher_observation_and_archival_use_configured_endpoints() -> None:
    """The real launcher must not observe or archive a neighbouring Controller."""
    script = (SCENARIO / "run-b1-roboguide.sh").read_text()
    assert "--controller-endpoint" in script and "--mission-endpoint" in script
    for port in (25060, 28060, 28090, 28100, 28102, 8070):
        assert f"127.0.0.1:{port}" not in script
        assert f"clean_port {port}" not in script
