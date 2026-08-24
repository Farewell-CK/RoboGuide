# RoboGuide Node Contract v0.1

This directory documents the first HTTP binding of RoboGuide's semantic Node Contract. The
contract is transport-neutral: HTTP paths and JSON fields are reference wire details, not Core
ownership or a requirement that nodes run a RoboGuide agent.

## Registration

`GET /v1/registration` returns `schema_version`, `node_id`, `local_runtime`, coarse
`capabilities`, and declared `resources`. `schema_version` must equal
`roboguide.node.v0.1`; consumers reject unknown versions rather than silently guessing
compatibility.

## Status

`GET /v1/status` returns `node_id`, reported `health`, and `source_observed_at_ms`. The source
timestamp belongs to the Local EAIOS clock. RoboGuide Runtime adds its own receive time. HTTP
failure leaves reported health unchanged and provides evidence that liveness is `Unreachable`.

## Invocation

`POST /v1/execute` carries task, group, role, node, correlation identity, and an `intent`:

```json
{
  "schema_version": "roboguide.node.v0.1",
  "task_ref": {"mission_id": "mission-a", "task_id": "task-01"},
  "group_id": "group-a",
  "role_id": "transport",
  "node_id": "node-a",
  "correlation_id": "trace-a",
  "intent": {
    "capability_contract": {"namespace": "mobility", "name": "move", "version": "v1"},
    "parameters": {"destination": "zone-b", "speed": 0.5}
  }
}
```

The synchronous v0.1 response is `task_completed`, `task_failed`, or `safe_stopped`. Long-running
`accepted -> started -> completed/failed` execution, callbacks, streaming observations, capability_contract
catalog discovery, and transport negotiation are deferred.

Adapters map canonical `CapabilityContractRef` values to Local EAIOS skills, services, primitives, or vendor
APIs. Network callers never supply an executable or shell command.
