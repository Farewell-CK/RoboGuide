"""Copy agent NavMesh settings and optionally preserve declared step resolution.

The opt-in profile refines vertical voxel size without enlarging robot abilities.
It does not move an agent, query a route, sample RNG, or change a shared mesh.
Native mesh construction still belongs to Habitat and has deployment costs.
"""

from __future__ import annotations

import math
from typing import Any

from .goal_region_navigation import GoalRegionResolutionError

STEP_AWARE_PROFILE = "step-preserving-cell-height/v0.1"
MIN_CELL_HEIGHT_M = 0.005
MAX_VERTICAL_REFINEMENT = 32.0
CELL_HEIGHT_ROUNDING_M = 1e-9
NAVMESH_FIELDS = frozenset(
    (
        "agent_height",
        "agent_max_climb",
        "agent_max_slope",
        "agent_radius",
        "cell_height",
        "cell_size",
        "detail_sample_dist",
        "detail_sample_max_error",
        "edge_max_error",
        "edge_max_len",
        "filter_ledge_spans",
        "filter_low_hanging_obstacles",
        "filter_walkable_low_height_spans",
        "include_static_objects",
        "region_merge_size",
        "region_min_size",
        "verts_per_poly",
    )
)


def copied_agent_settings(sim: Any, config: Any, api: Any, *, step_aware: bool = False) -> Any:
    """Prepare a detached, exact agent profile without mutating vendor settings.

    The existing Oracle collision buffer and static-object setting are retained.
    When enabled, vertical cells are at most half the positive declared climb,
    avoiding its truncation to zero by Recast. Zero climb requires no refinement.
    Unsupported layouts, invalid scalars, or excessive refinement fail explicitly;
    no fallback silently changes capability or the declared Local How profile.
    """
    if getattr(sim, "navmesh_visualization", False):
        raise GoalRegionResolutionError("navmesh visualization prevents isolated mesh construction")
    template = sim.pathfinder.nav_mesh_settings
    fields = {
        name
        for name in dir(template)
        if not name.startswith("_") and not callable(getattr(template, name))
    }
    if fields != NAVMESH_FIELDS:
        raise GoalRegionResolutionError("unsupported navmesh settings layout")
    settings = api.NavMeshSettings()
    for name in sorted(fields):
        value = getattr(template, name)
        flag = name.startswith("filter_") or name == "include_static_objects"
        if (flag and not isinstance(value, bool)) or (
            not flag
            and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            )
        ):
            raise GoalRegionResolutionError("navmesh settings contain unavailable scalars")
        setattr(settings, name, value)
    for name in ("agent_radius", "agent_height", "agent_max_climb", "agent_max_slope"):
        value = getattr(config, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise GoalRegionResolutionError("agent navmesh configuration is unavailable")
        setattr(settings, name, float(value))
    settings.agent_radius += 0.05
    settings.include_static_objects = True
    if step_aware:
        original = float(settings.cell_height)
        climb = float(settings.agent_max_climb)
        refined = min(original, climb / 2.0) if climb > 0 else original
        if (
            original <= 0
            or refined < MIN_CELL_HEIGHT_M - CELL_HEIGHT_ROUNDING_M
            or original / refined > MAX_VERTICAL_REFINEMENT * (1 + 1e-6)
        ):
            raise GoalRegionResolutionError(
                "step-aware navmesh refinement exceeds supported bounds"
            )
        settings.cell_height = refined
    return settings


def settings_snapshot(settings: Any) -> dict[str, Any]:
    """Expose the bounded scalar profile actually given to mesh construction."""
    return {name: getattr(settings, name) for name in sorted(NAVMESH_FIELDS)}
