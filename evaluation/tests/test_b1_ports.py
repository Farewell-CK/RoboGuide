"""Offline transport-isolation tests for the production B1 launcher."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest
from mission.api import build_engine
from mission.execution_profile import DeploymentExecutionProfile
from mission.planning_profile import DeploymentPlanningProfile
from mission.service_config import MissionServiceConfigError
from roboguide_eval.b1_ports import (
    PORT_DEFAULTS,
    deployment_ports,
    mission_observation_budget,
    render_deployment_configs,
)

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios/e1-shared-world-episode-51"


@pytest.mark.parametrize("scenario", ["episode-51", "relocation", "perception", "rearrangement"])
def test_prepared_templates_compose_the_real_mi_engine_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Compose production MI without serving HTTP, creating a Request or calling a Provider."""
    source = SCENARIO.parent / f"e1-shared-world-{scenario}"
    count = 3 if scenario == "rearrangement" else 2
    configs = tuple(f"node-{chr(97 + index)}.toml" for index in range(count))
    render_deployment_configs(tmp_path, source, deployment_ports({}, count), configs)

    def prohibit_network(*args: object, **kwargs: object) -> None:
        """Fail the test if configuration composition unexpectedly attempts any HTTP call."""
        raise AssertionError("offline MI startup must not make network requests")

    monkeypatch.setattr("urllib.request.urlopen", prohibit_network)
    monkeypatch.setattr(
        "mission.api.current_environment",
        lambda: {"OPENAI_API_KEY": "offline-placeholder", "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"},
    )
    engine, settings = build_engine(
        source / "mission-config-b1.toml",
        tmp_path / "mission-service-b1.toml",
        SCENARIO.parents[1],
    )
    assert engine is not None
    assert settings.state_db == tmp_path / "mission-service.sqlite3"


def test_live_engine_fails_closed_when_required_planning_evidence_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live configuration cannot silently fall back to the legacy MI grounding schema."""
    source = SCENARIO.parent / "e1-shared-world-perception"
    render_deployment_configs(
        tmp_path, source, deployment_ports({}, 2), ("node-a.toml", "node-b.toml")
    )
    service = tmp_path / "mission-service-b1.toml"
    service.write_text(
        service.read_text(encoding="utf-8").replace(
            "execution_planning_evidence_required = false",
            "execution_planning_evidence_required = true",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mission.api.current_environment",
        lambda: {"OPENAI_API_KEY": "offline-placeholder", "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"},
    )
    with pytest.raises(MissionServiceConfigError, match="required execution planning evidence"):
        build_engine(source / "mission-config-b1.toml", service, SCENARIO.parents[1])


@pytest.mark.parametrize("scenario", ["episode-51", "relocation", "perception", "rearrangement"])
def test_rendered_profiles_are_consumable_before_starting_a_world(
    tmp_path: Path, scenario: str
) -> None:
    """Runner preparation loads exact production profiles without credentials or services."""
    source = SCENARIO.parent / f"e1-shared-world-{scenario}"
    count = 3 if scenario == "rearrangement" else 2
    configs = tuple(f"node-{chr(97 + index)}.toml" for index in range(count))
    render_deployment_configs(tmp_path, source, deployment_ports({}, count), configs)
    for filename, loader in (
        ("planning-profile.json", DeploymentPlanningProfile.load),
        ("execution-profile.json", DeploymentExecutionProfile.load),
    ):
        rendered = tmp_path / filename
        loaded = loader(rendered if rendered.is_file() else source / filename)
        assert loaded.to_json() == json.loads((source / filename).read_bytes())


@pytest.mark.parametrize(
    ("filename", "message"),
    [("planning-profile.json", "digest does not match"), ("execution-profile.json", "units")],
)
def test_profile_content_drift_rejects_before_any_render_or_service(
    tmp_path: Path, filename: str, message: str
) -> None:
    """Unconsumable deployment facts fail preparation before any run-local write or service."""
    source = tmp_path / "scenario"
    shutil.copytree(SCENARIO.parent / "e1-shared-world-perception", source)
    profile_path = source / filename
    document = json.loads(profile_path.read_bytes())
    if filename == "planning-profile.json":
        document["identity"]["revision"] = "edited-without-new-content-digest"
    else:
        document["profiles"][0]["resources"][0]["units"] = 0
    profile_path.write_text(json.dumps(document))
    output = tmp_path / "run"
    output.mkdir()
    with pytest.raises(ValueError, match=message):
        render_deployment_configs(output, source, deployment_ports({}))
    assert list(output.iterdir()) == []


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
