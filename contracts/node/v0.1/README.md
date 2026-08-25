# RoboGuide Node Contract v0.1

> Historical artifact. Production Server and Node Service compile and serve v0.2 only.

This directory owns RoboGuide Node Protocol v0.1. `roboguide-node.proto` is the formal gRPC
bidirectional streaming contract. The older HTTP paths below remain reference/debug bindings of
the same transport-neutral Node Contract and are not the production session transport.

## Formal gRPC session

`RoboGuideNodeProtocol.NodeSession` carries ordered streams in both directions. The lifecycle is
Hello -> Welcome -> Register -> Registered, followed by Heartbeat, RegistrationUpdate,
ExecutionEvent, and reconnect ExecutionSnapshot from the Node; Execute, Cancel, Ack, and Error
flow from RoboGuide. CanonicalInvocation never contains local Atlas, Pilot, ROS topic, executable,
or vendor SDK semantics.

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

The synchronous HTTP response is retained only for compatibility with the reference probe. Formal
Node Protocol execution is asynchronous `accepted -> started -> completed/failed/cancelled`, pushed
through the gRPC stream without RoboGuide polling.

Adapters map canonical `CapabilityContractRef` values to Local EAIOS skills, services, primitives, or vendor
APIs. Network callers never supply an executable or shell command.
