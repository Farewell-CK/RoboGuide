# Mission Intelligence

Turns user objectives into clarified, Reviewed and approved complete plans while retaining Requests, raw provider responses and provenance.

## Responsibilities and key objects

RequestEngine, GroundingContextSnapshot, Planner/Reviewer/Repairer; runtime work claims, nonces and original objectives.

## Main flow

Freeze grounding/policy → interpret → draft → preflight/Review → bounded repair/approval → submit. Interrupted submission/provider outcomes remain uncertain and use read-only original-result observation.

## Code entrypoints

- [Primary entry](https://github.com/Farewell-CK/RoboGuide/blob/main/mission/src/mission/request_engine.py): `mission/src/mission/request_engine.py`.
- [Collaborating entry](https://github.com/Farewell-CK/RoboGuide/blob/main/mission/src/mission/runtime_replanning.py): `mission/src/mission/runtime_replanning.py`.

## Implementation status

[Status](../../../reference/implementation-status.md) records actual paths and remaining work; [Reference](../../../reference/contracts-and-build.en.md) generates versions. A module's existence does not establish full stage or device acceptance.

## Validation and diagnosis

Inspect original identity/plan/attempt, current policy and deadlines before upstream/downstream transactions and receipts. Rejection, timeout, replay and restart preserve original evidence. Explicit software configurations, FakeNodes and protocol fixtures are not devices.

## Related boundaries

[Architecture and authority](../../../architecture/v2/architecture-tour.md) · [Concepts](../../../architecture/v2/concepts.md) · [Lifecycle](../../../architecture/v2/execution-lifecycle.md) · [Integration](../../integration/README.md) · [State and Memory](../../../architecture/v2/state-memory.md)

## Detailed flow reference

### Requests and runtime work

<!-- diagram:mission-planning:en -->
![From conversation to a reviewed plan](../../../images/architecture/2026-11-26/mission-planning.en.png)

Missing current Nodes is a scheduling condition, not permission for Interpreter to invent entities or reject semantics. Retain draft errors separately from provider/HTTP/source failures; regeneration uses only the original bounded draft budget.

Runtime workers discover original Requests from committed Generating tickets filtered by stable principal/tenant. Original claims/nonces and SQL intents precede provider I/O. Uncertain calls use read-only reconciliation; replacing stores/nonces cannot repeat them. Candidates/advice grant no Control permission; independent Review and original-goal proofs remain mandatory.

Code paths: `request_engine.py`, `plan_validation.py`, `runtime_replanning.py`, `runtime_work_store.py`.

For interactive operations, `roboguide_workbench` (`roboguide chat`) provides a native agent loop, device `/state` queries, `/cancel` confirmation, mission diagnosis, and runbooks (PR #34 / ADR-0251–0256); MI also includes prompt stability, transport/field latency optimizations, and request provenance capture (PR #28/#29/#31).
