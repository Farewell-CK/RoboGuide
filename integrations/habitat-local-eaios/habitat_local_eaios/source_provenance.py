"""Declare source coverage offline and observe actual runtime source files separately."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SOURCE_COVERAGE_SCHEMA = "roboguide.local-eaios-source-coverage/v0.1"
_COMMON_MODULES = (
    "habitat.tasks.rearrange.actions.habitat_mas_actions",
    "habitat.tasks.rearrange.actions.oracle_nav_action",
    "habitat.tasks.rearrange.actions.actions",
    "habitat.tasks.rearrange.actions.reset_action",
    "habitat_sim",
    "habitat_sim._ext.habitat_sim_bindings",
    "habitat_baselines.rl.hrl.hl.llm_policy",
    "habitat_baselines.rl.hrl.skills.wait",
    "habitat_baselines.rl.hrl.skills.reset",
    "habitat_baselines.rl.multi_agent.multi_agent_access_mgr",
    "habitat_baselines.rl.multi_agent.multi_llm_policy",
    "habitat_mas.agents.crab_agent",
    "habitat_mas.scene_graph.utils",
    "habitat_mas.tasks.habitat_mas_sensors",
    "habitat_mas.utils.models",
    "habitat_local_eaios.diagnostics",
    "habitat_local_eaios.native_usage_observer",
    "habitat_local_eaios.observation_read_cache",
    "habitat_local_eaios.source_provenance",
)
_CONTROLLED_MODULES = (
    "habitat_local_eaios.stage2_feedback",
    "habitat_local_eaios.execution_lifecycle",
    "habitat_local_eaios.stage2_context",
    "habitat_local_eaios.reset_completion",
    "habitat_local_eaios.idle_endpoint",
    "habitat_local_eaios.relocation_capability",
    "habitat_local_eaios.stage2_contract",
    "habitat_local_eaios.operation_admission",
    "habitat_local_eaios.emos_stage2",
    "habitat_local_eaios.goal_region_action",
    "habitat_local_eaios.goal_region_navigation",
    "habitat_local_eaios.navmesh_profile",
    "habitat_local_eaios.reset_route_support",
    "habitat_local_eaios.navmesh_region",
    "habitat_local_eaios.shared_world",
)


def runtime_source_modules(
    arm: str, *, relocation_binding: bool = False, spatial_arrival: bool = False
) -> tuple[str, ...]:
    """Keep producer coverage in one place without importing or executing the runtime.

    Native execution never acquires controlled Local How merely by declaring
    original action/skill source files. Conditional controlled observations stay
    conditional; common original reset sources are recorded by both arms.
    """
    if arm not in {"emos", "roboguide"}:
        raise ValueError("unknown runtime source arm")
    modules = list(_COMMON_MODULES)
    if arm == "roboguide":
        modules.extend(_CONTROLLED_MODULES)
        if relocation_binding:
            modules.extend(
                (
                    "habitat_local_eaios.relocation_completion",
                    "habitat_local_eaios.navigation_dispatch",
                    "habitat_local_eaios.live_bindings",
                )
            )
        if spatial_arrival:
            modules.extend(
                (
                    "habitat_local_eaios.spatial_navigation",
                    "habitat_local_eaios.spatial_navigation_action",
                    "habitat.gym.gym_wrapper",
                    "habitat_local_eaios.navigation_preparation",
                )
            )
    return tuple(dict.fromkeys(modules))


class SourceProvenanceError(RuntimeError):
    """Report a runtime module whose loaded source cannot be proven."""


def build_runtime_source_manifest(module_names: tuple[str, ...]) -> dict[str, object]:
    """Digest exact loaded module files, including explicitly requested extensions.

    Raises:
        SourceProvenanceError: If a requested module has no readable filesystem
            source. Controlled executions fail closed instead of claiming an
            EMOS checkout that the Python import system did not actually load.
    """
    modules: dict[str, object] = {}
    for module_name in module_names:
        specification = importlib.util.find_spec(module_name)
        origin = None if specification is None else specification.origin
        if origin is None or origin in {"built-in", "frozen"}:
            raise SourceProvenanceError(f"runtime module {module_name!r} has no source path")
        path = Path(origin).resolve()
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SourceProvenanceError(
                f"runtime module {module_name!r} source is unreadable: {error}"
            ) from error
        modules[module_name] = {
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return {
        "schema_version": "roboguide.local-eaios-source-provenance/v0.1",
        "python_executable": str(Path(sys.executable).resolve()),
        "modules": modules,
    }


def main(argv: list[str] | None = None) -> None:
    """Describe guaranteed emission coverage using only the worker's standard library.

    Direct script execution also works with isolated Python and no import path,
    credential, simulator package, model client or environment construction.
    The declaration is not an observed runtime identity manifest.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true", required=True)
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "schema_version": SOURCE_COVERAGE_SCHEMA,
                "basis": "declared-emission-coverage-not-runtime-observation",
                "arms": {arm: list(runtime_source_modules(arm)) for arm in ("emos", "roboguide")},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
