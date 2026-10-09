"""Command-line entry point for the Habitat Local EAIOS bridge."""

from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

from .adapter import HabitatLocalAdapter
from .backend import HabitatBackendConfig, HabitatMobilityBackend
from .crabagent_backend import SUBTASK_MODES, CrabAgentBackendConfig, CrabAgentMobilityBackend
from .http_service import HabitatBridgeServer
from .process_backend import HabitatProcessBackend
from .shared_world import (
    NodeEndpoint,
    ProcessWorldService,
    SharedWorldCoordinator,
)
from .spatial_feasibility import load_spatial_profile_snapshot
from .store import ExecutionStore

_BACKENDS = ("direct-oracle", "emos-crabagent", "shared-emos-stage2")


def _arguments() -> argparse.Namespace:
    """Parse fixed deployment choices without accepting per-request Local How."""
    parser = argparse.ArgumentParser(description="RoboGuide Habitat Local EAIOS bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28100)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=_BACKENDS,
        default="direct-oracle",
        help="deployment-owned Local How selection; never chosen by plans or requests",
    )
    parser.add_argument("--habitat-config", type=Path, required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--agent-id", type=int, default=0)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="simulator seed forwarded as habitat.seed; None keeps the config default",
    )
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--step-period-ms", type=int, default=20)
    parser.add_argument(
        "--progress-directory",
        type=Path,
        default=None,
        help="opt-in bounded read-only navigation progress; absent keeps sampling disabled",
    )
    parser.add_argument(
        "--video-path",
        type=Path,
        default=None,
        help="optional RGB evidence MP4; disabled unless explicitly supplied",
    )
    parser.add_argument(
        "--video-fps",
        type=int,
        default=30,
        help="RGB evidence playback rate; does not change simulator stepping",
    )
    parser.add_argument(
        "--live-preview-path",
        type=Path,
        default=None,
        help="optional atomically replaced operator-view JPEG; disabled by default",
    )
    parser.add_argument(
        "--live-preview-period-steps",
        type=int,
        default=5,
        help="bounded preview sampling period in simulator steps",
    )
    parser.add_argument("--initialization-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--subtask-mode",
        choices=SUBTASK_MODES,
        default="natural-objective",
        help="CrabAgent backends only: natural-objective (Protocol B) or entity-grounded",
    )
    parser.add_argument(
        "--relocation-profile",
        type=Path,
        default=None,
        help="shared relocation: trusted frozen Node registration snapshot, verified before spawn",
    )
    parser.add_argument(
        "--enable-relocation",
        action="store_true",
        help="opt in only when loaded EMOS action/skill readiness proves object relocation support",
    )
    parser.add_argument(
        "--relocation-completion-binding",
        action="store_true",
        help="opt in to exact-object distance and observed release for original place completion",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="LLM backends only: directory for scene/trace/token evidence",
    )
    parser.add_argument(
        "--run-id",
        default="unbound",
        help="shared backend only: immutable evaluation run identity for semantic evidence",
    )
    parser.add_argument(
        "--port-b",
        type=int,
        default=None,
        help="shared backend only: second Node's loopback port",
    )
    parser.add_argument(
        "--state-db-b",
        type=Path,
        default=None,
        help="shared backend only: second Node's durable execution store",
    )
    parser.add_argument(
        "--agent-b-id",
        type=int,
        default=None,
        help="shared backend only: second Node's Habitat agent id",
    )
    parser.add_argument(
        "--pair-wait-s",
        type=float,
        default=300.0,
        help="shared backend only: bounded episode-start synchronization window",
    )
    parser.add_argument(
        "--spatial-profile",
        type=Path,
        default=None,
        help="shared backend: digest-bound Node capability snapshot",
    )
    parser.add_argument(
        "--goal-aware-navigation-arrival",
        action="store_true",
        help="shared backend: require the actual reference inside the exact goal region to finish",
    )
    parser.add_argument(
        "--spatial-navigation-arrival",
        action="store_true",
        help="shared backend: require spatial arrival before facing the assigned entity",
    )
    parser.add_argument(
        "--step-aware-navmesh",
        action="store_true",
        help="shared backend: preserve declared climb in copied vertical NavMesh resolution",
    )
    parser.add_argument(
        "--goal-region-navigation",
        action="store_true",
        help="shared backend: opt in to agent-navmesh target selection for official any_at goals",
    )
    parser.add_argument(
        "--reset-route-support",
        action="store_true",
        help="shared backend: observe bounded reset routes without excluding Node candidates",
    )
    parser.add_argument(
        "--reset-route-geometry",
        action="store_true",
        help="shared backend: add bounded static component geometry to reset route evidence",
    )
    parser.add_argument(
        "--retain-stopped-session",
        action="store_true",
        help="shared backend: retain cancelled Group worlds for coordinated exact attempts",
    )
    return parser.parse_args()


def _run_shared_world(arguments: argparse.Namespace) -> None:
    """Serve two Node endpoints over exactly one shared Habitat world."""
    if arguments.port_b is None or arguments.state_db_b is None or arguments.agent_b_id is None:
        raise SystemExit(
            "the shared-emos-stage2 backend requires --port-b, --state-db-b, --agent-b-id"
        )
    if arguments.evidence_dir is None:
        raise SystemExit("the shared-emos-stage2 backend requires --evidence-dir")
    if arguments.spatial_profile is None:
        raise SystemExit("the shared-emos-stage2 backend requires --spatial-profile")
    if arguments.enable_relocation and arguments.relocation_profile is None:
        raise SystemExit("shared relocation requires --relocation-profile from actual Node configs")
    if not arguments.enable_relocation and arguments.relocation_profile is not None:
        raise SystemExit("--relocation-profile requires --enable-relocation")
    if arguments.agent_id == arguments.agent_b_id:
        raise SystemExit("shared-world endpoints must map to distinct Habitat agents")
    config = CrabAgentBackendConfig(
        config_path=arguments.habitat_config,
        episode_id=arguments.episode_id,
        agent_id=arguments.agent_id,
        max_steps=arguments.max_steps,
        step_period_ms=arguments.step_period_ms,
        seed=arguments.seed,
        video_path=arguments.video_path,
        video_fps=arguments.video_fps,
        live_preview_path=arguments.live_preview_path,
        live_preview_period_steps=arguments.live_preview_period_steps,
        subtask_mode=arguments.subtask_mode,
        evidence_dir=arguments.evidence_dir,
        run_id=arguments.run_id,
        spatial_capabilities=load_spatial_profile_snapshot(arguments.spatial_profile),
        spatial_profile_path=arguments.spatial_profile,
        goal_region_navigation=arguments.goal_region_navigation,
        step_aware_navmesh=arguments.step_aware_navmesh,
        spatial_navigation_arrival=arguments.spatial_navigation_arrival,
        goal_aware_navigation_arrival=arguments.goal_aware_navigation_arrival,
        reset_route_support=arguments.reset_route_support,
        reset_route_geometry=arguments.reset_route_geometry,
        retain_stopped_session=arguments.retain_stopped_session,
        enable_relocation=arguments.enable_relocation,
        relocation_profile_path=arguments.relocation_profile,
        relocation_completion_binding=arguments.relocation_completion_binding,
        progress_directory=arguments.progress_directory,
    )
    world = ProcessWorldService(config, (arguments.agent_id, arguments.agent_b_id))
    coordinator = SharedWorldCoordinator(
        world,
        arguments.pair_wait_s,
        arguments.evidence_dir,
        retain_stopped_session=arguments.retain_stopped_session,
        max_steps=arguments.max_steps,
    )
    endpoint_a = NodeEndpoint(
        "node-a",
        arguments.agent_id,
        ExecutionStore(arguments.state_db),
        coordinator,
        progress_directory=arguments.progress_directory,
        retain_stopped_session=arguments.retain_stopped_session,
        enable_relocation=arguments.enable_relocation,
    )
    endpoint_b = NodeEndpoint(
        "node-b",
        arguments.agent_b_id,
        ExecutionStore(arguments.state_db_b),
        coordinator,
        progress_directory=arguments.progress_directory,
        retain_stopped_session=arguments.retain_stopped_session,
        enable_relocation=arguments.enable_relocation,
    )
    server_a = HabitatBridgeServer((arguments.host, arguments.port), endpoint_a)
    server_b = HabitatBridgeServer((arguments.host, arguments.port_b), endpoint_b)
    for server in (server_a, server_b):
        threading.Thread(target=server.serve_forever, args=(0.2,), daemon=True).start()
        logging.info("Habitat Local EAIOS ready at http://%s:%s", *server.server_address)
    try:
        threading.Event().wait()
    finally:
        server_a.server_close()
        server_b.server_close()
        coordinator.shutdown()


def main() -> None:
    """Initialize the configured Habitat backend before exposing workflow routes."""
    arguments = _arguments()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Habitat Local EAIOS must bind a loopback host")
    if arguments.backend == "emos-crabagent" and arguments.evidence_dir is None:
        raise SystemExit("the emos-crabagent backend requires --evidence-dir")
    if arguments.enable_relocation and arguments.backend != "shared-emos-stage2":
        raise SystemExit("relocation readiness requires the shared-emos-stage2 backend")
    if arguments.goal_region_navigation and arguments.backend != "shared-emos-stage2":
        raise SystemExit("goal-region navigation requires the shared EMOS Stage2 backend")
    if arguments.step_aware_navmesh and not arguments.goal_region_navigation:
        raise SystemExit("step-aware navmesh requires goal-region navigation")
    if arguments.spatial_navigation_arrival and not arguments.step_aware_navmesh:
        raise SystemExit("spatial navigation arrival requires step-aware navmesh")
    if arguments.goal_aware_navigation_arrival and not arguments.spatial_navigation_arrival:
        raise SystemExit("goal-aware navigation arrival requires spatial navigation arrival")
    if arguments.relocation_completion_binding and (
        not arguments.enable_relocation or arguments.backend != "shared-emos-stage2"
    ):
        raise SystemExit("relocation completion binding requires shared relocation")
    if arguments.retain_stopped_session and arguments.backend != "shared-emos-stage2":
        raise SystemExit("retained stopped sessions require the shared EMOS Stage2 backend")
    if arguments.reset_route_support and (
        arguments.backend != "shared-emos-stage2" or not arguments.goal_region_navigation
    ):
        raise SystemExit(
            "reset route support requires shared EMOS Stage2 and goal-region navigation"
        )
    if arguments.reset_route_geometry and not arguments.reset_route_support:
        raise SystemExit("reset route geometry requires reset route support")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if arguments.backend == "shared-emos-stage2":
        _run_shared_world(arguments)
        return
    common = {
        "config_path": arguments.habitat_config,
        "episode_id": arguments.episode_id,
        "agent_id": arguments.agent_id,
        "max_steps": arguments.max_steps,
        "step_period_ms": arguments.step_period_ms,
        "seed": arguments.seed,
        "video_path": arguments.video_path,
        "video_fps": arguments.video_fps,
        "live_preview_path": arguments.live_preview_path,
        "live_preview_period_steps": arguments.live_preview_period_steps,
        "progress_directory": arguments.progress_directory,
    }
    if arguments.backend == "emos-crabagent":
        config: HabitatBackendConfig = CrabAgentBackendConfig(
            **common,
            subtask_mode=arguments.subtask_mode,
            evidence_dir=arguments.evidence_dir,
            run_id=arguments.run_id,
            goal_region_navigation=arguments.goal_region_navigation,
            enable_relocation=arguments.enable_relocation,
        )
        backend_class: type = CrabAgentMobilityBackend
    else:
        config = HabitatBackendConfig(**common)
        backend_class = HabitatMobilityBackend
    store = ExecutionStore(arguments.state_db)
    adapter = HabitatLocalAdapter(
        store,
        lambda: HabitatProcessBackend(config, arguments.initialization_timeout_s, backend_class),
        arguments.initialization_timeout_s,
        progress_directory=arguments.progress_directory,
        agent_id=arguments.agent_id,
    )
    server = HabitatBridgeServer((arguments.host, arguments.port), adapter)
    try:
        logging.info("Habitat Local EAIOS ready at http://%s:%s", *server.server_address)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
