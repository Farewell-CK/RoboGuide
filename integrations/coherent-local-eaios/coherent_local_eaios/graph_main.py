"""Command-line entry point for the controlled COHERENT graph bridge."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .bridge import BridgeServer
from .graph_bridge import (
    CoherentGraphAdapter,
    GraphBackendConfig,
    GraphExecutionStore,
    initial_graph,
    load_task_data,
)


def main() -> None:
    """Start the loopback graph workflow service for public env4/task17."""
    parser = argparse.ArgumentParser(description="RoboGuide COHERENT controlled graph bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28120)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--pefa-root", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("COHERENT graph bridge must bind a loopback host")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task_data = load_task_data(arguments.pefa_root)
    config = GraphBackendConfig(
        pefa_root=arguments.pefa_root,
        evidence_dir=arguments.evidence_dir,
        task_data=task_data,
    )
    adapter = CoherentGraphAdapter(
        GraphExecutionStore(arguments.state_db, initial_graph(task_data)), config
    )
    server = BridgeServer((arguments.host, arguments.port), adapter)
    try:
        logging.info("COHERENT graph bridge ready at http://%s:%s", *server.server_address)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
