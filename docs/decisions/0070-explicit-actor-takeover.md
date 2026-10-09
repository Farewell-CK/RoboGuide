# ADR-0070: Explicit Mission Actor Takeover After Confirmed Stop

- Status: Proposed for implementation validation
- Date: 2026-10-09

## Context

ADR-0007 and ADR-0040 deliberately forbid ordinary Role recovery from changing an existing
`ActorBinding`. Confirmed-stop recovery can safely repeat an intact operation on the same physical
owner, but it cannot make a different robot authoritative. The E2 fault study needs a separately
reported extension in which Dog-A is preferred and a registered Dog-B may take over only after the
current attempt is stopped and same-owner recovery is unavailable.

A second Node name is not a second robot. A takeover therefore needs deployment-owned entity
identity, exact routing topology, an executor-neutral operation, current capability/resource facts,
and an explicit operator authorization. It must not be inferred from model output or heartbeat loss.

## Decision

Control adds a one-shot `ActorTakeoverAuthorization` scoped to the exact Group, Task, Role and
logical Actor. It records the previous Node/entity, replacement Node/entity, exact physical registry
identity and revision, and a canonical SHA-256 evidence reference. Authorization performs no
Matching, reservation, Actor mutation or execution.

Authorization is rejected unless:

- the Group is Blocked and the exact role is unbound through the existing recovery transition;
- the role has an existing ActorBinding and the source entity routes through its current Node;
- source and replacement are distinct registered entities under one current registry revision;
- the Mission Actor is not immutably grounded to the source entity;
- no fixed single-Node placement remains installed, while any candidate restriction explicitly
  includes the standby Node;
- the standby is not already authoritative for another Actor in the Mission; and
- the supplied evidence reference is a canonical SHA-256 identity.

The authorization is carried through the existing
Match -> Schedule -> Proposal -> Commit -> Rebind pipeline. Matching contains only its exact standby
Node and still checks live eligibility, operation support and recovery declarations. Commit rechecks
the authorization and reserves resources through the existing authority. Rebind consumes the
durable commitment and atomically changes the role assignment and ActorBinding, then emits
`MissionActorTakenOver`. Failed validation leaves the Actor binding and Group unchanged.

The application must retain current-attempt physical-stop proof and repeat authorization. This ADR
does not treat `Unknown`, heartbeat loss, process death, a Cancel receipt, or Node registration as
stop proof. A later application HTTP command will bind those Runtime facts to the Control
authorization; until that command exists, Core support is not an end-to-end recovery claim.

## E2 deployment boundary

Official COHERENT `env4/task17` contains one dog. Dog-B testing is therefore an explicitly named
graph-level extension or a separate physical deployment and is never included in official benchmark
success-rate tables. The extension uses two independent entity registrations and executor-neutral
operation semantics. It must not alias Dog-B to Dog-A, copy world state, or rewrite an accepted
agent-specific operation inside the adapter.

## Compatibility

Ordinary recovery APIs and existing checkpoints remain valid; the new optional authorization field
defaults to absent. Existing role recovery still cannot cross Actor authority. Checkpoint restore
requires the exact registry revision named by a pending takeover commitment before Rebind.
