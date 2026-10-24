"""Compose a real published file map with original-process runtime revision authority."""

from __future__ import annotations

import copy
import json
import ssl
import subprocess
import uuid
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save, wait_for
from memory_group_lifecycle import GroupScenario
from memory_revision_processes import run as revise
from spatial_memory_consumer import Exchange, SpatialMemoryConsumer, Start


def run(
    consumer: SpatialMemoryConsumer,
    origin: str,
    cas: str,
    exchange: Exchange,
    controller: subprocess.Popen[bytes],
    controller_command: list[str],
    start: Start,
    contexts: dict[str, ssl.SSLContext],
    producer_node: str,
    case: str,
    old_controller: Path | None,
) -> tuple[GroupScenario, dict[str, Any]]:
    """Use the original live Controller, published map and actual receiver processes."""
    directory = consumer.output
    typed = json.loads((directory / "catalog-manifest.json").read_bytes())["manifest"]
    path = f"/v1/memories/{typed['map_id']}/revisions/{typed['revision_id']}"
    status, response = exchange(cas, path, None)
    assert status == 200, response
    parent = copy.deepcopy(response["manifest"])
    del parent["typed_extension"]
    del parent["status"]
    parent["schema"] = "roboguide.memory-manifest/v0.1"
    save(directory / "manifest.json", parent)
    save(directory / "revision-typed-map.json", typed)
    source = json.loads((directory / "mission.json").read_bytes())
    mission = "typed-revision-" + uuid.uuid4().hex
    original = copy.deepcopy(source)
    original["mission"] = {
        "id": mission,
        "objective": "independently hash the original published map",
        "actors": [{"id": "map-consumer"}],
    }
    original["contexts"][0]["roles"] = [{"id": "consumer", "actor": "map-consumer"}]
    original["tasks"] = [copy.deepcopy(source["tasks"][-1])]
    task = original["tasks"][0]
    task["depends_on"] = []
    task["roles"][0]["execution_intent"]["parameters"]["rounds"] = 2000000
    save(directory / "handoff-source-mission.json", original)

    def scoped_exchange(
        path: str,
        body: dict[str, Any] | None = None,
        *,
        context: ssl.SSLContext | None = None,
        endpoint: str = origin,
    ) -> tuple[int, dict[str, Any]]:
        """Keep the exact actual leaf independent of JSON roles across the shared public APIs."""
        from check_artifact_authority_processes import call

        status, raw = call(
            endpoint,
            context or contexts["admin"],
            "GET" if body is None else "POST",
            path,
            None if body is None else json.dumps(body).encode(),
            {
                "Content-Type": "application/json",
                **({"X-RoboGuide-Tenant": "local"} if path.startswith("/v1/missions") else {}),
            },
        )
        result = json.loads(raw)
        assert isinstance(result, dict)
        return status, result

    scenario = GroupScenario(
        directory=directory,
        nodes=[consumer.node, producer_node],
        backends=[consumer.backend],
        manifest=parent,
        exchange=scoped_exchange,
        verifier_context=contexts["viewer"],
        controller=controller,
        controller_command=controller_command,
        start=start,
        node_processes=(consumer.node_process,),
        typed_map=typed,
        worker_names=("consumer",),
        administrator="spatial-administrator",
    )
    status, receipt = scoped_exchange("/v1/missions", original)
    assert status == 202, receipt
    consumer.authorize(origin, exchange, "group-" + mission)
    wait_for(
        lambda: next(
            (
                row
                for row in scenario.get(f"/v1/missions/{mission}/execution-attempts")["attempts"]
                if row["mission_id"] == mission and row["status"] == "Running"
            ),
            None,
        ),
        "actual original typed-map receiver process is running",
        30,
    )
    result = revise(scenario, mission, case, old_controller)
    result["production_mission_id"] = typed["source_mission_id"]
    result["consumer_mission_id"] = mission
    assert result["production_mission_id"] != mission
    save(directory / "typed-revision-result.json", result)
    return scenario, result
