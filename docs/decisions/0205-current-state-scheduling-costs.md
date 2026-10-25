# ADR-0205: Current-session State scheduling costs

Status: Accepted

## Decision

The existing scheduling cost structure was reachable only through manually built snapshots. Normal
Mission and recovery scheduling must consume the same explicit current source contract. Costs
order already eligible candidates; they cannot create capabilities, reservations, quotas or stop
permission.

Decision policy registry v0.2 adds bounded `scheduling_costs` beside unchanged eligibility gates.
Each policy freezes tenant, configured Node, source object/channel/schema, TTL, confidence/conflict
reducer, metric units/weight/bound and lower/higher preference. One enabled interpretation per
candidate is allowed. Only original current-session observations through that Node qualify.
Missing, stale, conflicting, invalid, out-of-range or unsupported required measurements have no
numeric cost. Current revocation/session loss immediately removes ordering permission. No model,
operator input timestamp or catalog hint can become a measurement.

Normal and recovery schedulers load these assessments, preserve CandidateSet confinement and
existing deterministic fallback, and retain original records, normalization policy and decision
clock. Actual selections emit the existing durable `StatePolicyDecision` audit with `schedule`;
condition success means a usable numeric ordering value, not eligibility or resource authority.
Future calendar decisions additionally retain the complete assessments and validate exact original
registry history on admission/restore. Lookup and restore never refresh original receive times.
Explicit legacy snapshot costs remain compatibility input, with available GPU memory ordered in
the correct direction. An explicit unknown current policy cannot borrow a legacy score.

The reference local provider offers readonly `/v1/scheduling-cost`, sampling actual OS host
one-minute load divided by reported logical CPU count. It is host load, not worker utilization or
cgroup enforcement. Queue/travel/traffic/GPU remain null because this provider has no corresponding
measurement. The deployment chooses whether to export this channel and which measured terms to
weight. A `GET /v1/missions/{id}/scheduling-costs` view uses existing Mission/tenant authorization.

## Compatibility and validation

v0.1 registry serialization omits the new empty field, preserving old digests; it cannot carry a
cost policy. Accepted v0.2 cannot downgrade, remove retained policies, reattribute candidates or
change interpretation without its own policy revision. Older binaries reject v0.2's closed registry
schema/field and the new closed decision enum rather than writing through new cost provenance.
No new Node wire/config or Controller wrapper generation is necessary: costs use existing State
exports and the independently closed registry interpretation. There is no hidden allocation or
execution authority in a compatibility envelope.

The maintained actual file-process gate gains `--scheduling-costs`, composing owned local providers,
two actual enrolled software Nodes, mTLS, original State records, SQL audit and complete service
restarts. Offline cases cover alternative ordering, unknown/stale/session/tenant fences, explicit
units, overflow, downgrade and original audit. Zero devices/models/simulators are created. This
closes the cost-source mechanism, not divisible allocation, complete time/space commitments,
preemption, hardware telemetry coverage or overall SoftwareComplete.
