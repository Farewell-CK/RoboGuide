"""Command-line entry point for the Habitat Local EAIOS bridge."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .adapter import HabitatLocalAdapter
from .backend import HabitatBackendConfig
from .http_service import HabitatBridgeServer
from .process_backend import HabitatProcessBackend
from .store import ExecutionStore


def _arguments() -> argparse.Namespace:
    """Parse fixed deployment choices without accepting per-request Local How."""
    parser = argparse.ArgumentParser(description="RoboGuide Habitat Local EAIOS bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28100)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--habitat-config", type=Path, required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--agent-id", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--step-period-ms", type=int, default=20)
    parser.add_argument("--initialization-timeout-s", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    """Initialize the real Habitat backend before exposing loopback workflow routes."""
    arguments = _arguments()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Habitat Local EAIOS must bind a loopback host")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = HabitatBackendConfig(
        config_path=arguments.habitat_config,
        episode_id=arguments.episode_id,
        agent_id=arguments.agent_id,
        max_steps=arguments.max_steps,
        step_period_ms=arguments.step_period_ms,
    )
    store = ExecutionStore(arguments.state_db)
    adapter = HabitatLocalAdapter(
        store,
        lambda: HabitatProcessBackend(config, arguments.initialization_timeout_s),
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
