"""Actual deployment sources fence sparse live endpoints before registration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.endpoint_registry import (  # noqa: E402
    build_endpoint_registry,
    prepare_live_deployment,
    registry_digest,
    verify_endpoint_sources,
)
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.recovery_deployment import render_node_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def node_sources(run: Path, count: int) -> tuple[tuple[int, Path], ...]:
    """Render distinct configured endpoints from maintained real registration templates."""
    run.mkdir(exist_ok=True)
    source = ROOT / "scenarios/e1-shared-world-rearrangement"
    records = []
    for agent in range(count):
        text = (source / f"node-{chr(97 + min(agent, 2))}.toml").read_text()
        if agent == 3:
            text = (
                text.replace('-node-c"', '-node-d"')
                .replace("28104", "28106")
                .replace("slot-c", "slot-d")
                .replace("simulator-c", "simulator-d")
            )
        path = run / f"node-{chr(97 + agent)}.toml"
        path.write_text(text)
        records.append((agent, path))
    return tuple(records)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_registry_binds_configured_endpoints_and_recovery(run_path: Path, count: int) -> None:
    """Endpoint count and support come from configuration, never goal or Actor counts."""
    sources = node_sources(run_path, count)
    registry = build_endpoint_registry(sources, run_path)
    assert len(registry["endpoints"]) == count
    for _, path in sources:
        assert render_node_config(path.read_text(), False) == path.read_text()
    (run_path / "endpoint-registry.json").write_text(json.dumps(registry))
    assert verify_endpoint_sources(run_path / "endpoint-registry.json") == registry


@pytest.fixture
def run_path(tmp_path: Path) -> Path:
    """Allocate a fresh local deployment directory without any running services."""
    return tmp_path / "run"


def test_forged_rehashed_endpoint_facts_are_rejected(run_path: Path) -> None:
    """A valid content digest cannot fabricate another port or registered operation."""
    registry = build_endpoint_registry(node_sources(run_path, 3), run_path)
    registry["endpoints"][2]["operations"].append("object.relocate@v1")
    registry["endpoints"][2]["operations"].sort()
    registry["digest"] = registry_digest(
        {key: value for key, value in registry.items() if key != "digest"}
    )
    path = run_path / "endpoint-registry.json"
    path.write_text(json.dumps(registry))
    with pytest.raises(IntegrationError, match="actual Node"):
        verify_endpoint_sources(path)


def test_declared_drone_has_no_manipulation_profile(run_path: Path) -> None:
    """Only real configured relocators enter the relocation readiness subset."""
    node_sources(run_path, 3)
    prepare_live_deployment(run_path, 3)
    registry = json.loads((run_path / "endpoint-registry.json").read_bytes())
    profile = json.loads((run_path / "relocation-registration-profile.json").read_bytes())
    assert registry["endpoints"][2]["robot_type"] == "DJIDrone"
    assert "object.relocate@v1" not in registry["endpoints"][2]["operations"]
    assert [record["agent_id"] for record in profile["agents"]] == [0, 1]
    with pytest.raises(FileExistsError):
        prepare_live_deployment(run_path, 3)


def test_overlapping_ports_and_changed_sources_fail_closed(run_path: Path) -> None:
    """Repeated transport ownership and source drift cannot be treated as valid readiness."""
    sources = node_sources(run_path, 2)
    registry = build_endpoint_registry(sources, run_path)
    path = run_path / "endpoint-registry.json"
    path.write_text(json.dumps(registry))
    second = sources[1][1]
    second.write_text(second.read_text().replace("28102", "28100"))
    with pytest.raises(IntegrationError, match="source changed"):
        verify_endpoint_sources(path)
    with pytest.raises(IntegrationError, match="duplicate"):
        build_endpoint_registry(sources, run_path)
