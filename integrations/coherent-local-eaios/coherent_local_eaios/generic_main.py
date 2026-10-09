"""Command-line entry point for the generic COHERENT primitive bridge."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .bridge import BridgeServer
from .generic_bridge import (
    CoherentPrimitiveAdapter,
    GenericBackendConfig,
    PrimitiveStore,
    initial_graph,
    load_task_data,
)


def main() -> None:
    """Start one loopback workflow service for any indexed official task."""
    parser = argparse.ArgumentParser(description="RoboGuide generic COHERENT primitive bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28120)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--pefa-root", type=Path, required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--pre-effect-hold-step", type=int)
    parser.add_argument("--pre-effect-hold-seconds", type=float, default=0.0)
    arguments = parser.parse_args()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("COHERENT generic bridge must bind a loopback host")
    if (
        arguments.pre_effect_hold_step is not None
        and arguments.pre_effect_hold_step < 1
    ) or arguments.pre_effect_hold_seconds < 0:
        raise SystemExit("pre-effect hold step and duration must be non-negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task_data = load_task_data(arguments.pefa_root, arguments.env, arguments.task)
    config = GenericBackendConfig(
        env_name=arguments.env,
        task_index=arguments.task,
        pefa_root=arguments.pefa_root,
        evidence_dir=arguments.evidence_dir,
        task_data=task_data,
        pre_effect_hold_step=arguments.pre_effect_hold_step,
        pre_effect_hold_seconds=arguments.pre_effect_hold_seconds,
    )
    adapter = CoherentPrimitiveAdapter(
        PrimitiveStore(arguments.state_db, initial_graph(task_data)), config
    )
    server = BridgeServer((arguments.host, arguments.port), adapter)
    try:
        logging.info("generic COHERENT bridge ready at http://%s:%s", *server.server_address)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        adapter.close()


if __name__ == "__main__":
    main()
