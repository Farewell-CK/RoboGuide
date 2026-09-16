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
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--step-period-ms", type=int, default=20)
    parser.add_argument("--initialization-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--subtask-mode",
        choices=SUBTASK_MODES,
        default="natural-objective",
        help="CrabAgent backends only: natural-objective (Protocol B) or entity-grounded",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="LLM backends only: directory for scene/trace/token evidence",
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
    return parser.parse_args()


def _run_shared_world(arguments: argparse.Namespace) -> None:
    """Serve two Node endpoints over exactly one shared Habitat world."""
    if arguments.port_b is None or arguments.state_db_b is None or arguments.agent_b_id is None:
        raise SystemExit(
            "the shared-emos-stage2 backend requires --port-b, --state-db-b, --agent-b-id"
        )
    if arguments.evidence_dir is None:
        raise SystemExit("the shared-emos-stage2 backend requires --evidence-dir")
    if arguments.agent_id == arguments.agent_b_id:
        raise SystemExit("shared-world endpoints must map to distinct Habitat agents")
    config = CrabAgentBackendConfig(
        config_path=arguments.habitat_config,
        episode_id=arguments.episode_id,
        agent_id=arguments.agent_id,
        max_steps=arguments.max_steps,
        step_period_ms=arguments.step_period_ms,
        subtask_mode=arguments.subtask_mode,
        evidence_dir=arguments.evidence_dir,
    )
    world = ProcessWorldService(config, (arguments.agent_id, arguments.agent_b_id))
    coordinator = SharedWorldCoordinator(world, arguments.pair_wait_s, arguments.evidence_dir)
    endpoint_a = NodeEndpoint(
        "node-a", arguments.agent_id, ExecutionStore(arguments.state_db), coordinator
    )
    endpoint_b = NodeEndpoint(
        "node-b", arguments.agent_b_id, ExecutionStore(arguments.state_db_b), coordinator
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
    }
    if arguments.backend == "emos-crabagent":
        config: HabitatBackendConfig = CrabAgentBackendConfig(
            **common,
            subtask_mode=arguments.subtask_mode,
            evidence_dir=arguments.evidence_dir,
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
