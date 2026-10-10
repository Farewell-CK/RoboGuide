"""Exercise actual B1 deployment preparation without a Provider or a simulator."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from mission.config import load_settings
from mission.service_config import load_service_settings
from roboguide_eval.b1_deployment import MAX_DECLARATION_BYTES, freeze_deployment, load_deployment

ROOT = Path(__file__).resolve().parents[2]
NAVIGATION = ROOT / "scenarios/e1-shared-world-episode-51"
RELOCATION = ROOT / "scenarios/e1-shared-world-relocation"
DIST_MAN = RELOCATION / "b1-deployment-dist-man.json"


def offline_environment(tmp_path: Path) -> dict[str, str]:
    """Use original-shaped config paths and tripwires forbidding network/SUT startup."""
    emos = tmp_path / "emos"
    for scenario in (NAVIGATION, RELOCATION):
        declaration = load_deployment(scenario)
        config = emos / declaration.habitat_config
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("# Offline path fixture only; not an executable Habitat configuration.\n")
    config = emos / load_deployment(RELOCATION, declaration_path=DIST_MAN).habitat_config
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("# Offline distance-manipulation config path fixture.\n")
    commands = tmp_path / "tripwires"
    commands.mkdir()
    for name in ("curl", "conda", "ss"):
        command = commands / name
        command.write_text('#!/bin/bash\nprintf "forbidden component invoked\\n" >&2\nexit 99\n')
        command.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    for key in tuple(environment):
        if (
            key.startswith("ROBOGUIDE_B1_")
            or key.startswith("ROBOGUIDE_MISSION_")
            or key == "HABITAT_RELOCATION_COMPLETION_BINDING"
        ):
            del environment[key]
    environment.update(
        ROBOGUIDE_EMOS_ROOT=str(emos),
        ROBOGUIDE_B1_PREPARE_ONLY="1",
        PATH=str(commands) + os.pathsep + environment["PATH"],
    )
    return environment


def prepare(
    tmp_path: Path, scenario: Path, overrides: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the maintained shell entry through its real offline preparation boundary."""
    environment = offline_environment(tmp_path)
    environment.update(overrides or {})
    frozen = tmp_path / "frozen.json"
    frozen.write_bytes((NAVIGATION / "b1-input.json").read_bytes())
    run = tmp_path / "run"
    result = subprocess.run(
        ["bash", str(scenario / "run-b1-roboguide.sh"), str(run), str(frozen)],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    return result, run


@pytest.mark.parametrize("scenario", [NAVIGATION, RELOCATION])
def test_real_runner_prepares_the_selected_deployment(tmp_path: Path, scenario: Path) -> None:
    """Actual preparation freezes config, ports, resources and MI sources without SUT calls."""
    result, run = prepare(tmp_path, scenario)
    assert result.returncode == 0, result.stderr
    declaration = load_deployment(scenario)
    used = json.loads((run / "b1-deployment-used.json").read_bytes())
    assert used["declaration"]["max_steps"] == declaration.max_steps
    assert used["declaration"]["enable_relocation"] is declaration.enable_relocation
    assert used["relocation_completion_binding"] is declaration.relocation_completion_binding
    requirement = json.loads((run / "b1-local-execution-profile-required.json").read_bytes())
    assert requirement["relocation_completion_binding"] is declaration.relocation_completion_binding
    assert requirement["run_id"] == run.name
    assert (
        requirement["deployment_sha256"]
        == hashlib.sha256((run / "b1-deployment-used.json").read_bytes()).hexdigest()
    )
    assert (
        requirement["frozen_input_sha256"]
        == hashlib.sha256((run / "b1-input-used.json").read_bytes()).hexdigest()
    )
    assert used["habitat_config_path"].endswith(declaration.habitat_config)
    for suffix, port in (("a", 28100), ("b", 28102)):
        node = tomllib.loads((run / f"node-{suffix}.toml").read_text())
        assert node["connections"][0]["endpoint"] == f"http://127.0.0.1:{port}"
        assert node["node_id"] in used["node_ids"]
    service = tomllib.loads((run / "mission-service-b1.toml").read_text())["service"]
    assert service["grounding_planning_world_evidence_required"] is True
    assert "PLACEHOLDER" not in (run / "mission-service-b1.toml").read_text()
    assert (run / "mission-config-used.toml").exists()
    assert not (run / "b1-request-record.json").exists()
    assert not (run / "shared-bridge.log").exists()
    assert not (run / "controller.sqlite3").exists()
    if declaration.enable_relocation:
        profile = json.loads((run / "relocation-registration-profile.json").read_bytes())
        assert [agent["robot_type"] for agent in profile["agents"]] == [
            "FetchRobot",
            "StretchRobot",
        ]
        assert all(agent["resource"]["capacity"] == 1 for agent in profile["agents"])
        assert Path(service["execution_profile_path"]) == run / "execution-profile.json"
        assert Path(service["planning_profile_path"]) == run / "planning-profile.json"
        loaded = load_service_settings(run / "mission-service-b1.toml", repository_root=ROOT)
        assert loaded.execution_profile_path == run / "execution-profile.json"
        assert loaded.grounding_planning_world_evidence_required is True
        settings = load_settings(run / "mission-config-used.toml", repository_root=ROOT)
        assert settings.llm.model == "gpt-6.1-sol"
        assert settings.review_enabled is True
    else:
        assert not (run / "relocation-registration-profile.json").exists()


def test_real_runner_selects_an_explicit_declaration_before_startup(tmp_path: Path) -> None:
    """The generic relocation runner freezes the selected variant without a service or model."""
    selected = tmp_path / "explicit deployment.json"
    selected.write_bytes(DIST_MAN.read_bytes())
    result, run = prepare(tmp_path, RELOCATION, {"ROBOGUIDE_B1_DEPLOYMENT": str(selected)})
    assert result.returncode == 0, result.stderr
    used = json.loads((run / "b1-deployment-used.json").read_bytes())
    declaration = load_deployment(RELOCATION, declaration_path=selected)
    assert used["declaration"]["habitat_config"] == declaration.habitat_config
    assert used["declaration"]["max_steps"] == 4000
    assert used["relocation_completion_binding"] is True
    assert (
        used["habitat_config_sha256"]
        == hashlib.sha256(Path(used["habitat_config_path"]).read_bytes()).hexdigest()
    )
    assert (run / "b1-input-used.json").read_bytes() == (tmp_path / "frozen.json").read_bytes()
    assert not (run / "b1-request-record.json").exists()
    assert not (run / "shared-bridge.log").exists()
    assert not (run / "controller.sqlite3").exists()


def test_invalid_explicit_declaration_cannot_fall_back(tmp_path: Path) -> None:
    """A selected missing declaration fails rather than silently loading the height variant."""
    result, run = prepare(
        tmp_path, RELOCATION, {"ROBOGUIDE_B1_DEPLOYMENT": str(tmp_path / "missing.json")}
    )
    assert result.returncode != 0
    assert not (run / "b1-deployment-used.json").exists()
    assert not (run / "b1-request-record.json").exists()


def test_relocation_custom_ports_reach_every_workflow(tmp_path: Path) -> None:
    """The relocation templates use the same noncascading transport mapping as navigation."""
    result, run = prepare(
        tmp_path,
        RELOCATION,
        {"ROBOGUIDE_B1_HABITAT_PORT": "38101", "ROBOGUIDE_B1_HABITAT_PORT_B": "38102"},
    )
    assert result.returncode == 0, result.stderr
    for suffix, port in (("a", 38101), ("b", 38102)):
        text = (run / f"node-{suffix}.toml").read_text()
        assert f"127.0.0.1:{port}" in text
        assert "127.0.0.1:28100" not in text
        assert "127.0.0.1:28102" not in text


@pytest.mark.parametrize("flag", ["GOAL_REGION_NAVIGATION", "RETAIN_STOPPED_SESSION"])
def test_unsupported_relocation_profiles_fail_before_start(tmp_path: Path, flag: str) -> None:
    """Manipulation cannot silently inherit navigation-only or cancellation continuation modes."""
    result, run = prepare(tmp_path, RELOCATION, {"ROBOGUIDE_B1_" + flag: "1"})
    assert result.returncode != 0
    assert "unsupported_relocation_profile_combination" in result.stderr
    assert not (run / "shared-bridge.log").exists()


def test_existing_archive_is_preserved(tmp_path: Path) -> None:
    """A second preparation cannot overwrite the frozen workload or a historical manifest."""
    result, run = prepare(tmp_path, RELOCATION)
    assert result.returncode == 0, result.stderr
    before = {
        name: (run / name).read_bytes()
        for name in ("b1-input-used.json", "b1-deployment-used.json")
    }
    result = subprocess.run(
        ["bash", str(RELOCATION / "run-b1-roboguide.sh"), str(run), str(tmp_path / "frozen.json")],
        env={**os.environ, "ROBOGUIDE_B1_PREPARE_ONLY": "1"},
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0 and "refusing to overwrite" in result.stderr
    assert {name: (run / name).read_bytes() for name in before} == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "wrong"),
        ("schema_version", []),
        ("max_steps", True),
        ("max_steps", 0),
        ("max_steps", 100001),
        ("enable_relocation", "true"),
        ("relocation_completion_binding", 1),
        ("relocation_completion_binding", "true"),
        ("habitat_config", "../escape.yaml"),
        ("habitat_config", "/absolute.yaml"),
        ("habitat_config", "cmd\n.yaml"),
        ("unknown_field", "invented"),
    ],
)
def test_invalid_deployment_declaration_fails_closed(
    tmp_path: Path, field: str, value: Any
) -> None:
    """Invalid deployment fields cannot become launch flags or alter the task contract."""
    document = json.loads((RELOCATION / "b1-deployment.json").read_bytes())
    document[field] = value
    (tmp_path / "b1-deployment.json").write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_deployment(tmp_path)


def test_declaration_read_is_bounded(tmp_path: Path) -> None:
    """Oversize declarations fail before unbounded parsing or launch."""
    (tmp_path / "b1-deployment.json").write_bytes(b" " * (MAX_DECLARATION_BYTES + 1))
    with pytest.raises(ValueError, match="byte budget"):
        load_deployment(tmp_path)


@pytest.mark.parametrize("flag", ["0", "true", "2", ""])
def test_declared_completion_cannot_be_silently_overridden(tmp_path: Path, flag: str) -> None:
    """An omitted shell flag uses the declaration; contradictory or invalid flags stop startup."""
    result, run = prepare(tmp_path, RELOCATION, {"HABITAT_RELOCATION_COMPLETION_BINDING": flag})
    assert result.returncode != 0
    assert not (run / "b1-deployment-used.json").exists()
    assert not (run / "shared-bridge.log").exists()
    assert not (run / "b1-request-record.json").exists()


def test_new_declaration_requires_an_explicit_completion_choice(tmp_path: Path) -> None:
    """The new deployment version cannot fall back to the legacy default when a field is absent."""
    document = json.loads((RELOCATION / "b1-deployment.json").read_bytes())
    del document["relocation_completion_binding"]
    (tmp_path / "b1-deployment.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="schema"):
        load_deployment(tmp_path)
    document["relocation_completion_binding"] = True
    document["enable_relocation"] = False
    (tmp_path / "b1-deployment.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="requires relocation"):
        load_deployment(tmp_path)


@pytest.mark.parametrize(
    ("legacy", "override", "enabled"),
    [(True, None, False), (True, "1", True), (False, None, False)],
)
def test_legacy_and_explicitly_unbound_deployments_remain_available(
    tmp_path: Path, legacy: bool, override: str | None, enabled: bool
) -> None:
    """Legacy opt-in and a declared unbound comparison arm are frozen without modifying plans."""
    environment = offline_environment(tmp_path)
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    declaration = json.loads((RELOCATION / "b1-deployment.json").read_bytes())
    if legacy:
        declaration["schema_version"] = "roboguide.e1.b1-deployment/v0.1"
        del declaration["relocation_completion_binding"]
    else:
        declaration["relocation_completion_binding"] = False
    (scenario / "b1-deployment.json").write_text(json.dumps(declaration))
    for suffix in ("a", "b"):
        (scenario / f"node-{suffix}.toml").write_bytes(
            (RELOCATION / f"node-{suffix}.toml").read_bytes()
        )
    run = tmp_path / "frozen-run"
    run.mkdir()
    (run / "b1-input-used.json").write_bytes((NAVIGATION / "b1-input.json").read_bytes())
    values = freeze_deployment(
        run,
        scenario,
        Path(environment["ROBOGUIDE_EMOS_ROOT"]),
        completion_binding_override=override,
    )
    assert values["RELOCATION_COMPLETION_BINDING"] == int(enabled)
    used = json.loads((run / "b1-deployment-used.json").read_bytes())
    assert used["declaration"] == declaration
    assert used["relocation_completion_binding"] is enabled


def test_node_wait_uses_actual_inventory_identity(tmp_path: Path) -> None:
    """An unrelated Node ID appearing in diagnostics cannot satisfy the registration barrier."""
    source = (NAVIGATION / "run-b1-roboguide.sh").read_text()
    function = source[source.index("wait_nodes() {") : source.index("\nwait_mission_terminal() {")]
    inventory = tmp_path / "inventory.json"
    # Use shell function substitution to feed a deterministic HTTP response into the real barrier.
    fixture = (
        "CONTROLLER_PORT=1 NODE_A_ID=a NODE_B_ID=b\ncurl() { cat "
        + shlex.quote(str(inventory))
        + "; }\n"
    )
    fixture += 'seq() { printf "1\\n"; }\nsleep() { :; }\n' + function + "\nwait_nodes\n"
    for nodes, accepted in (
        ([{"node_id": "a"}, {"node_id": "b"}], True),
        ([{"node_id": "a", "detail": "b"}], False),
    ):
        inventory.write_text(json.dumps({"nodes": nodes}))
        result = subprocess.run(["bash", "-c", fixture], capture_output=True, check=False)
        assert (result.returncode == 0) is accepted
