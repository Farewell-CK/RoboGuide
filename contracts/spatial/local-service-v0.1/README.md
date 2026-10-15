# Spatial Local How service v0.1

Deployment-owned file computation; no Control, Runtime, Task, localization or device authority.
Transport is fixed HTTP over an absolute Unix socket, mode `0600`; no remote listener. The maintained
Node HTTP driver uses `unix:///absolute/backend/local.sock`. The backend exclusively locks its root.

The closed profile has `schema_version = roboguide.spatial-local-service/v0.1`, `max_jobs` (1–4096),
`max_active` (1–16), and `profiles` (1–128). Each profile has exact `profile_id`, `map_id`, `revision_id`,
`spatial_anchor_id` and a complete [file job](../file-provider-v0.1/README.md). The native `job_id` equals
the semantic revision. Slot and `(map_id, revision_id)` are unique. No input supplies a Node selector
or a successful computation result. The backend freezes source/output roots, config/code digest and
an actual persistent UUID; changed code/profile permits only explicit `--readonly` observation.

| Route | Request / result |
| --- | --- |
| `GET /v1/instance` | Original backend UUID and frozen roots/config/code identity |
| `GET /v1/health` | Actual software facade availability; no device claim |
| `GET /v1/readiness` | Original recovery fences and bounded CPU job availability |
| `POST /v1/executions` | `operation`, exact `attempt_id`, canonical `invocation`, `artifact_path` **or** selected `artifact_paths` |
| `POST /v1/executions/status` | `execution_id`; readonly original proof |
| `POST /v1/executions/observe` | `attempt_id`; readonly lookup, never admission |
| `POST /v1/executions/cancel` | `execution_id`; actual owned process interruption acceptance |

`operation` is `build-map` or `publish-map`. Invocation operation must be the matching canonical
string `spatial.map.build@v0` or `spatial.map.publish@v0`; parameters bind the exact `map_id`,
`revision_id`, `spatial_anchor_id`, `artifact_slot` and `artifact_operation` (`prepare-output` or
`publish`). Selected output context contains exactly one configured slot. Original request identity
normalizes the selected path; retries cannot change it. Build uses a selector-hashed subdirectory
under the configured Node cache. Publish may use the Node's frozen content-addressed copy, after
rehashing it against original complete native proof. A map revision has one producing attempt.

Admission fsyncs the original request before spawning its one CLI. The CLI snapshots actual source
bytes and its original clock/budget before one native worker. Immutable results must revalidate
source/request/code, bundle hash, manifest and original timing. Service completion also requires
the actual joined original PID, not a terminal string. After a facade crash, queries never spawn a
replacement. Work continues only under the original native budget or remains Unknown. Results are
`ACCEPTED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED` or `UNKNOWN`; Node owns lifecycle reduction.

Cancellation returns `cancel_accepted = true` and RUNNING. Only actual original child exit plus the
original software-process stop proof permits CANCELLED. Natural completion and a cancel request
cannot manufacture each other. This is not a device stop certificate or independent safe-state
evidence; Control ownership cannot be released from this local acceptance alone.

The service checks bytes for publication; it does not publish catalog metadata itself. Node Service
owns preparation, CAS transfer and publication. Tests use real OS processes and attributed files;
the loopback process proof does not establish remote provider authority or physical geometry truth.
