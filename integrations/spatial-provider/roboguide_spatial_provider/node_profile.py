"""Generate deployment-owned formal Node workflows for actual configured spatial file jobs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .__main__ import parse_job
from .contracts import canonical, digest, exact, fields


def node_config(
    configuration: dict[str, Any],
    *,
    node_id: str,
    server_endpoint: str,
    artifact_endpoint: str,
    state_root: Path,
    cache_root: Path,
    socket_path: Path,
) -> str:
    """Declare configured software Node identity and private workflows without starting them."""
    raw = fields(configuration, {"schema_version", "max_jobs", "max_active", "profiles"})
    if raw["schema_version"] != "roboguide.spatial-local-service/v0.1":
        raise ValueError("unsupported spatial Local How profile")
    if not isinstance(raw["profiles"], list) or not 1 <= len(raw["profiles"]) <= 128:
        raise ValueError("one to 128 explicit file profiles are required")
    exact(node_id, "node_id")
    for value in (state_root, cache_root, socket_path):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("Node state/cache/socket paths must be exact absolute paths")
    encoded = json.dumps
    text = f"""schema = "roboguide.node-config/v0.8"
node_id = {encoded(node_id)}
server_endpoint = {encoded(server_endpoint)}
state_directory = {encoded(str(state_root))}
reconnect_delay_ms = 100
[artifacts]
endpoint = {encoded(artifact_endpoint)}
cache_directory = {encoded(str(cache_root))}
max_artifact_bytes = 536870912
chunk_size_bytes = 65536
connect_timeout_ms = 3000
read_timeout_ms = 10000
"""
    selectors = set()
    slots = set()
    for item in raw["profiles"]:
        profile = fields(item, {"profile_id", "map_id", "revision_id", "spatial_anchor_id", "job"})
        for key in ("profile_id", "map_id", "revision_id", "spatial_anchor_id"):
            exact(profile[key], key)
        job = parse_job(profile["job"])
        if job.job_id != profile["revision_id"]:
            raise ValueError("native and semantic revisions differ")
        selector = (profile["map_id"], profile["revision_id"])
        if selector in selectors or profile["profile_id"] in slots:
            raise ValueError("duplicate immutable spatial selector or output slot")
        selectors.add(selector)
        slots.add(profile["profile_id"])
        key = digest(canonical(selector)).removeprefix("sha256:")
        planar = profile["job"]["schema_version"] == "roboguide.spatial-occupancy-job/v0.1"
        resolution = profile["job"]["policy"]["resolution_meters" if planar else "voxel_size"]
        text += f"""
[[artifacts.output_bindings]]
id = {encoded(profile["profile_id"])}
map_id = {encoded(profile["map_id"])}
revision_id = {encoded(profile["revision_id"])}
source_path = {encoded(f"outputs/{key}/{job.job_id}/map.bundle")}
media_type = "application/vnd.roboguide.spatial-map"
format_name = {encoded("observed-occupancy-tiles" if planar else "observed-point-tiles")}
format_version = "v0.1"
root_frame = {encoded(job.frame_id)}
coordinate_convention = "declared-file-frame"
spatial_anchor_id = {encoded(profile["spatial_anchor_id"])}
resolution_meters = {resolution}
"""
    text += f"""
[[local_systems]]
id = "spatial-files"
runtime_name = "roboguide-spatial-file-provider"
runtime_version = "0.1"
[local_systems.metadata]
assurance = "software-process"
source = "configured-files"
[local_systems.health]
state_pointer = "/state"
detail_pointer = "/detail"
online = ["ONLINE"]
degraded = ["DEGRADED"]
offline = ["OFFLINE"]
[local_systems.health.step]
id = "health"
connection = "spatial-unix"
[local_systems.health.step.operation]
kind = "http"
method = "GET"
path = "/v1/health"
[[connections]]
driver = "http"
id = "spatial-unix"
local_system = "spatial-files"
endpoint = {encoded("unix://" + str(socket_path))}
timeout_ms = 10000
[[resources]]
id = "spatial-cpu-slot"
kind = "compute"
capacity = 1
owner = "spatial-files"
"""
    for schema in sorted({profile["job"]["schema_version"] for profile in raw["profiles"]}):
        planar = schema == "roboguide.spatial-occupancy-job/v0.1"
        payload_schema = (
            "roboguide.spatial-occupancy-map/v0.1" if planar else "roboguide.spatial-file-map/v0.1"
        )
        text += f"""
[[memory_providers]]
id = {encoded("spatial-occupancy-maps" if planar else "spatial-file-maps")}
owner = "spatial-files"
kind = "spatial"
scope = "global"
visibility = "exchangeable"
payload_schema = {encoded(payload_schema)}
media_type = "application/vnd.roboguide.spatial-map"
"""
    for operation, directive in (("build", "prepare-output"), ("publish", "publish")):
        text += f'''
[[capability_profiles]]
contract = "spatial.map.{operation}@v0"
kind = "compute"
owner = "spatial-files"
attributes = {{}}
[capability_profiles.readiness]
state_pointer = "/capabilities/spatial.map.{operation}@v0/state"
detail_pointer = "/capabilities/spatial.map.{operation}@v0/detail"
ready = ["READY"]
unavailable = ["UNAVAILABLE"]
[capability_profiles.readiness.step]
id = "readiness"
connection = "spatial-unix"
[capability_profiles.readiness.step.operation]
kind = "http"
method = "GET"
path = "/v1/readiness"
[[operations]]
operation = "spatial.map.{operation}@v0"
owner = "spatial-files"
required_resources = ["spatial-cpu-slot"]
local_locks = ["spatial-cpu-job"]
artifact_operation = "{directive}"
[operations.workflow]
poll_interval_ms = 100
[operations.workflow.local_handle]
kind = "pointer"
pointer = "/steps/dispatch/execution_id"
[operations.workflow.execution_state]
state_pointer = "/steps/status/state"
reason_pointer = "/steps/status/detail"
accepted = ["ACCEPTED"]
running = ["RUNNING"]
completed = ["COMPLETED"]
failed = ["FAILED"]
cancelled = ["CANCELLED"]
[[operations.workflow.execute]]
id = "dispatch"
connection = "spatial-unix"
[operations.workflow.execute.operation]
kind = "http"
method = "POST"
path = "/v1/executions"
[operations.workflow.execute.request]
base = {{ operation = "{operation}-map" }}
'''
        for key, pointer in (
            ("invocation", "/invocation"),
            ("attempt_id", "/attempt_id"),
            ("artifact_paths", "/artifacts/outputs"),
        ):
            text += f'''
[[operations.workflow.execute.request.bindings]]
target = "/{key}"
[operations.workflow.execute.request.bindings.value]
kind = "pointer"
pointer = "{pointer}"
'''
        for phase in ("status", "cancel"):
            text += f'''
[[operations.workflow.{phase}]]
id = "{phase}"
connection = "spatial-unix"
[operations.workflow.{phase}.operation]
kind = "http"
method = "POST"
path = "/v1/executions/{phase}"
[[operations.workflow.{phase}.request.bindings]]
target = "/execution_id"
[operations.workflow.{phase}.request.bindings.value]
kind = "pointer"
pointer = "/local_handle"
'''
    return text


def main() -> None:
    """Write one explicit deployment configuration without registering or executing a Node."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--server-endpoint", required=True)
    parser.add_argument("--artifact-endpoint", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--socket-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    text = node_config(
        json.loads(arguments.profile.read_bytes()),
        node_id=arguments.node_id,
        server_endpoint=arguments.server_endpoint,
        artifact_endpoint=arguments.artifact_endpoint,
        state_root=arguments.state_root,
        cache_root=arguments.cache_root,
        socket_path=arguments.socket_path,
    )
    with arguments.output.open("x") as stream:
        stream.write(text)


if __name__ == "__main__":
    main()
