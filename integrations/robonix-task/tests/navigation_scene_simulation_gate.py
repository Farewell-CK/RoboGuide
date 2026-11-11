"""Exercise synthetic Scene and reported-pose qualification at original JSON boundaries.

No sockets, Controller/Node processes, credentials, real devices, Mission admission or
actuator calls exist here. Production readers are real; their original source IO and
Controller HTTP JSON documents are deterministic isolated fixtures. Fixed patrol IDs
below belong only to the private fixture and confer no field authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from mission.grounding_reader import HttpMissionGroundingReader
from mission.models import JSONObject
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from robonix_task.navigation_preflight import assess_navigation_preflight
from robonix_task.patrol import (
    ARRIVAL_CONTRACT,
    ASSIGNMENTS,
    arrival_predicate,
    assess_patrol_preflight,
)
from robonix_task.patrol_grounding import build_patrol_snapshot
from robonix_task.physical_observation import SCHEMA as PHYSICAL_SCHEMA
from robonix_task.physical_observation import physical_observation
from robonix_task.position import inspect_position
from robonix_task.posture import inspect_posture
from robonix_task.scene_targets import SCHEMA as SCENE_SCHEMA
from robonix_task.scene_targets import SceneTargetObserver
from test_patrol_grounding import _record, fixture
from test_physical_observation import original

ROOT = Path(__file__).resolve().parents[3]
ENTITY_SCHEMA = "roboguide.world.physical-entity-reference/v0.1"


class SceneSource:
    """Supply exact synthetic source bytes and process-local clocks without network access."""

    def __init__(self, index: int) -> None:
        """Initialize a separate original map/region source for each fixture participant."""
        assignment = ASSIGNMENTS[index]
        self.ns = 1_000_000_000
        self.calls: list[str] = []
        self.error = False
        self.switch_map = False
        self.state: dict[str, Any] = {
            "map_binding": {
                "map_id": "synthetic-map-" + assignment.node_id,
                "mode": "localization",
                "generation": index,
                "source": "isolated-software-fixture",
            }
        }
        self.regions = {
            "ok": True,
            "annotations": [
                {
                    "annotation_id": "software-region-" + str(index),
                    "kind": "region",
                    "name": assignment.target_name,
                    "points": [[11.399093130268493, 0], [12.399093130268493, 0], [12, 1]],
                    "stale": False,
                }
            ],
        }

    def read(self, path: str, timeout: float) -> bytes:
        """Return one bounded original document while spending the source's own budget."""
        assert path in {"/api/state", "/api/regions"} and 0 < timeout <= 5
        self.calls.append(path)
        self.ns += 10_000_000
        if self.error:
            raise OSError("isolated synthetic Scene unavailable")
        value = cast(
            dict[str, Any], deepcopy(self.regions if path == "/api/regions" else self.state)
        )
        if self.switch_map and path == "/api/state" and len(self.calls) % 3 == 0:
            value["map_binding"]["map_id"] = "synthetic-foreign-map"
        return json.dumps(value, ensure_ascii=False).encode()

    def clock(self) -> int:
        """Return only this fake source's monotonic clock."""
        return self.ns

    def wall(self) -> int:
        """Provide an audit receipt clock, never a localization measurement timestamp."""
        return 1_000_000_000_000 + self.ns

    def observer(self, index: int) -> SceneTargetObserver:
        """Construct the unmodified production sampler using isolated original source IO."""
        assignment = ASSIGNMENTS[index]
        return SceneTargetObserver(
            assignment.entity_id,
            assignment.node_id,
            reader=self.read,
            clock=self.clock,
            wall_clock=self.wall,
        )


class ControllerDocuments:
    """Substitute only authenticated HTTP transport, without pretending to test authentication."""

    def __init__(self, state: dict[str, Any]) -> None:
        """Freeze the exact test document so every capture has retained original evidence."""
        self.state = deepcopy(state)
        self.calls: list[str] = []

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Permit only the real reader's two GET documents; refuse all other destinations."""
        assert timeout_seconds > 0
        self.calls.append(url)
        if url == "http://controller.invalid/v1/state/records?object_class=world":
            return cast(JSONObject, deepcopy(self.state))
        assert url == "http://artifact.invalid/v1/memories"
        return {"schema": "roboguide.memory-catalog/v0.1", "memories": []}


def documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[SceneSource]]:
    """Assemble real Scene sampler outputs, physical projections and exact fixture sources."""
    world, inventory, registry, _ = fixture()
    world["records"] = []
    robots: list[dict[str, Any]] = []
    sources = [SceneSource(i) for i in range(2)]
    for i, assignment in enumerate(ASSIGNMENTS):
        source = sources[i]
        observer = source.observer(i)
        observer.sample()
        report = observer.read()
        raw = original()
        raw["mapping"]["value"]["map_id"] = source.state["map_binding"]["map_id"]
        raw["chassis"]["value"]["posture"] = "standing"
        for channel, schema, value in [
            ("physical-entity-reference", ENTITY_SCHEMA, {"entity_id": assignment.entity_id}),
            ("scene-targets", SCENE_SCHEMA, report),
            (
                "robot-physical-observation",
                PHYSICAL_SCHEMA,
                physical_observation(raw, assignment.entity_id),
            ),
        ]:
            world["records"].append(
                _record(
                    assignment.node_id,
                    assignment.entity_id,
                    channel,
                    schema,
                    value,
                )
            )
        robot = _record(
            assignment.node_id, assignment.entity_id, "robot-state", "lite3.robot-state/v0.1", raw
        )
        robot.update(
            object={"class": "node", "object_type": "robot", "object_id": assignment.node_id},
            source=f"node:{assignment.node_id}/robot-observer",
        )
        robot["value"].update(confidence_millionths=None, valid_for_ms=5000)
        robots.append(robot)
    for record in world["records"]:
        record["value"]["confidence_millionths"] = None
        if record["object"]["object_type"] == "robot-physical-observation":
            record["channel_id"] = "physical-observation"
            record["value"]["valid_for_ms"] = 5000
    world["robot_records"] = robots
    return world, inventory, registry, sources


def evaluate(
    world: dict[str, Any],
    inventory: dict[str, Any],
    registry: dict[str, Any],
    verifier_view: dict[str, Any] | None = None,
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run production reported-position, posture, MI and patrol readers without admitting work."""
    world = deepcopy(world)
    node_state = {
        "schema": world["schema"],
        "observed_at_ms": world["observed_at_ms"],
        "records": world.pop("robot_records"),
    }
    posture = {
        a.node_id: inspect_posture(node_state, inventory, node_id=a.node_id) for a in ASSIGNMENTS
    }
    positions = {
        a.node_id: inspect_position(
            node_state, inventory, node_id=a.node_id, expected_map_id="synthetic-map-" + a.node_id
        )
        for a in ASSIGNMENTS
    }
    snapshot = build_patrol_snapshot(world, inventory, registry, posture, local_read_elapsed_ms=1)
    assessment = assess_patrol_preflight(snapshot, observed_at_ms=inventory["observed_at_ms"])
    transport = ControllerDocuments(world)
    reader = HttpMissionGroundingReader(
        "http://controller.invalid",
        "http://artifact.invalid",
        2,
        16,
        16,
        transport,
        admitted_world_payload_schemas=frozenset({SCENE_SCHEMA, PHYSICAL_SCHEMA, ENTITY_SCHEMA}),
    )
    grounding = reader.capture(
        "isolated-scene-qualification",
        (
            DialogueTurn(
                "turn-one",
                DialogueSpeaker.USER,
                DialogueTurnKind.INSTRUCTION,
                "Synthetic Scene qualification only; no motion",
                1,
            ),
        ),
        1000,
    ).to_json()
    return {
        "world": world,
        "node_state": node_state,
        "inventory": inventory,
        "physical_registry": registry,
        "positions": positions,
        "postures": posture,
        "patrol_snapshot": snapshot,
        "patrol_preflight": assessment,
        "navigation_preflight": assess_navigation_preflight(
            world,
            node_state,
            inventory,
            registry,
            verifier_view or {},
            source_config or {},
            local_read_elapsed_ms=1,
        ),
        "mi_grounding": grounding,
        "transport_calls": transport.calls,
    }


def scene_record(world: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """Select an exact fixture Scene envelope without borrowing the peer's report."""
    return next(
        r
        for r in world["records"]
        if r["channel_id"] == "scene-targets"
        and r["object"]["object_id"] == ASSIGNMENTS[index].entity_id
    )


def arrival_source(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze one invented registry once from normal targets, never from injected faulty data."""
    targets = []
    for assignment in ASSIGNMENTS:
        original = next(t for t in snapshot["targets"] if t["node_id"] == assignment.node_id)
        target = {
            k: original[k]
            for k in [
                "entity_id",
                "node_id",
                "target_name",
                "map_id",
                "region_id",
                "region_source_digest",
            ]
        }
        target["predicate"] = arrival_predicate(snapshot, assignment)
        targets.append(target)
    config = {
        "schema_version": "roboguide.onsite-arrival-verifier/v0.1",
        "source_id": "isolated-arrival",
        "revision": 1,
        "tenant_id": "local",
        "producer_principal": "software-only-observer",
        "verifier": dict(ARRIVAL_CONTRACT),
        "max_age_ms": 5000,
        "targets": targets,
    }
    source = {
        "source_id": config["source_id"],
        "revision": 1,
        "tenant_id": "local",
        "producer_principals": [config["producer_principal"]],
        "verifier": dict(ARRIVAL_CONTRACT),
        "predicates": sorted(t["predicate"] for t in targets),
        "max_age_ms": 5000,
        "stop_timeout_ms": 5000,
        "enabled": True,
        "continuous": False,
        "require_artifact": False,
    }
    view = {
        "schema_version": "roboguide.semantic-verifier-view/v0.1",
        "registry": {
            "schema_version": "roboguide.semantic-verifier-registry/v0.1",
            "registry_id": "synthetic-only-registry",
            "revision": 1,
            "sources": {config["source_id"]: source},
        },
    }
    return view, config


def run(output: Path) -> dict[str, Any]:
    """Retain normal and independently injected data faults in a fresh isolated directory."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    cases: dict[str, Any] = {}
    world, inventory, registry, sources = documents()
    baseline = evaluate(world, inventory, registry)
    verifier_view, source_config = arrival_source(baseline["patrol_snapshot"])
    normal = evaluate(world, inventory, registry, verifier_view, source_config)
    (output / "normal.json").write_text(
        json.dumps(normal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert len(normal["patrol_snapshot"]["targets"]) == 2
    assert not normal["mi_grounding"]["gaps"]
    assert normal["navigation_preflight"]["navigation_ready"], normal["navigation_preflight"]
    assert all(v["status"] == "reported" for v in normal["positions"].values())
    assert not normal["patrol_preflight"]["navigation_ready"]
    cases["normal"] = normal
    for mode in [
        "wrong-map",
        "stale-pose",
        "scene-map-switch",
        "digest-mismatch",
        "expired-cache",
        "source-error",
        "restart-before-sample",
        "missing-epoch",
    ]:
        world, inventory, registry, sources = documents()
        source = sources[0]
        record = scene_record(world)
        if mode in {"wrong-map", "stale-pose"}:
            raw = world["robot_records"][0]["value"]["value"]
            if mode == "wrong-map":
                raw["mapping"]["value"]["map_id"] = "synthetic-wrong-map"
            else:
                raw["mapping"]["age_ms"] = 10001
            projected = next(
                r
                for r in world["records"]
                if r["object"]["object_type"] == "robot-physical-observation"
                and r["object"]["object_id"] == ASSIGNMENTS[0].entity_id
            )
            projected["value"]["value"] = physical_observation(raw, ASSIGNMENTS[0].entity_id)
        elif mode == "digest-mismatch":
            record["value"]["value"]["definition_json"] += " "
        elif mode == "missing-epoch":
            record["value"]["source_epoch"] = ""
        else:
            observer = source.observer(0)
            observer.sample()
            if mode == "scene-map-switch":
                source.switch_map = True
                observer.sample()
            elif mode == "expired-cache":
                source.ns += 15_000_000_000
            elif mode == "source-error":
                source.error = True
                observer.sample()
            else:
                observer = source.observer(0)
                record["value"]["source_epoch"] = "synthetic-restarted-source"
            record["value"]["value"] = observer.read()
        result = evaluate(world, inventory, registry, verifier_view, source_config)
        (output / (mode + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if mode in {"wrong-map", "stale-pose"}:
            position = result["positions"][ASSIGNMENTS[0].node_id]
            assert position["status"] == "unavailable"
            assert position["reason"] == (
                "map_mismatch" if mode == "wrong-map" else "mapping_response_stale"
            )
        else:
            assert len(result["patrol_snapshot"]["targets"]) == 1, mode
            assert result["patrol_snapshot"]["source_gaps"], mode
        assert not result["navigation_preflight"]["navigation_ready"], mode
        assert result["navigation_preflight"]["motion_authorized"] is False
        assert result["patrol_preflight"]["motion_authorized"] is False
        cases[mode] = result
    source_paths = [
        Path(__file__),
        ROOT / "integrations/robonix-task/robonix_task/scene_targets.py",
        ROOT / "mission/src/mission/grounding_reader.py",
        ROOT / "integrations/robonix-task/robonix_task/navigation_preflight.py",
    ]
    result = {
        "passed": True,
        "simulation": True,
        "physical_robot_used": False,
        "controller_node_transport_tested": False,
        "authentication_tested": False,
        "synthetic_registry_not_field_authority": True,
        "new_missions": 0,
        "actuator_calls": 0,
        "cases": list(cases),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths
        },
        "limits": [
            "JSON-boundary fixture only; not actual Controller/Node wire acceptance",
            "reported Mapping response age is not localization measurement time",
            "no localization, shared frame, arrival, stopping or motion authority",
            "legacy patrol preparation alone does not bind Scene map to Mapping pose",
            "navigation_preflight adds reported map/freshness checks, not localization truth",
            "verifier registration and photo/summary remain explicit blockers",
        ],
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    """Require a fresh private output directory and expose no deployment/network arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output.resolve())))


if __name__ == "__main__":
    main()
