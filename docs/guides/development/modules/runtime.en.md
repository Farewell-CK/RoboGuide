# Distributed Runtime

Maintains logical slots, physical attempts, generations, durable intents, ordered facts and relation fences for committed execution.

## Responsibilities and key objects

RuntimeExecutionManager, DispatchIntent, ExecutionStatus, ExecutionSlot, relation reducers and checkpoints.

## Main flow

Prepare exact attempts/intents → application commit → Node routing → identity-bound fact reduction. Unknown/relation violations fence success and continuation; stale actions never replay automatically.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/runtime/src/execution/mod.rs): `core/runtime/src/execution/mod.rs`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/runtime/src/relation/manager.rs): `core/runtime/src/relation/manager.rs`.

## Implementation status

[Status](../../../reference/implementation-status.md) records actual paths and remaining work; [Reference](../../../reference/contracts-and-build.en.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../../../architecture/v2/architecture-tour.md) · [Concepts](../../../architecture/v2/concepts.md) · [Lifecycle](../../../architecture/v2/execution-lifecycle.md) · [Integration](../../integration/README.md) · [State and Memory](../../../architecture/v2/state-memory.md)

## Detailed flow reference

### Stable slots and formal dispatch

<!-- diagram:recovery:en -->
![Unknown, stopping and recovery](../../../images/architecture/2026-11-26/recovery.en.png)

A `(Group, Task, Role)` slot may retain multiple historical attempts. New attempts never overwrite original sessions/generations/results. Delivered means proven Node acceptance, not satisfaction.

`requires-active` reduces current execution state; `shared-spatial-reference` checks current attempt/owner/map revision/frame. Violated/Unknown fences need explicit acknowledgement even if the relation recovers. Peer readiness observes channels already established by Local EAIOS; Node Protocol carries no high-frequency peer data.

Controller HTTP (PR #40) exposes `GET /v1/missions/{id}/shared-view?context_id=...` (classifying freshness as `Fresh`, `Stale`, or `Unknown`) and `POST /v1/missions/{id}/relations/{relation_id}/reconcile` to clear `relation_fences` once conditions recover. Across the four coupling modes (ADR-0026/0027), `TightlyCoupledCooperation` (Mode 4) keeps only the `PeerChannel` lifecycle (`Planned -> Ready -> Fenced -> Closed`) in Runtime while high-frequency peer data and immediate safety stay directly between Local EAIOS endpoints.

Restore clears process-local session anchors and retains nonterminal ambiguity as Unknown. It neither resends physical operations nor refreshes receive time.
