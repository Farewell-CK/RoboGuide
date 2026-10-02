"""Offline route witnesses, unavailable evidence, and observational isolation."""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import reset_route_support as routes  # noqa: E402
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.navmesh_profile import NAVMESH_FIELDS  # noqa: E402
from habitat_local_eaios.preassignment_feasibility import preassignment_digest  # noqa: E402
from habitat_local_eaios.shared_world import SharedEmosStage2Runtime  # noqa: E402


class FakeSettings:
    """Expose the installed Habitat-Sim scalar layout with independent storage."""

    def __init__(self) -> None:
        """Set distinct template values to reveal accidental shared mutation."""
        for name in NAVMESH_FIELDS:
            setattr(
                self,
                name,
                False if name.startswith("filter_") or name == "include_static_objects" else 1.0,
            )


class FakePathFinder:
    """Return controlled routes while forbidding vertex scans and RNG calls."""

    def __init__(self) -> None:
        """Retain independent mesh settings, queries, and a route-failure switch."""
        self.nav_mesh_settings = FakeSettings()
        self.is_loaded = True
        self.queries: list[tuple[float, ...]] = []
        self.route_found = True
        self.raise_query = False
        self.invalid_snap = False

    def snap_point(self, point: tuple[float, ...], island: int | None = None) -> tuple[float, ...]:
        """Return a deterministic point; invalid snap never invokes random fallback."""
        del island
        return (float("nan"), 0.0, 0.0) if self.invalid_snap else point

    def find_path(self, path: Any) -> bool:
        """Record the exact target and return a real or absent static path."""
        self.queries.append(tuple(path.requested_end))
        if self.raise_query:
            raise RuntimeError("route query failed")
        path.points = [path.requested_start, path.requested_end]
        path.geodesic_distance = 3.0
        return self.route_found

    def build_navmesh_vertices(self) -> None:
        """Fail if a preflight allocates an unbounded full navmesh vertex array."""
        raise AssertionError("vertex scan is outside the probe budget")


class GoalRegionOracleNavDiffBaseAction:
    """Expose only the action's immutable configuration and untouched cache."""

    def __init__(self) -> None:
        """Declare a deterministic, vendor-compatible navigation profile."""
        self.config = SimpleNamespace(
            agent_radius=0.4,
            agent_height=1.5,
            agent_max_climb=0.2,
            agent_max_slope=45.0,
            spawn_max_dist_to_obj=-1,
            dist_thresh=0.5,
        )
        self.pathfinder = None
        self._targets = {9: ("existing", "cache")}

    def _get_target_for_idx(self, index: int) -> None:
        """Forbid target selection before the actual skill executes."""
        raise AssertionError(f"mutating action query {index}")

    def _create_pathfinder(self, config: Any) -> None:
        """Forbid the vendor helper that mutates shared settings."""
        raise AssertionError(f"mutating vendor mesh build {config}")


def _environment(*, found: bool = True) -> Any:
    """Create a read-only world whose act, step, reset, and safe-snap routes fail."""
    native = FakePathFinder()
    action = GoalRegionOracleNavDiffBaseAction()
    builds: list[FakePathFinder] = []

    def recompute(pathfinder: FakePathFinder, settings: Any) -> bool:
        """Populate only the caller's detached mesh and capture copied parameters."""
        assert pathfinder is not native and settings is not native.nav_mesh_settings
        pathfinder.nav_mesh_settings = settings
        pathfinder.route_found = found
        builds.append(pathfinder)
        return True

    def forbidden(*args: Any, **kwargs: Any) -> None:
        """Reject every physical or random interface in the observation path."""
        del args, kwargs
        raise AssertionError("observation touched execution or RNG")

    agent = SimpleNamespace(
        base_pos=(0.0, 0.0, 0.0),
        base_rot=0.4,
        base_transformation=SimpleNamespace(translation=(0.0, 0.0, 0.0)),
    )
    return SimpleNamespace(
        task=SimpleNamespace(
            actions={"agent_0_oracle_nav_action": action},
            pddl_problem=SimpleNamespace(
                get_entity=lambda name: name,
                sim_info=SimpleNamespace(
                    get_entity_pos=lambda name: (1.0, 0.0, 0.0), robot_at_thresh=2.0
                ),
            ),
        ),
        sim=SimpleNamespace(
            pathfinder=native,
            _largest_indoor_island_idx=7,
            get_agent_data=lambda index: SimpleNamespace(articulated_agent=agent),
            recompute_navmesh=recompute,
            safe_snap_point=forbidden,
            step=forbidden,
            reset=forbidden,
        ),
        actor=SimpleNamespace(act=forbidden),
        gym_env=SimpleNamespace(step=forbidden, reset=forbidden),
        builds=builds,
    )


def _api() -> Any:
    """Provide an isolated compiled-library facade without importing Habitat."""
    return SimpleNamespace(
        __version__="offline-test",
        NavMeshSettings=FakeSettings,
        PathFinder=FakePathFinder,
        ShortestPath=SimpleNamespace,
    )


def _sources() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Freeze one neutral goal and one already-reset endpoint for snapshot tests."""
    semantic = {
        "goal": {"kind": "predicate", "name": "any_at", "arguments": ["goal"]},
        "digest": "sha256:" + "a" * 64,
    }
    matrix = {
        "identity": {"semantic_evidence_digest": semantic["digest"], "episode_reset_count": 1},
        "digest": "sha256:" + "b" * 64,
        "initial_agent_positions": {"0": [0.0, 0.0, 0.0]},
        "records": [{"agent_id": 0, "node_id": "endpoint", "destination": "goal"}],
    }
    profile = {
        "navigation_point_resolver": "official-any-at-agent-navmesh/v0.1",
        "reset_route_support_enabled": True,
    }
    return semantic, matrix, profile, {"modules": {}}


def test_probe_preserves_live_action_settings_and_rng() -> None:
    """A static witness must not prime caches, move an agent, or consume RNG."""
    env = _environment()
    action = env.task.actions["agent_0_oracle_nav_action"]
    before = copy.deepcopy(vars(env.sim.pathfinder.nav_mesh_settings))
    rng_before = random.getstate()
    probe = routes.ResetRouteProbe(env, 0, _api())
    record = probe.observe("goal")
    assert record["status"] == "supported"
    assert record["selection"]["source"] == "original"
    assert record["selection"]["path_queries"] == 1
    assert vars(env.sim.pathfinder.nav_mesh_settings) == before
    assert probe.settings["agent_radius"] == 0.45
    assert probe.settings["include_static_objects"] is True
    assert env.sim.pathfinder.queries == []
    assert action.pathfinder is None and action._targets == {9: ("existing", "cache")}
    assert random.getstate() == rng_before


def test_probe_miss_records_budget_not_physical_impossibility() -> None:
    """No initial-candidate path remains a bounded miss with exact counters."""
    env = _environment(found=False)
    probe = routes.ResetRouteProbe(env, 0, _api())
    record = probe.observe("goal")
    assert record["status"] == "not_found"
    assert record["selection"] is None
    assert record["search"] == {
        "reason_code": "candidates_exhausted",
        "vertices_seen": 0,
        "candidates_in_region": 0,
        "path_queries": 1,
        "search_truncated": False,
    }
    assert len(probe.pathfinder.queries) <= routes.PATH_QUERIES_PER_RECORD


def test_geometry_is_independently_opted_in_and_reuses_endpoint_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-off execution never exports geometry; opt-in never adds path queries."""
    from habitat_local_eaios.navmesh_region import RegionBudget

    env = _environment(found=False)
    probe = routes.ResetRouteProbe(env, 0, _api())
    exports: list[int] = []

    def vertices(component: int) -> list[tuple[float, float, float]]:
        """Count one bounded detached export shared by both observations."""
        exports.append(component)
        return [(-1.0, 0.0, -1.0), (1.0, 0.0, -1.0), (0.0, 0.0, 1.0)]

    probe.pathfinder.get_island = lambda start: 0
    probe.pathfinder.build_navmesh_vertices = vertices
    probe.pathfinder.build_navmesh_vertex_indices = lambda component: [0, 1, 2]
    ordinary = probe.observe("goal")
    assert exports == [] and "region_analysis" not in ordinary
    env.task.pddl_problem.sim_info.get_entity_pos = lambda name: (20.0, 0.0, 0.0)
    observation = probe.observe("goal")
    before = len(probe.pathfinder.queries)
    region = probe.observe_region(observation, RegionBudget())
    assert region["status"] == "disjoint" and region["complete"] is True
    assert probe.observe_region(observation, RegionBudget())["mesh_digest"] == region["mesh_digest"]
    assert exports == [0] and len(probe.pathfinder.queries) == before
    assert env.task.actions["agent_0_oracle_nav_action"].pathfinder is None
    monkeypatch.setattr(routes, "ResetRouteProbe", lambda environment, agent_id: probe)
    semantic, matrix, profile, runtime = _sources()
    baseline = routes.build_reset_route_support(env, semantic, matrix, profile, runtime)
    assert baseline["schema_version"] == routes.RESET_ROUTE_SUPPORT_SCHEMA
    assert "region_analysis" not in baseline["records"][0]
    profile["reset_route_geometry_enabled"] = True
    enriched = routes.build_reset_route_support(env, semantic, matrix, profile, runtime)
    assert enriched["schema_version"] == routes.GEOMETRY_ROUTE_SUPPORT_SCHEMA
    assert enriched["records"][0]["region_analysis"]["status"] == "disjoint"
    assert enriched["probe"]["region_analysis"]["is_node_exclusion"] is False


def test_geometry_failure_does_not_overwrite_route_outcome() -> None:
    """An export failure remains unknown while the original route result survives."""
    from habitat_local_eaios.navmesh_region import RegionBudget

    env = _environment()
    probe = routes.ResetRouteProbe(env, 0, _api())
    record = probe.observe("goal")
    before = copy.deepcopy(record)
    region = probe.observe_region(record, RegionBudget())
    assert region["status"] == "unknown" and region["complete"] is False
    assert region["minimum_reference_distance_m"] is None
    assert record == before and record["status"] == "supported"


def test_geometry_requires_existing_observer_configuration() -> None:
    """Geometry cannot accidentally activate on the default execution profile."""
    assert (
        CrabAgentBackendConfig(
            config_path=Path("unused"), episode_id="x", agent_id=0, max_steps=3, step_period_ms=0
        ).reset_route_geometry
        is False
    )
    with pytest.raises(IntegrationError, match="requires reset route support"):
        CrabAgentBackendConfig(
            config_path=Path("unused"),
            episode_id="x",
            agent_id=0,
            max_steps=3,
            step_period_ms=0,
            reset_route_geometry=True,
        )


def test_adjacent_floor_witness_uses_the_original_goal_tolerance() -> None:
    """Different goal height can have a valid same-floor route without teleporting."""
    env = _environment()
    env.task.pddl_problem.sim_info.get_entity_pos = lambda name: (1.0, -1.5, 0.0)
    env.sim.pathfinder.snap_point = lambda point, island: (1.0, -4.0, 0.0)
    probe = routes.ResetRouteProbe(env, 0, _api())
    record = probe.observe("goal")
    assert record["status"] == "supported"
    assert record["goal_center"] == [1.0, -1.5, 0.0]
    assert record["selection"]["point"] == [1.0, 0.0, 0.0]
    assert record["selection"]["source"] == "agent-navmesh"
    assert record["selection"]["estimated_stop_envelope_distance_m"] < 2.0
    assert env.sim.get_agent_data(0).articulated_agent.base_pos == (0.0, 0.0, 0.0)


def test_missing_projected_geometry_is_unavailable_not_an_exhaustive_miss() -> None:
    """A failed snap is a missing observation, even after an original route miss."""
    env = _environment(found=False)
    probe = routes.ResetRouteProbe(env, 0, _api())
    probe.pathfinder.invalid_snap = True
    record = probe.observe("goal")
    assert record["status"] == "unavailable"
    assert record["search"] is None


@pytest.mark.parametrize("fault", ["query", "snap", "threshold", "rotation"])
def test_unavailable_is_not_a_search_miss(fault: str) -> None:
    """Broken observations and API calls retain errors without false negatives."""
    env = _environment()
    probe = routes.ResetRouteProbe(env, 0, _api())
    if fault == "query":
        probe.pathfinder.raise_query = True
    elif fault == "snap":
        env.sim.pathfinder.invalid_snap = True
    elif fault == "threshold":
        env.task.pddl_problem.sim_info.robot_at_thresh = float("nan")
    else:
        env.sim.get_agent_data(0).articulated_agent.base_rot = float("inf")
    record = probe.observe("goal")
    assert record["status"] == "unavailable"
    assert record["error_type"]
    assert record["selection"] is None and record["search"] is None


def test_snapshot_records_initialization_failure_and_later_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable mesh and moved starts are explicit, never supported witnesses."""
    api = ModuleType("habitat_sim")
    vars(api).update(vars(_api()))
    monkeypatch.setitem(sys.modules, "habitat_sim", api)
    env = _environment()
    env.sim.pathfinder.nav_mesh_settings.unrecognized = 1.0
    semantic, matrix, profile, sources = _sources()
    document = routes.build_reset_route_support(env, semantic, matrix, profile, sources)
    assert document["records"][0]["reason_code"] == "probe_initialization_error"
    assert document["records"][0]["status"] == "unavailable"
    assert env.builds == []
    del env.sim.pathfinder.nav_mesh_settings.unrecognized
    matrix["initial_agent_positions"]["0"] = [9.0, 0.0, 0.0]
    document = routes.build_reset_route_support(env, semantic, matrix, profile, sources)
    assert document["records"][0]["reason_code"] == "reset_state_changed"
    assert document["records"][0]["selection"] is None


def test_snapshot_freezes_identity_and_bounds_without_excluding_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All goal/endpoint records bind the reset and active Local How digests."""
    api = ModuleType("habitat_sim")
    vars(api).update(vars(_api()))
    monkeypatch.setitem(sys.modules, "habitat_sim", api)
    env = _environment(found=False)
    semantic, matrix, profile, sources = _sources()
    before = copy.deepcopy(matrix)
    document = routes.build_reset_route_support(env, semantic, matrix, profile, sources)
    assert document["identity"]["preassignment_digest"] == matrix["digest"]
    assert document["identity"]["local_how_digest"] == preassignment_digest(profile)
    assert document["purpose"] == "diagnostic_only"
    assert document["probe"]["is_node_exclusion"] is False
    assert document["probe"]["navmesh_vertex_search_performed"] is False
    assert document["records"][0]["status"] == "not_found"
    assert matrix == before
    matrix["records"] = [
        {"agent_id": 0, "node_id": "endpoint", "destination": "goal"} for _ in range(500)
    ]
    # Duplicate operation records do not increase the unique endpoint budget.
    assert (
        len(routes.build_reset_route_support(env, semantic, matrix, profile, sources)["records"])
        == 1
    )
    semantic["goal"] = {
        "kind": "logical",
        "operator": "and",
        "quantifier": None,
        "operands": [
            {"kind": "predicate", "name": "any_at", "arguments": [f"goal-{index}"]}
            for index in range(129)
        ],
    }
    env.builds.clear()
    document = routes.build_reset_route_support(env, semantic, matrix, profile, sources)
    assert document["scope_reason"] == "record_budget" and document["records"] == []
    assert env.builds == []


@pytest.mark.parametrize("fault", ["serialization", "write", "native_source"])
def test_route_archive_write_failure_does_not_escape_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Optional JSON/write errors remain archival gaps and leave execution untouched."""
    runtime = SharedEmosStage2Runtime(
        CrabAgentBackendConfig(
            config_path=tmp_path / "unused",
            episode_id="episode",
            agent_id=0,
            max_steps=10,
            step_period_ms=0,
            evidence_dir=tmp_path,
        ),
        (0, 1),
    )
    semantic, matrix, profile, sources = _sources()
    (tmp_path / "local-how-profile.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runtime-source-manifest.json").write_text('{"modules": {}}', encoding="utf-8")
    attempted: list[int] = []

    def snapshot(*args: Any) -> dict[str, Any]:
        """Exercise the actual JSON writer only after native attribution succeeds."""
        del args
        attempted.append(1)
        return (
            {"unserializable": object()} if fault == "serialization" else {"diagnostic_only": True}
        )

    def native_source(modules: tuple[str, ...]) -> dict[str, Any]:
        """Keep a native-fingerprint read failure inside optional archival isolation."""
        del modules
        if fault == "native_source":
            raise OSError("native source read failed")
        return {"modules": {}}

    original_write = runtime._write_json

    def write_json(name: str, document: dict[str, Any]) -> None:
        """Fail just the final diagnostic write while preserving the generic manifest."""
        if name == "reset-route-support.json" and fault == "write":
            raise OSError("diagnostic disk write failed")
        original_write(name, document)

    monkeypatch.setattr(
        "habitat_local_eaios.shared_world.build_reset_route_support",
        snapshot,
    )
    monkeypatch.setattr(
        "habitat_local_eaios.shared_world.build_runtime_source_manifest",
        native_source,
    )
    monkeypatch.setattr(runtime, "_write_json", write_json)
    runtime._record_reset_route_support(_environment(), semantic, matrix)
    assert attempted == ([] if fault == "native_source" else [1])
    assert not (tmp_path / "reset-route-support.json").exists()
    assert runtime._prepared_observations is None
    del profile, sources


def test_route_diagnostics_default_off_and_requires_matching_profile(tmp_path: Path) -> None:
    """Explicit probing cannot silently select a different Local How implementation."""
    common: dict[str, Any] = {
        "config_path": tmp_path / "unused",
        "episode_id": "episode",
        "agent_id": 0,
        "max_steps": 10,
        "step_period_ms": 0,
    }
    assert CrabAgentBackendConfig(**common).reset_route_support is False
    with pytest.raises(IntegrationError, match="requires goal-region"):
        CrabAgentBackendConfig(**common, reset_route_support=True)
