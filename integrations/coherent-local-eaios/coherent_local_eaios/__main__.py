"""Command-line entry point for the COHERENT Local EAIOS bridge."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .bridge import BackendConfig, BridgeServer, CoherentLocalAdapter, ExecutionStore, preflight


def main() -> None:
    """Start the startup-validated loopback COHERENT workflow service."""
    parser = argparse.ArgumentParser(description="RoboGuide COHERENT Local EAIOS bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28110)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--container", default="coherent-sim")
    parser.add_argument("--runner", default="/workspace/exp2-coherent/scripts/run_official_task.sh")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--timeout-s", type=int, default=900)
    arguments = parser.parse_args()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("COHERENT Local EAIOS must bind a loopback host")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = BackendConfig(
        container=arguments.container,
        runner=arguments.runner,
        results_root=arguments.results_root,
        evidence_dir=arguments.evidence_dir,
        timeout_s=arguments.timeout_s,
    )
    preflight(config)
    adapter = CoherentLocalAdapter(ExecutionStore(arguments.state_db), config)
    server = BridgeServer((arguments.host, arguments.port), adapter)
    try:
        logging.info("COHERENT Local EAIOS ready at http://%s:%s", *server.server_address)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
