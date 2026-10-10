"""Offline transport-isolation tests for the production B1 launcher."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from roboguide_eval.b1_ports import (
    PORT_DEFAULTS,
    deployment_ports,
    mission_observation_budget,
    render_deployment_configs,
)

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios/e1-shared-world-episode-51"


def test_legacy_ports_remain_default() -> None:
    """An unconfigured single run keeps every existing transport address."""
    assert deployment_ports({}) == PORT_DEFAULTS


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_live_ports_include_only_configured_endpoints(count: int) -> None:
    """A sparse live deployment never reserves or rejects an unused endpoint's port."""
    ports = deployment_ports({}, count)
    names = {"HABITAT_PORT"} | {"HABITAT_PORT_" + chr(65 + agent) for agent in range(1, count)}
    assert {name for name in ports if name.startswith("HABITAT_PORT")} == names
    if count == 1:
        assert deployment_ports({"ROBOGUIDE_B1_HABITAT_PORT_B": "not-used"}, 1) == ports


@pytest.mark.parametrize("count", [0, 5, True])
def test_invalid_endpoint_count_cannot_start_services(count: int) -> None:
    """A malformed declared endpoint count fails before transport or process startup."""
    with pytest.raises(ValueError):
        deployment_ports({}, count)


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


@pytest.mark.parametrize("value", ["0", "-1", "86401", "3600;exit", "1.5"])
def test_invalid_observation_budget_rejected(value: str) -> None:
    """Unbounded or injectable waits fail before any SUT process starts."""
    with pytest.raises(ValueError):
        mission_observation_budget({"ROBOGUIDE_B1_MISSION_OBSERVATION_BUDGET_SECONDS": value})


def test_slow_provider_observation_can_outlast_single_call() -> None:
    """A long operation can be observed without extending its physical step budget."""
    assert mission_observation_budget({}) == 1800
    assert (
        mission_observation_budget({"ROBOGUIDE_B1_MISSION_OBSERVATION_BUDGET_SECONDS": "18000"})
        == 18000
    )
    script = (SCENARIO / "run-b1-roboguide.sh").read_text()
    assert (
        'wait_mission_terminal "$RUN/mission.json" "$MISSION_OBSERVATION_BUDGET_SECONDS"' in script
    )
