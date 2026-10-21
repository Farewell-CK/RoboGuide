"""Source-bound deployment optics and read-only checks of loaded fixed cameras.

Scalar capability facts describe one base-mounted pinhole camera. They never
promise visibility, gimbal control or an official semantic outcome. Articulated
cameras remain usable by the original detector but are not advertised as fixed.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .model import IntegrationError

OPTICS_SCHEMA = "roboguide.observation-optics-declaration/v0.1"
OPTICS_MODEL = "fixed-primary-pinhole/v0.1"
OPTICS_FIELDS = frozenset(
    {
        "camera-model",
        "camera-height-above-base-m",
        "camera-forward-offset-m",
        "camera-forward-elevation-degrees",
        "camera-horizontal-fov-degrees",
        "camera-vertical-fov-degrees",
        "camera-pan-tilt-supported",
    }
)
MAX_OPTICS_BYTES = 65536


def fixed_camera_mount(mounts: dict[str, Any], sensor_name: str, agent: int) -> Any:
    """Resolve native single/multi-agent camera prefixes without mixing endpoint ownership."""
    prefix = f"agent_{agent}_"
    if not sensor_name.startswith(prefix):
        raise IntegrationError("semantic camera belongs to another endpoint")
    matching = [
        mount
        for key, mount in mounts.items()
        if sensor_name.startswith(key)
        or not key.startswith("agent_")
        and sensor_name[len(prefix) :].startswith(key)
    ]
    return matching[0] if len(matching) == 1 and matching[0].attached_link_id == -1 else None


def validate_optics(attributes: dict[str, Any]) -> dict[str, Any]:
    """Validate an optional complete fixed-camera model; a partial model is unavailable."""
    facts = {name: attributes[name] for name in OPTICS_FIELDS if name in attributes}
    if not facts:
        return {}
    if (
        set(facts) != OPTICS_FIELDS
        or facts["camera-model"] != OPTICS_MODEL
        or facts["camera-pan-tilt-supported"] is not False
    ):
        raise IntegrationError("fixed observation optics declaration is incomplete or unsupported")
    for name, value in facts.items():
        if name in {"camera-model", "camera-pan-tilt-supported"}:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise IntegrationError("observation optics must contain finite numeric scalars")
    if (
        not 0 < facts["camera-horizontal-fov-degrees"] < 180
        or not 0 < facts["camera-vertical-fov-degrees"] < 180
        or not -90 <= facts["camera-forward-elevation-degrees"] <= 90
        or not 0 <= facts["camera-height-above-base-m"] <= 100
        or abs(facts["camera-forward-offset-m"]) > 100
    ):
        raise IntegrationError("observation optics values exceed the supported fixed-camera model")
    return facts


def build_optics_declaration(sources: tuple[tuple[int, Path], ...]) -> dict[str, Any]:
    """Freeze exact Node capability declarations under RoboGuide Python before child startup."""
    import tomllib

    records = []
    for agent, path in sources:
        raw = path.read_bytes()
        document = tomllib.loads(raw.decode())
        profiles = [
            item
            for item in document.get("capability_profiles", [])
            if item.get("contract") == "observation.acquire@v1"
        ]
        if len(profiles) > 1:
            raise IntegrationError("observation optics need one exact acquisition profile")
        facts = validate_optics(profiles[0].get("attributes", {})) if profiles else {}
        records.append(
            {
                "agent_id": agent,
                "node_config_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "attributes": facts,
            }
        )
    return {"schema_version": OPTICS_SCHEMA, "agents": records}


def read_optics_declaration(path: Path, registry: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Require exact startup source coverage; absence never downgrades the new profile."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_OPTICS_BYTES + 1)
    if len(raw) > MAX_OPTICS_BYTES:
        raise IntegrationError("observation optics declaration exceeds its byte budget")
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "agents"}
        or document["schema_version"] != OPTICS_SCHEMA
        or not isinstance(document["agents"], list)
        or len(document["agents"]) != len(registry["endpoints"])
    ):
        raise IntegrationError("observation optics source coverage is invalid")
    result = {}
    for index, record in enumerate(document["agents"]):
        endpoint = registry["endpoints"][index]
        if (
            not isinstance(record, dict)
            or set(record) != {"agent_id", "node_config_digest", "attributes"}
            or type(record["agent_id"]) is not int
            or record["agent_id"] != endpoint["agent_id"]
            or record["node_config_digest"] != endpoint["node_config_digest"]
            or not isinstance(record["attributes"], dict)
        ):
            raise IntegrationError("observation optics differ from the frozen Node source")
        facts = validate_optics(record["attributes"])
        if facts != record["attributes"]:
            raise IntegrationError("observation optics snapshot contains undeclared fields")
        result[record["agent_id"]] = facts
    return result


def loaded_fixed_camera_optics(environment: Any, agent: int) -> dict[str, Any]:
    """Read one actual base-mounted pinhole camera without rendering or changing state."""
    import habitat_sim  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]
    from habitat_sim.sensor import CameraSensor  # type: ignore[import-not-found]

    robot = environment.sim.get_agent_data(agent).articulated_agent
    params = robot.params
    candidates = []
    for name, wrapper in environment.sim._sensors.items():
        prefix = f"agent_{agent}_"
        if not name.startswith(prefix) or "semantic" not in name:
            continue
        if fixed_camera_mount(params.cameras, name, agent) is None:
            continue
        sensor = wrapper._sensor_object
        if (
            type(sensor) is not CameraSensor
            or sensor.specification().sensor_subtype != habitat_sim.SensorSubType.PINHOLE
        ):
            continue
        renderer = sensor.render_camera
        body_from_camera = (renderer.camera_matrix @ robot.sim_obj.transformation).inverted()
        matrix = np.asarray(body_from_camera)
        if matrix.shape != (4, 4) or not bool(np.isfinite(matrix).all()):
            raise IntegrationError("loaded fixed-camera transform is malformed")
        # The model declares height from the kinematic base point and forward
        # along the platform's local +X axis. Other mount layouts stay unknown.
        forward = -matrix[:3, 2]
        if abs(float(matrix[2, 3])) > 1e-4 or abs(float(forward[2])) > 1e-4:
            continue
        hfov = float(sensor.hfov)
        width, height = map(int, renderer.viewport)
        if min(width, height) <= 0:
            raise IntegrationError("loaded fixed-camera viewport is malformed")
        vfov = math.degrees(2 * math.atan(math.tan(math.radians(hfov) / 2) * height / width))
        facts = validate_optics(
            {
                "camera-model": OPTICS_MODEL,
                "camera-height-above-base-m": float(matrix[1, 3] - params.base_offset[1]),
                "camera-forward-offset-m": float(matrix[0, 3]),
                "camera-forward-elevation-degrees": math.degrees(
                    math.atan2(float(forward[1]), float(forward[0]))
                ),
                "camera-horizontal-fov-degrees": hfov,
                "camera-vertical-fov-degrees": vfov,
                "camera-pan-tilt-supported": False,
            }
        )
        candidates.append((name, facts))
    if len(candidates) != 1:
        raise IntegrationError("one unambiguous base-mounted fixed camera is required")
    name, facts = candidates[0]
    return {"sensor_uuid": name, "attributes": facts}


def check_loaded_optics(environment: Any, agent: int, declared: dict[str, Any]) -> dict[str, Any]:
    """Reject inaccurate configured optics before READY; unknown declarations remain unknown."""
    if not declared:
        return {"status": "unavailable", "reason": "no fixed-camera capability declared"}
    actual = loaded_fixed_camera_optics(environment, agent)
    validate_observed_optics(declared, actual)
    return {"status": "observed", "source": "loaded-original-camera-matrices", **actual}


def validate_observed_optics(declared: dict[str, Any], actual: dict[str, Any]) -> None:
    """Check finite observed values against declared facts without querying Habitat."""
    observed = validate_optics(actual.get("attributes", {}))
    if set(observed) != OPTICS_FIELDS:
        raise IntegrationError("loaded fixed-camera optics are unavailable")
    for name, expected in validate_optics(declared).items():
        value = observed[name]
        if (
            isinstance(expected, (str, bool))
            and (type(value) is not type(expected) or value != expected)
            or not isinstance(expected, (str, bool))
            and not math.isclose(value, expected, rel_tol=0, abs_tol=1e-4)
        ):
            raise IntegrationError("loaded fixed-camera optics disagree with Node capability")


def validate_optics_readiness(
    readiness: dict[str, Any], declared: dict[int, dict[str, Any]]
) -> None:
    """Fence archived readiness against source-bound declarations without loading Habitat."""
    sensors = readiness.get("sensors")
    actual = readiness.get("fixed_camera_optics")
    if (
        readiness.get("schema_version") != "roboguide.perception-readiness/v0.3"
        or not isinstance(sensors, dict)
        or not isinstance(actual, dict)
        or set(actual) != set(sensors)
        or not set(actual) <= {str(agent) for agent in declared}
    ):
        raise IntegrationError("observation optics readiness coverage is unavailable")
    for key, observation in actual.items():
        facts = declared[int(key)]
        if not facts:
            if observation != {
                "status": "unavailable",
                "reason": "no fixed-camera capability declared",
            }:
                raise IntegrationError("undeclared observation optics cannot become known")
            continue
        if (
            not isinstance(observation, dict)
            or observation.get("status") != "observed"
            or observation.get("source") != "loaded-original-camera-matrices"
            or not isinstance(observation.get("sensor_uuid"), str)
            or not observation["sensor_uuid"].startswith(f"agent_{key}_")
            or not isinstance(observation.get("attributes"), dict)
            or set(observation["attributes"]) != OPTICS_FIELDS
        ):
            raise IntegrationError("loaded observation optics are unavailable or detached")
        validate_observed_optics(facts, observation)
