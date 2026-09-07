# ADR-0028: Durable Command, Recovery, and Physical Attempt Completion

## Status

Accepted

## Context

RoboGuide already persisted Control, Mission, Runtime, State, and Node execution evidence, but the
Controller could still crash between deciding to dispatch and routing `Execute`. A successful gRPC
send did not prove that the Node journal had accepted the command. Mission cancellation released its
Group immediately, recovery required manual composition calls, physical retries had no explicit
generation history, and lease expiry depended on incoming traffic. Typed localization evidence also
lacked a current Node-session admission boundary on its HTTP path.

Those gaps make the normal path look complete while leaving dispatch crash, restart ambiguity,
disconnect, cancel race, and rebind behavior under-specified.

## Decision

Runtime owns a durable command outbox inside the Controller checkpoint. Application composition
must prepare the immutable `ExecutionIntent`, physical `execution_id`, `command_id`, binding, and
committed resources, commit the checkpoint, and only then ask Integration to route it. Same-process
delivery failures are retryable with the same identities. One unavailable route does not prevent
best-effort delivery to other Nodes.

Node Protocol v0.4 adds immutable command identities and a management-sequenced `CommandReceipt`.
Node Service writes its SQLite execution journal or cancellation tombstone before reporting
`COMMAND_PERSISTED`. That receipt proves command durability only; execution lifecycle remains owned
by ordered execution facts. A rejected receipt reports admission failure but does not claim every
pre-journal validation error was stored by the Node. The Controller accepts either receipt through
the same application-authority and checkpoint boundary as other Node facts and checks its
session-bound Node owner against the attempt. A rejected Execute
becomes Runtime `Unknown` and enters reconciliation rather than becoming a terminal Task failure.
Route loss and rejected command receipts refresh affected relations immediately, latching their
reconciliation fences in the same transition as physical ambiguity.

Runtime distinguishes a stable logical slot `(GroupId, TaskRef, RoleId)` from each physical attempt.
Attempt ids contain a durable per-slot generation and remain in immutable attempt history after
replacement. Relations continue to target logical slots and resolve only to their current attempt.
On Controller restore every nonterminal attempt becomes `Unknown`, the dispatch outbox is not
automatically replayed, and the application timer emits one recovery-required transition. This is a
conservative ambiguity fence, not evidence that execution failed.

Mission cancellation is two-phase. Orchestration first moves the Mission to `Cancelling`, blocks its
Group, and stops new Task dispatch while retaining Control ownership. Runtime persists a cancel
intent for every retained nonterminal attempt, including superseded ambiguous attempts. Node Service persists cancel-before-execute tombstones
and suppresses a later matching Execute. A command receipt does not finish cancellation. Only
terminal attempt facts allow application composition to call explicit finalization, fail/release the
Group, release reservations, and mark the Mission `Cancelled`.
Cancellation retries are driven by the application timer, never by the receipt or snapshot of the
previous Cancel; this avoids a nonterminal acknowledgement creating a command feedback loop.

Application composition consumes Runtime `RecoveryRequired` and Node-unavailable evidence, then
invokes the existing Control-owned assessment and recovery pipeline:
Match candidates, deterministic bootstrap scheduling, Propose, Commit, and Rebind. Runtime detects
physical ambiguity but never chooses a replacement or mutates Control ownership. No candidate leaves
the Group blocked and pending; the application timer retries that Control-owned pending need when
later Node evidence changes the candidate set. The current recovery slice handles one unavailable role at a time;
simultaneous multi-role ambiguity is drained over successive application ticks rather than being
misrepresented as a joint optimizer. A Group remains blocked if one tick cannot make progress.

Integration Server owns an application liveness driver. Its receive-time timer expires Control
leases, refreshes peer readiness deadlines, drains cancellation completion, advances recovery and
newly Ready Tasks, checkpoints all authority mutations, and flushes command outboxes only after the
commit. Timer failure is fail-stop. It does not create a second State, Runtime, or Control authority.

Typed Node-authored localization evidence admitted through Artifact HTTP must identify a currently
registered `NodeId` and exact live Node session before durable append. Runtime continues to require
the exact current attempt and Node owner during admission before append, and the map revision and
frame during relation evaluation. HTTP transport, Artifact storage, and
State never gain localization or execution authority from this check.

The inner integration checkpoint advances to `roboguide.controller-checkpoint/v12`; the server
wrapper advances to `roboguide.controller-checkpoint/v13`. Each accepts only its immediately previous
version for migration. Node configuration remains v0.6; contract bundle v0.7 contains Protocol v0.4.

## Consequences

The Controller crash windows now have explicit durable intent and conservative restart semantics.
Cancellation is observable and recoverable instead of being an immediate local state rewrite.
Physical attempt history makes rebind auditable without changing relation endpoint identity.
Application time progresses even when no Node fact arrives, while all commitment and recovery
decisions remain in Control.

This decision does not provide exactly-once physical action, distributed transactions, automatic
replay after ambiguous restart, a general recovery optimizer, multi-role joint recovery, or a new
peer transport. Those require explicit policy and physical-world evidence rather than stronger
claims from message delivery alone.
