# ADR-0027: Runtime Coordination Evidence Completion

## Status

Accepted

## Context

MissionPlan v0.4 can declare several typed execution relation families, a shared spatial reference,
and a tightly-coupled peer channel. Before this decision, only `RequiresActive` had a complete
evidence reducer. State checkpoint restore also rebased every record's `received_at`, which could
make expired evidence appear fresh after Controller restart. Peer channel `Ready` was an
unidentified composition call rather than proof from both Local EAIOS endpoints.

## Decision

State record restore preserves the original RoboGuide-local `received_at`. TTL/freshness is always
evaluated from that persisted receive time; restart never refreshes an observation. Node liveness
and leases retain their existing conservative restart behavior and are not State-record TTLs.

`SharedSpatialReference` is the first typed relation after `RequiresActive` with an executable
Runtime reducer. The existing strong localization evidence contract supplies exact Group, logical
Task/Role, execution attempt, physical Node, immutable `MapRevisionSelector`, and map frame.
Runtime admits it only for the current attempt and current owner, then derives `Satisfied`,
`Violated`, or `Unknown` and reuses the existing latched relation fence and reconciliation flow.
Rebind removes prior-attempt evidence. Checkpoint restore retains the evidence but restores
nonterminal execution status to `Unknown`, so it cannot silently restore permission.

The Artifact/Spatial Memory path remains the durable source of strong localization evidence.
Integration Server projects durable evidence into Runtime and persists the resulting Runtime
checkpoint. Startup rehydrates current-attempt evidence from the typed map catalog to close the
two-commit crash window. Historical attempts remain discoverable Spatial evidence but do not
occupy a live relation slot. Group shared views expose the same current-attempt evidence and an
explicit `Verified`, `Mismatched`, or `Unknown` comparison with their declared map/frame.

Node Protocol v0.3 adds a management-sequenced `PeerChannelReadiness` fact. A Local EAIOS or its
deployment adapter establishes the actual peer transport and publishes one acknowledgement for its
logical `ContextRole`. Node config v0.6 exposes this as a fixed, owner-qualified, periodically
sampled HTTP GET observer; Local EAIOS responses cannot select their registered owner or evidence
lifetime. A bounded response is observation only and is never a setup command. Node Service
attaches the current session and sequence. Controller admits
the fact only when its Node, registered `LocalSystemId`, current committed ContextRole binding, and
canonical capability owner agree. Each acknowledgement has a bounded receive-relative lifetime.
Runtime marks the channel `Ready` only when every declared peer has a non-expired confirmation for
the same channel instance, profile, and message schema. A negative acknowledgement, expiry,
conflicting instance, route loss, or Controller restart fences the channel. A bound Task awaiting
this evidence remains durably Ready and is dispatched by the existing event loop after readiness;
Mission submission is not rolled back. RoboGuide never carries peer data-plane traffic and never
implements formation, grasp, relative-state, motion, or safety control.
Complete Node registration changes also invalidate that Node's prior endpoint proof because the
registered LocalSystem capability owner may have changed. After expiry, route loss, or registration
change, affected endpoints must prove the same instance again and every peer must still have a
non-expired confirmation before the channel may return to `Ready`.
Node Service treats a lagged local readiness stream as session-fatal, making route loss fence stale
positive evidence instead of silently dropping a negative transition. When application
orchestration makes the Mission terminal, Runtime closes the Group's peer descriptors and discards
their live acknowledgements; this does not imply a RoboGuide-owned transport-close command.

This version does not add a Controller-to-Node channel-setup command. The accepted Context,
committed bindings, and observable peer descriptor are RoboGuide's orchestration context; the
deployment adapter decides how those identities reach its Local EAIOS and must establish/renew the
channel idempotently. The configured observer reports the result; failure/omission produces no
renewal, so Controller TTL fencing remains authoritative. A future setup/close command requires its own delivery, retry, recovery, and
node-side workflow authority rather than being implied by this readiness fact.

MissionPlan schema continues to reserve future typed relation descriptors. A Controller-owned
`SupportedMechanismProfile` separates contract validity from implementation support. The current
profile accepts `RequiresActive` and `SharedSpatialReference`; `GroupMemberState`, `RelativePose`,
`RelativeDistance`, `StateRequirement`, and `FreshnessRequirement` fail before Control creates a
Group. Mission Intelligence applies the same early preflight after model/fixture output.

The inner Controller checkpoint advances to v11 and accepts v10 for one-step migration. The server
wrapper advances to v12 and accepts v11. Admitted peer acknowledgements use
`domain.EventPayload.json/v9`, while v2-v8 evidence remains readable. Peer acknowledgements and
shared spatial evidence are Runtime projection state; State does not store Memory blobs, Artifact
remains opaque bytes, and Control remains the only commitment/recovery authority.

## Consequences

Typed spatial coupling now has a real `State/Spatial evidence -> Runtime relation -> fence/allow`
path without a general graph engine or control DSL. Valid plans can no longer reach execution when
their relation mechanisms are merely syntactic reservations. Direct peer readiness is identified
and two-ended while its high-frequency semantics remain local and heterogeneous.

This version deliberately does not implement a RoboGuide P2P transport or the remaining typed
relation reducers. Readiness lifetime is based only on RoboGuide receive time and does not compare
Local EAIOS clocks; richer provider-defined health semantics belong to a later protocol decision.
