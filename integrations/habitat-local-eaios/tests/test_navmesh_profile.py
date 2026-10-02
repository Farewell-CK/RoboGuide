"""Prove copied mesh construction preserves abilities and has explicit opt-in limits."""

from __future__ import annotations

import copy
import importlib.util
import math
import struct
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from test_reset_route_support import FakeSettings, _api, _environment, _sources

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.goal_region_navigation import GoalRegionResolutionError  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navmesh_profile import (  # noqa: E402
    copied_agent_settings,
    settings_snapshot,
)
from habitat_local_eaios.reset_route_support import (  # noqa: E402
    ResetRouteProbe,
    build_reset_route_support,
)


@pytest.mark.parametrize(
    ("cell_height", "climb", "enabled", "expected"),
    [
        (0.2, 0.02, False, 0.2),
        (0.2, 0.02, True, 0.01),
        (0.2, 0.8, True, 0.2),
        (0.01, 0.02, True, 0.01),
        (0.2, 0.0, True, 0.2),
    ],
)
def test_step_resolution_preserves_all_robot_abilities(
    cell_height: float, climb: float, enabled: bool, expected: float
) -> None:
    """Avoid zero quantized climb without silently increasing physical abilities."""
    env = _environment()
    config = env.task.actions["agent_0_oracle_nav_action"].config
    config.agent_max_climb = climb
    env.sim.pathfinder.nav_mesh_settings.cell_height = cell_height
    original = copy.deepcopy(vars(env.sim.pathfinder.nav_mesh_settings))
    original_config = copy.deepcopy(vars(config))
    result = copied_agent_settings(env.sim, config, _api(), step_aware=enabled)
    assert result.cell_height == expected
    assert result.agent_max_climb == climb
    assert result.agent_max_slope == config.agent_max_slope
    assert result.agent_height == config.agent_height
    assert result.agent_radius == config.agent_radius + 0.05
    assert result.cell_size == original["cell_size"]
    assert result.include_static_objects is True
    assert vars(env.sim.pathfinder.nav_mesh_settings) == original
    assert vars(config) == original_config
    if enabled and climb > 0:
        assert math.floor(climb / result.cell_height) >= 2


@pytest.mark.parametrize(
    ("cell_height", "climb"), [(0.2, 0.001), (1.0, 0.02), (0.0, 0.02), (0.001, 0.0)]
)
def test_excessive_refinement_fails_without_fallback(cell_height: float, climb: float) -> None:
    """Bound supported voxel refinement before expensive native construction."""
    env = _environment()
    env.sim.pathfinder.nav_mesh_settings.cell_height = cell_height
    config = env.task.actions["agent_0_oracle_nav_action"].config
    config.agent_max_climb = climb
    with pytest.raises(GoalRegionResolutionError, match="supported bounds"):
        copied_agent_settings(env.sim, config, _api(), step_aware=True)
    assert env.builds == []


def _action_module(monkeypatch: pytest.MonkeyPatch, api: Any) -> Any:
    """Load the maintained action against a fake original controller, with no Habitat."""

    class OriginalOracle:
        """Mark inherited physical methods and reject a mutating mesh helper."""

        def _create_pathfinder(self, config: Any) -> None:
            """Expose any unexpected use of the vendor's shared-settings helper."""
            del config
            raise AssertionError("vendor mutation is forbidden for the opt-in profile")

        def step(self) -> str:
            """Represent the unchanged original physical control method."""
            return "original-step"

        def reset(self) -> str:
            """Represent the unchanged original reset path."""
            return "original-reset"

    stubs = {
        name: ModuleType(name)
        for name in (
            "habitat",
            "habitat.core",
            "habitat.core.registry",
            "habitat.tasks",
            "habitat.tasks.rearrange",
            "habitat.tasks.rearrange.actions",
            "habitat.tasks.rearrange.actions.habitat_mas_actions",
            "habitat_sim",
            "numpy",
        )
    }
    vars(stubs["habitat_sim"]).update(vars(api))
    vars(stubs["habitat.core.registry"])["registry"] = SimpleNamespace(
        register_task_action=lambda cls: cls
    )
    vars(stubs["habitat.tasks.rearrange.actions.habitat_mas_actions"])[
        "OracleNavDiffBaseAction"
    ] = OriginalOracle
    for name, stub in stubs.items():
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location(
        "habitat_local_eaios._step_profile_test",
        INTEGRATION_ROOT / "habitat_local_eaios/goal_region_action.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_mesh_matches_observer_without_changing_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct independent identical meshes; retain original control and shared settings."""
    env = _environment()
    config = env.task.actions["agent_0_oracle_nav_action"].config
    config.agent_max_climb = 0.02
    env.sim.pathfinder.nav_mesh_settings.cell_height = 0.2
    original = copy.deepcopy(vars(env.sim.pathfinder.nav_mesh_settings))
    module = _action_module(monkeypatch, _api())
    action = module.StepAwareGoalRegionOracleNavDiffBaseAction.__new__(
        module.StepAwareGoalRegionOracleNavDiffBaseAction
    )
    action._sim, action.config, action._roboguide_selections = env.sim, config, {}
    env.task.actions["agent_0_oracle_nav_action"] = action
    observer = ResetRouteProbe(env, 0, _api())
    active = action._create_pathfinder(config)
    assert active is not observer.pathfinder and active is not env.sim.pathfinder
    assert settings_snapshot(active.nav_mesh_settings) == observer.settings
    assert observer.settings["cell_height"] == 0.01
    assert vars(env.sim.pathfinder.nav_mesh_settings) == original
    assert action.step() == "original-step"
    assert module.GoalRegionOracleNavDiffBaseAction.step is action.step.__func__
    semantic, matrix, profile, sources = _sources()
    with pytest.raises(ValueError, match="differs from the active action"):
        build_reset_route_support(env, semantic, matrix, profile, sources)
    profile.update(
        schema_version="roboguide.habitat-local-how-profile/v0.4",
        navmesh_resolution_profile="step-preserving-cell-height/v0.1",
    )
    document = build_reset_route_support(env, semantic, matrix, profile, sources)
    assert document["records"][0]["navmesh_settings"] == observer.settings
    assert document["records"][0]["status"] == "supported"
    env.sim.recompute_navmesh = lambda *_args: False
    with pytest.raises(GoalRegionResolutionError, match="mesh build failed"):
        action._create_pathfinder(config)


def test_profile_cannot_be_enabled_without_declared_local_how() -> None:
    """Default off and invalid opt-in configurations never silently change execution."""
    common: dict[str, Any] = {
        "config_path": Path("config.yaml"),
        "episode_id": "neutral",
        "agent_id": 0,
        "max_steps": 10,
        "step_period_ms": 0,
    }
    assert CrabAgentBackendConfig(**common).step_aware_navmesh is False
    with pytest.raises(IntegrationError, match="requires goal-region"):
        CrabAgentBackendConfig(**common, step_aware_navmesh=True)


@pytest.mark.parametrize("value", [True, float("nan"), -0.01, "0.02"])
def test_invalid_robot_climb_is_never_coerced(value: Any) -> None:
    """Reject malformed deployment facts before constructing an active mesh."""
    env = _environment()
    config = env.task.actions["agent_0_oracle_nav_action"].config
    config.agent_max_climb = value
    with pytest.raises(GoalRegionResolutionError, match="configuration is unavailable"):
        copied_agent_settings(env.sim, config, _api(), step_aware=True)
    assert env.builds == []


def test_native_float32_boundary_does_not_reject_supported_resolution() -> None:
    """Accept 5 mm represented by native float32 while retaining the refinement limit."""
    env = _environment()
    env.sim.pathfinder.nav_mesh_settings.cell_height = 0.16
    config = env.task.actions["agent_0_oracle_nav_action"].config
    config.agent_max_climb = 0.01
    api = _api()

    class Float32Settings(FakeSettings):
        """Mimic the native settings properties' float32 storage."""

        def __setattr__(self, name: str, value: Any) -> None:
            """Round scalar floats as the compiled Habitat settings class does."""
            if isinstance(value, float):
                value = struct.unpack("f", struct.pack("f", value))[0]
            super().__setattr__(name, value)

    api.NavMeshSettings = Float32Settings
    settings = copied_agent_settings(env.sim, config, api, step_aware=True)
    assert settings.cell_height < 0.005
    assert settings.cell_height >= 0.005 - 1e-9
    assert math.floor(settings.agent_max_climb / settings.cell_height) >= 2
    config.agent_max_climb = 0.0099
    with pytest.raises(GoalRegionResolutionError, match="supported bounds"):
        copied_agent_settings(env.sim, config, api, step_aware=True)
