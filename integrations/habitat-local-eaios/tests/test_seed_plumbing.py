"""Deterministic tests for bridge seed plumbing and initial-state evidence."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.backend import (  # noqa: E402
    HabitatBackendConfig,
    habitat_config_overrides,
    initial_agent_positions,
)
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402


def test_overrides_without_seed_keep_config_default() -> None:
    """A null seed forwards no override so the config default stays visible."""
    assert habitat_config_overrides(None) == [
        "habitat_baselines.num_environments=1",
        "habitat_baselines.eval.video_option=[]",
        "habitat.simulator.concur_render=False",
    ]


@pytest.mark.parametrize("seed", [1, 7, 40, 12345])
def test_overrides_forward_seed_as_habitat_seed(seed: int) -> None:
    """A supplied seed reaches Habitat through the same override the EMOS arm uses."""
    assert habitat_config_overrides(seed)[-1] == f"habitat.seed={seed}"


def test_backend_config_carries_seed_to_both_backends() -> None:
    """The seed is ordinary deployment config shared by every backend path."""
    config = HabitatBackendConfig(
        config_path=Path("habitat.yaml"),
        episode_id="12",
        agent_id=0,
        max_steps=10,
        step_period_ms=0,
        seed=7,
    )
    assert config.seed == 7
    assert (
        CrabAgentBackendConfig(
            config_path=Path("habitat.yaml"),
            episode_id="12",
            agent_id=0,
            max_steps=10,
            step_period_ms=0,
            seed=7,
        ).seed
        == 7
    )


def _stub_habitat_env(positions: dict[int, tuple[float, float, float]]) -> types.SimpleNamespace:
    """Build a minimal habitat environment stub exposing agent base positions."""

    def get_agent_data(agent_id: int) -> types.SimpleNamespace:
        """Return one agent whose articulated base position is the stub value."""
        position = types.SimpleNamespace(base_pos=positions[agent_id])
        return types.SimpleNamespace(articulated_agent=position)

    sim = types.SimpleNamespace(get_agent_data=get_agent_data)
    return types.SimpleNamespace(sim=sim)


def test_initial_agent_positions_records_observed_reset_state() -> None:
    """Positions are read from the live environment, keyed and ordered stably."""
    env = _stub_habitat_env({0: (1.5, 0.0, -2.25), 1: (-0.5, 1.0, 3.0)})
    assert initial_agent_positions(env, (1, 0)) == {
        "0": [1.5, 0.0, -2.25],
        "1": [-0.5, 1.0, 3.0],
    }


def test_initial_agent_positions_converts_numpy_like_arrays() -> None:
    """Array-valued base positions serialize to plain floats for JSON evidence."""

    class Vector:  # Minimal numpy-array stand-in supporting iteration.
        """Emulate a numpy position vector."""

        def __iter__(self) -> Iterator[float]:
            """Iterate component values in fixed order."""
            return iter((0.25, 2.0, 4.75))

    def get_agent_data(agent_id: int) -> types.SimpleNamespace:
        """Return one agent with the vector-valued base position."""
        return types.SimpleNamespace(articulated_agent=types.SimpleNamespace(base_pos=Vector()))

    env = types.SimpleNamespace(sim=types.SimpleNamespace(get_agent_data=get_agent_data))
    assert initial_agent_positions(env, (0,)) == {"0": [0.25, 2.0, 4.75]}
