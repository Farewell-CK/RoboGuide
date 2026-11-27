# Module map and dependency boundaries

Domain holds identities, immutable values and invariants. Ports defines transport-neutral interfaces so policy, storage and Local How can evolve without changing semantics.

## Responsibilities and key objects

IDs, MissionPlan, ExecutionIntent and EventPayload; Clock, EventSink and State/Memory/Artifact ports.

## Main flow

Define identity, lifecycle and write authority before adding a contract, then inject implementations through ports. Vendor SDKs, HTTP DTOs and database rows cannot become domain authority.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/domain/src/lib.rs): `core/domain/src/lib.rs`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/ports/src/lib.rs): `core/ports/src/lib.rs`.

## Implementation status

[Status](../status.md) records actual paths and remaining work; [Reference](../reference.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../architecture-tour.md) · [Concepts](../concepts.md) · [Lifecycle](../lifecycle.md) · [Integration](../integration-guide.md) · [State and Memory](../state-memory.md)

## Detailed flow reference

### Dependency direction

```text
apps → orchestration → control/runtime/state/integration → ports → domain
apps → node-service → integration → ports → domain
apps → artifact-store → ports → domain
apps/controller-replica → controller-replication → state/ports → domain
```

These are allowed responsibility directions, not a claim that every crate directly imports every module in its row. Domain contains no I/O or SDKs.

| Port family | Purpose |
| --- | --- |
| Clock / EventSink | Inject time and immutable evidence |
| SharedNodeState / StateRecord | Source records and current Node evidence |
| AllocationState | One-way projection from Control, without commitment authority |
| MemoryCatalog / MapCatalog | Metadata and provider-qualified replica evidence |
| ArtifactBlobStore | Bounded opaque bytes |
| TaskSatisfactionEvidence | Independent satisfaction evidence, distinct from completion |

Before adding a port, define ownership, stable identity/version and unknown failure semantics, then choose storage or transport.
