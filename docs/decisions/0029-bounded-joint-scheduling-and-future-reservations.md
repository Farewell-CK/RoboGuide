# ADR-0029: Bounded Joint Scheduling and Future Reservations

## Status

Accepted

## Context

The first Control Scheduler selected one Node and at most one exclusive resource per Role with a
stable greedy policy. It established the `Who can` versus `Who should` versus `Commit` boundary, but
could not backtrack across a multi-Role Task, express more than one resource demand, choose a start
window, or retain future intent. As a result, Capability x Compute x Space x Time was an architecture
claim rather than an executable scheduling path.

RoboGuide already has authoritative current reservations, Mission-level Groups, Task DAG readiness,
durable Controller checkpoints, an application timer, and recovery Matching -> Propose -> Commit ->
Rebind. A first scheduling completion must reuse those boundaries instead of introducing a second
resource authority or placing a general optimizer inside Runtime.

## Decision

MissionPlan v0.5 adds `resources[]` to each Role and `timing` to each Task. A resource demand selects
one exclusive Node-advertised Resource of the requested kind whose declared capacity is at least
`units`. `units` is a sizing gate, not divisible quota accounting. Timing values are millisecond
offsets from durable Controller Mission acceptance: earliest start, optional latest start, optional
completion deadline, and optional estimated duration. Estimated duration is planning evidence and
never proves physical completion.

Only DAG-Ready Tasks enter scheduling. `BoundedJointScheduler` consumes exact Candidate Sets plus an
immutable Control calendar snapshot. It performs deterministic bounded backtracking over Role,
candidate Node, resource combinations, and earliest feasible half-open interval. It never broadens a
Candidate Set, re-runs health/lease/capability eligibility, reads State allocation projections,
commits a resource, mutates a Group, or interprets `ExecutionIntent`. The search budget is fixed and
an exhausted budget is an explicit result, not permission to fall back to the retired greedy policy.
Recovery uses the same deterministic Role/resource selection primitive but does not create future
recovery reservations in this version. It receives the same immutable calendar snapshot: because a
recovery attempt has no planning duration in v0.2, its open-ended physical occupancy cannot select a
resource that already has a future commitment.

Control remains the single commitment authority. It retains a versioned durable calendar of
Task-scoped scheduling records with `Scheduled`, `Activated`, and `Invalidated` phases. Admission
revalidates snapshot generation, current physical occupancy, Context-owned binding reuse, Group/Task
identity, and interval conflicts. A future decision creates no physical attempt and does not make
the Task Active. When due, application orchestration re-runs current Matching and normal Proposal ->
Commit -> Bind. Runtime dispatch occurs only afterward. Although a future interval is not physical
ownership, every normal and recovery Commit path treats another Task's interval as a conflict; no
commit path may silently displace it. A scheduled Task may commit only through its owning Group with
the exact retained decision.

Each decision retains an inclusive latest activation time derived from both latest-start and
completion-deadline minus estimated-duration. Control rejects activation before the selected start,
at or after the selected interval end, after that activation bound, or before exact committed Role
assignments exist. If an interval expires while its wider start window remains feasible,
orchestration invalidates and replans it. Once the activation window is missed, the Task remains
Ready but emits one durable `window-missed` deferral reason rather than repeated evidence on every
timer tick. It leaves automatic timer dispatch until explicit Mission policy revises, cancels, or
fails the work. A changed retryable reason or later feasible decision updates or clears that state.

An activated interval coexists with the existing physical reservation: the physical reservation is
ownership authority, while the interval is planning evidence. Before its estimated end, another
Ready Task may be planned after it. If the physical Task remains active beyond that end, Control
projects the resource as open-ended occupied; conflicting future work cannot activate or bypass the
overrun. Running work is never preempted. Terminal Task/Mission handling removes its scheduling
records, and cancellation releases future intervals before physical cancellation completes.

Context-scoped resources preserve existing semantics. A later Ready Task may reuse an exact
Control-owned ContextRole Node/resource binding; the Scheduler receives that binding as a hard
constraint rather than treating it as foreign contention or selecting an alternative.

Mission acceptance validates that all relative timing remains representable in Controller time.
Mission and Control checkpoints retain Mission acceptance time, the last per-Task deferral reason,
scheduling decisions including latest activation, phase, and calendar generation. The inner checkpoint advances to `roboguide.controller-checkpoint/v13`; the
server wrapper advances to v14, each accepting its immediately previous version. Historical
MissionPlan v0.2-v0.4 inputs normalize to immediate timing and one unit for legacy
`resource_kind`; Mission Intelligence emits v0.5. Scheduling lifecycle events and a read-only
`/v1/scheduling-reservations` endpoint provide inspection without becoming another authority.
Integrated restore recomputes each decision's earliest start, planned end, and latest activation from
the accepted Mission timing; Control-only structural validation cannot widen that contract.
Resource units are a Controller admission constraint; the Node continues to receive the existing
exact committed resource IDs, so this slice does not add scheduling authority or a new reservation
message to Node Protocol v0.4.

## Consequences

RoboGuide now has an executable joint placement and future reservation slice across Capability,
resource sizing, Space/Compute/Time categories, and Task time windows. Multi-Role placement can
backtrack out of stable-order dead ends, future Ready Tasks survive restart, due decisions are
revalidated, cancellation removes future claims, and overruns fail closed without preemption.
Deferred and window-missed outcomes remain observable Ready work until an explicit Mission policy
decides whether to retry, revise, cancel, or fail them; the Scheduler does not own that policy.
Expected scheduling deferrals are isolated per Task by the application timer and never terminate
the process-wide liveness driver; an irreversible window miss is not automatically retried.

This decision does not add fractional capacity sharing, priorities, fairness, cost optimization,
travel-time estimation, topology-aware spatial routing, batching across non-Ready Tasks, auctions,
RL/LLM scheduling, multi-Role joint recovery, or distributed clock synchronization. Those require
typed evidence and separate policy decisions. The bounded deterministic search is replaceable
behind the same CandidateSet/decision/Control admission boundary.
