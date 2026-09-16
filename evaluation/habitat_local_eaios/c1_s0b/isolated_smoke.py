"""C1-S0B PHASE 11 isolated smoke: real CrabAgent through the adapter API.

Builds a real ExecutionStore + HabitatLocalAdapter with the real
CrabAgentMobilityBackend (spawned simulator process, real EMOS CrabAgent,
real LLM endpoint), submits one canonical invocation through
``adapter.submit``, polls the durable handle to a terminal fact, and
prints the outcome JSON.  Usage:

    python isolated_smoke.py RUN_DIR SUBTASK_MODE
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

INTEGRATION_ROOT = Path(__file__).parents[2]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.adapter import HabitatLocalAdapter  # noqa: E402
from habitat_local_eaios.crabagent_backend import (  # noqa: E402
    CrabAgentBackendConfig,
    CrabAgentMobilityBackend,
)
from habitat_local_eaios.process_backend import HabitatProcessBackend  # noqa: E402
from habitat_local_eaios.store import ExecutionStore  # noqa: E402

EMOS_ROOT = Path("/data/workspace/code/emos-baseline")


def main() -> None:
    """Run one isolated real-CrabAgent smoke against the adapter API."""
    run_dir = Path(sys.argv[1]).resolve()
    subtask_mode = sys.argv[2]
    run_dir.mkdir(parents=True, exist_ok=True)
    config = CrabAgentBackendConfig(
        config_path=EMOS_ROOT / "habitat-baselines/habitat_baselines/config/multi_rearrange/"
        "llm_spot_fetch_mobility.yaml",
        episode_id="51",
        agent_id=0,
        max_steps=1000,
        step_period_ms=20,
        subtask_mode=subtask_mode,
        robot_type="SpotRobot",
        evidence_dir=run_dir / "evidence",
    )
    started = time.monotonic()
    adapter = HabitatLocalAdapter(
        ExecutionStore(run_dir / "bridge.sqlite3"),
        lambda: HabitatProcessBackend(config, 240.0, CrabAgentMobilityBackend),
        240.0,
    )
    init_seconds = round(time.monotonic() - started, 1)
    try:
        response = adapter.submit(
            {
                "invocation": {
                    "mission_id": f"mission-c1-s0b-isolated-{subtask_mode}",
                    "task_id": "navigate-target",
                    "group_id": f"group-c1-s0b-isolated-{subtask_mode}",
                    "role_id": "navigator",
                    "operation": "mobility.navigate@v1",
                    "objective": "Navigate the Habitat robot to the semantic target.",
                    "parameters": {"destination": "any_targets|0"},
                    "resource_ids": ["habitat-navigation-slot"],
                }
            }
        )
        handle = str(response["execution_id"])
        print("ACCEPT", json.dumps(response, ensure_ascii=False), flush=True)
        terminal = None
        while time.monotonic() - started < 900:
            time.sleep(2)
            status = adapter.status({"execution_id": handle})
            print("STATUS", status["state"], flush=True)
            if status["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                terminal = status
                break
        if terminal is None:
            raise SystemExit("isolated smoke exceeded 900s without a terminal fact")
        health = adapter.health()
        readiness = adapter.readiness()
        print(
            "RESULT",
            json.dumps(
                {
                    "init_seconds": init_seconds,
                    "terminal": terminal,
                    "health_after": health,
                    "readiness_after": readiness,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
