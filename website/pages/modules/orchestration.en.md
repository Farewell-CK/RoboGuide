# Mission Orchestration

Owns accepted plans, DAG readiness, Task satisfaction, Mission lifecycle, holds and logical dispatch policy.

## Responsibilities and key objects

MissionOrchestrator, MissionExecution and IntegrationRuntimeBridge; the bridge is not a second execution authority.

## Main flow

Submit creates the Group/all Tasks → advance Ready → call Control/Runtime → await satisfaction after local completion → release gates → DAG. Revisions preserve original goals and facts.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/orchestration/src/lib.rs): `core/orchestration/src/lib.rs`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/core/orchestration/src/mission/lifecycle.rs): `core/orchestration/src/mission/lifecycle.rs`.

## Implementation status

[Status](../status.md) records actual paths and remaining work; [Reference](../reference.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../architecture-tour.md) · [Concepts](../concepts.md) · [Lifecycle](../lifecycle.md) · [Integration](../integration-guide.md) · [State and Memory](../state-memory.md)

## Detailed flow reference

### Admission, satisfaction and versioned revision

```mermaid
flowchart TB
  P[Complete MissionPlan] --> A[submit / create_mission_group]
  A --> D[DAG Ready / bounded dispatch]
  D --> C[Control prepare / binding]
  C --> R[Runtime attempt]
  R --> L[Local completion]
  L --> S[Accepted satisfaction basis]
  S --> G[Memory revoke and other release gates]
  G --> D
  S --> F[Final negative evidence / Mission policy]
```

`mission/checkpoint.rs` retains the accepted plan/time anchor; `mission/lifecycle.rs` separates completion and satisfaction; `mission/revision.rs` preserves original goals/version history. `integration_bridge/` composes transport facts with existing Runtime rather than another execution map.

Shortages retain durable deferrals without blocking other Missions. Cancellation, hold, recovery and revision retain their own gates. Terminal evidence alone is not sufficient for every stop or content-revocation requirement.
