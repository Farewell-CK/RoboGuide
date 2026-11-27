# Integration and Node Service

Integration owns formal gRPC/sessions/routes. Node Service compiles configuration, keeps its journal and invokes generic drivers to reach Local EAIOS.

## Responsibilities and key objects

GrpcIntegrationService, Node router, NodeServiceConfig, LocalIntegrationEngine and ExecutionJournal.

## Main flow

Observe readiness before Register; Registered/Ack waits for durable application acceptance. Execute journals before Local How; duplicates retain original identity/budget.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/integration/src/lib.rs): `core/integration/src/lib.rs`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/node-service/src/lib.rs): `core/node-service/src/lib.rs`.

## Implementation status

[Status](../../../reference/implementation-status.md) records actual paths and remaining work; [Reference](../../../reference/contracts-and-build.en.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../../../architecture/v2/architecture-tour.md) · [Concepts](../../../architecture/v2/concepts.md) · [Lifecycle](../../../architecture/v2/execution-lifecycle.md) · [Integration](../../integration/README.md) · [State and Memory](../../../architecture/v2/state-memory.md)

## Detailed flow reference

### Admission, ACK and local journal

<!-- diagram:dispatch:en -->
![From candidates to durable execution](../../../images/architecture/2026-11-26/dispatch.en.png)

Wire v0.4, semantic Node Contract and config schema are separate version axes. The basic handshake explicitly negotiates v0.6. Memory/safety profiles require their declared contracts, never inferred metadata permission.

The Node journal holds original sibling/inode leases across clones and fences old writers. Local protocol drivers are generic; vendor semantics remain in deployment workflows.

Original receipts, terminals, stop proofs and provider revokes travel as separate evidence. gRPC delivery is not business completion.

For Wi-Fi roaming resilience, Node Service enables HTTP/2 keepalive probes and `CoalescedStateOutbox` merging per `export_id` (PR #19/#27), while Controller scopes superseded registration rejections to the original session (PR #39). Setting `ROBOGUIDE_NODE_PREFLIGHT_CONFIGS` and `ROBOGUIDE_NODE_PREFLIGHT_PROFILE=complete-sources/v0.1` (ADR-0255/0256) validates Node workflow pointers and external descriptors offline or via `/v1/missions/validate-plan`.
