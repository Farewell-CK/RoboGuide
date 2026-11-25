# State, Memory and Artifact

Stores attributed State, nonauthoritative projections, events/checkpoints and rebuildable catalogs. Artifact Store keeps opaque bytes; original providers retain content semantics.

## Responsibilities and key objects

StateRecord, Allocation projection, Memory/Map catalogs, SqliteEventLog and ArtifactBlobStore.

## Main flow

Application-accepted observations become source records consumed by explicit Control policy. Memory follows authorized prepare/import/use/revoke; recovery reconciles CAS and active references.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/state/src/lib.rs): `core/state/src/lib.rs`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/artifact-store/src/lib.rs): `core/artifact-store/src/lib.rs`.

## Implementation status

[Status](../../../reference/implementation-status.md) records actual paths and remaining work; [Reference](../../../reference/contracts-and-build.en.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../../../architecture/v2/architecture-tour.md) · [Concepts](../../../architecture/v2/concepts.md) · [Lifecycle](../../../architecture/v2/execution-lifecycle.md) · [Integration](../../integration/README.md) · [State and Memory](../../../architecture/v2/state-memory.md)

## Detailed flow reference

### Read projections and content gates

<!-- diagram:state-memory:en -->
![State, Memory and spatial content](../../../images/architecture/2026-11-26/state-memory.en.png)

State API federates original Desired, Committed, Reported, Observed, Derived and Belief authorities without another write entrypoint. Allocation may lag and cannot grant or revoke Control ownership.

Artifact filesystem code handles digests, paths, streaming and durability. Application writers and original-provider lifecycle gates enforce content authority. Existing bytes cannot authorize aliases; Imported history cannot authorize a new Task.

Compact provider use/worker/map/handoff references conservatively gate GC. Recovery validates active refs against the same provider CAS; released history does not require already deleted bytes.

Storage v3 (`controller_v3_events`, ADR-0244) deduplicates telemetry payload bodies within the original event transaction. Paired with ADR-0257 process-local heartbeat renewal and ADR-0258 `ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY` change/interval persistence, `GET /v1/state/records` retains fresh live projections while cutting redundant WAL writes. Multi-robot mapping artifacts flow through `:8090` CAS and `POST /v1/maps/{id}/replicas` (PR #18/#25) with exact receiver and session provenance.
