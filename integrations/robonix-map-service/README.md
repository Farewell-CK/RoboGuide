# Robonix Local Adapter

This directory contains an example Local EAIOS adapter for the RoboGuide Node
Service. It is deliberately outside `core/` and owns only the Robonix-specific
mapping calls and local file layout.

The adapter implements the workflow already declared by
`scenarios/distributed-spatial-memory-v0.1/*.toml`:

- `GET /v1/health`
- `GET /v1/readiness`
- `POST /v1/executions` with `operation` set to `build-map`, `publish-map`,
  `import-map`, or `verify-localization`
- `POST /v1/executions/status`
- `POST /v1/executions/cancel`

It uses Python's standard library only. No package installation on a Jetson is
needed. Local execution status is durable in SQLite so a Node Service restart
can continue status reconciliation without replaying a Robonix request.

`GET /v1/health` proves only that the Mapping WebUI `/api/state` endpoint is
reachable. `GET /v1/readiness` separately runs one startup-fixed, read-only ROS
service-list command and reports exact readiness for all four canonical
contracts. Build requires `/rtabmap/set_mode_mapping` plus map and artifact
storage; localization verify requires `/rtabmap/set_mode_localization` plus map
storage; publish requires artifact storage; import requires both storage roots.
Failure to discover a required service, including a broken Zenoh discovery path,
fails only the dependent contract closed.

The adapter calls Robonix Mapping WebUI on loopback (`/api/save`, `/api/load`,
and `/api/state`). It never publishes a catalog record or chooses a map
revision. RoboGuide Node Service owns digest verification, artifact staging,
manifest publication, and replica evidence.

## Run on a node

```bash
python3 robonix-map-service.py \
  --port 18101 \
  --map-root /home/nvidia/Desktop/robot-deeprobotics-lite3/rbnx-boot/cache/service-map-rbnx/maps \
  --artifact-root /home/nvidia/roboguide/dog-a/artifact-cache \
  --state-db /home/nvidia/roboguide/dog-a/map-adapter.sqlite3 \
  --ros-service-list-command 'docker exec robonix_mapping ros2 service list'
```

Dog 2 uses port `18102`, its own artifact/state root, and the same Robonix map
root layout. Bind the service to loopback; the Node Service is the only caller.
The command is split into argv and invoked without a shell. If the deployed ROS
environment needs setup beyond this known container command, provide a
deployment-owned wrapper executable; never accept a command from an execution
request.

## Controlled existing-map package

For a stationary transfer smoke test, an invocation may include
`parameters.source_map_id` to package an existing local Robonix map. This is an
explicit test hook and does not replace `build-map`: a real build without that
parameter calls Robonix `/api/save` using `parameters.local_map_id` (or the
canonical `map_id`).

`parameters.local_map_id` may also be supplied for import/verify when the local
directory name should differ from the immutable catalog `map_id`.

Successful imports persist `local_map_id` and the staged artifact SHA-256 in the
adapter SQLite database. Repeating an import is a successful no-op only when
the target map remains valid and the recorded digest is identical. A different
digest, or a pre-existing map without adapter provenance, remains an immutable
conflict.

## Safety boundary

The service rejects path traversal, symlink and special tar members, archives
outside the configured artifact root, malformed SQLite maps, incomplete map
bundles, and unproven overwrites of an existing local map. It is not an
authentication or transport-security layer; those remain deployment concerns
outside this experimental adapter.

The current localization verification calls `/api/load`, requires a successful
response, and then checks `has_map=true`. This is a smoke-level execution fact,
not evidence of active-map identity, localization mode, pose quality, or shared
coordinate-frame alignment. Readiness discovery does not close that evidence
gap.
