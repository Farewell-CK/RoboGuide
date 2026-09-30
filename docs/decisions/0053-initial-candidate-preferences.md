# ADR-0053: Bounded initial candidate preferences

- Status: Proposed for review
- Date: 2026-09-30

## Context

ADR-0052 archives positive static route witnesses alongside bounded misses and
unavailable observations. Ignoring positive evidence can give an initial Task
an endpoint with no observed route while another currently eligible endpoint
has a witness. Choosing the shortest route independently can also consume the
only witnessed endpoint for another concurrently ready Task.

## Decision

Add an explicitly enabled, deployment-owned projection:
`roboguide.deployment-initial-operation-preferences/v0.1`. It carries exact
canonical operation parameters, Node identities, optional nonnegative integer
costs, and the digests of the reset observation and existing feasibility source.
The Local EAIOS converts observations to data; it never accepts a MissionPlan,
chooses an Actor binding, or changes a model action. A missing witness is a null
cost, never a negative eligibility claim.

The feasibility catalog covers all declared entities and operation aliases; the
route observer covers only the direct official goal entities. The projection
retains full catalog coverage with null costs outside that probe scope. Every
probed entity must still cover every endpoint. Alias records may reuse that exact
entity/endpoint witness because the deployment declares the same navigation How.

The Controller composition validates this bounded projection against its
feasibility source and original reset route artifact. Recomputed projection
digests cannot hide changed costs, coverage, source ownership or reset identity.
For the current supported two-endpoint independent profile,
it compares at most four initial endpoint combinations. It prefers more positive
witnesses before total witnessed cost, with stable identity ordering for ties.
Distinct endpoints here express the deployment's concurrent capacity-one slots,
not a new Mission requirement for distinct Physical Entities. A single Actor's
later Tasks do not use reset costs. Only dependency-free initial Tasks participate.

Control accepts generic per-Task/Role candidate ordinals with source attribution
and a local receive-time validity window. Matching still produces the eligible
CandidateSet. The normal Scheduler only changes its order of exploration; every
choice still checks current resource occupancy, timing, explicit placement and
Actor continuity. Unknown candidates remain eligible, and resource shortages
retain the existing durable deferral and recovery behavior. Preference data
never commits, binds, rewrites an intent, or proves Task satisfaction.

This consumer is default off. B1 requires the observer and checks its original
artifact before producing the projection. A fresh Controller may use the source
for its first accepted Mission, for at most ten minutes after startup. After the
first successful Task Bind, all reset preferences for that Mission are discarded
before dispatch; later Tasks, recovery and checkpoint restore cannot reuse them.
Future scheduling outside the validity window uses the ordinary stable order.
Direct Controller configuration supplies both
`ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_PATH` and
`ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_SOURCE_PATH`; partial configuration
fails startup. Restoring a Controller disables the startup consumer rather than renewing old
evidence. A configured invalid source fails startup; absent optional evidence
does not change ordinary deployments.

## Boundaries and limitations

The existing floor exclusions, MissionPlan, MI inputs, Node Protocol, resource
authority, official goal, satisfaction and Formal admission rules are unchanged.
The observer itself remains diagnostic-only; this separately declared consumer
is an experimental initial scheduling policy, to be disclosed in comparisons.

A static witness is not dynamic reachability or success. A bounded miss is not
impossibility. Ranking does not reserve the other Task's preferred endpoint and
cannot override current resource availability. The fixed deployment and fresh
run source checks are assumptions, not cryptographic sensor authentication.
This is not an automatic LLM replanning or terminal-failure reassignment loop.
No benchmark improvement is claimed by deterministic tests.
