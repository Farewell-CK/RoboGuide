# ADR-0012：Controller Projection Checkpoint Recovery

- 状态：Accepted for the Integration Server bootstrap
- 日期：2026-08-25

## Context

The SQLite event envelope is durable evidence, but the current `EventPayload` values do not carry
enough information to reconstruct every Control and Runtime projection (for example, a
`NodeRegistered` event does not contain the complete registration lease, and a plan event does not
contain all assignments). Starting after a crash with an empty Control authority would therefore
discard reservations while physical work may still exist.

## Decision

The Integration bridge persists its versioned `roboguide.controller-checkpoint/v6`
Control/State/Runtime projection inside the current outer
`roboguide.controller-checkpoint/v7` JSON checkpoint in the same SQLite transaction as each
accepted fact and its lifecycle evidence. The checkpoint contains Control commitments, actor
bindings, deployment actor placement constraints, Groups, immutable Task/Role requirements used by
recovery authority checks, pending recovery commitments, Shared Node State registrations, and
Runtime execution contexts/statuses. Its event sequence must equal the event-log tail before startup
recovery is allowed. Outer v6 and embedded v5 make the newly required Group role metadata a
fail-closed schema boundary; it is never reconstructed from a caller-supplied recovery request.

Recovery is conservative across the process-local monotonic clock boundary:

- Control leases are cleared and must be re-established by node registration;
- restored Shared Node State keeps registration and reported health but rebases receive/liveness
  times and marks liveness `Unreachable`;
- nonterminal Runtime executions become `Unknown` and remain recovery-pending rather than terminally failed;
- restored execution IDs are fenced from ordinary routing, and no command is automatically replayed;
- fresh gRPC routes wait for nodes to reconnect and report current facts.

Databases with events but no checkpoint, an unsupported schema, or a checkpoint sequence that does
not match the event tail fail closed. This is a single-controller projection checkpoint, not
event-sourced replay, replication, or a second resource-commitment authority.

## Consequences

- A committed Control projection survives process restart without pretending old leases or physical
  commands are still live.
- Event evidence and the projection advance atomically; a rolled-back fact cannot leave a newer
  checkpoint behind.
- Full event-sourced historical projection and replicated/high-availability recovery remain future
  work and require separate ordering and idempotency decisions.
