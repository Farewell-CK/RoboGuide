# ADR-0212: Coordinated original-content Archive

Status: Accepted for implementation, 2026-10-25.

The application Archive endpoint currently refuses replicated maintenance. A complete Log or
Snapshot can preserve its original Catalog and CAS inputs, but the online writer cannot advance
the original history floor. Independent prefix deletion remains forbidden.

The application State store owns a closed coordinated-Archive journal. Admission freezes the
authenticated global recovery administrator, original Controller/Artifact owners, actual issuing
epoch, complete predecessor, cutoff, exact event/Catalog/checkpoint bytes and one original kernel
budget. Its pending record is accepted through the existing owned-child mTLS ingress and complete
content quorum before any Archive upload. Repeated command IDs are observations: an interrupted
publication never acquires another budget or silently repeats its effects.

The immutable Archive package is actual finalized CAS content in the original application Artifact
store. It contains original metadata, not copies of every historical payload. Qualified original
payload owners remain unchanged. Current publications, active references, uncertain operations and
required proofs continue through the existing complete-input reducer; released history does not
require deleted payloads. Every retained Archive package is itself an independent complete-input
reference. Root Log/View/Snapshot producer rules and actual same-member content quorum apply to
that package before its final acknowledgement.

Archive packages are private control metadata. Ordinary Artifact HTTP reads, including Artifact
administrators, refuse their digests; normal Artifact/Memory publication cannot reclassify a retained
package into public content. Global maintenance/recovery retains its separate original permission.

An explicit complete-content maintenance envelope advances the floor. Validation reconstructs the
exact old prefix, package identity, new protected Catalog, timestamp floor and contiguous manifest
history; it preserves the complete checkpoint, command journal, origins, original uncertain
operations and all other recovery evidence. One Raft application transaction installs the new
recovery projection and deletes only that verified prefix. Normal incremental envelopes cannot
advance the floor. The local application materialization follows the same accepted transaction.
An uncertain response remains original evidence and is reconciled through bounded readonly
observation. Loss of the original epoch or budget grants no new publication permission.

This is storage/maintenance authority, not a Mission or Node contract change. New Archive journal
v0.3, recovery projection v0.2 and complete-content v4 are explicitly fenced. The matching Replica
parent/process and quiescent recovery formats must reject old writers/readers before publication;
existing v0.2 journals and v3 batches retain their original meaning. No implicit migration is
selected from directory discovery. Recovery independently verifies exact package bytes and all
current qualified references; restore starts no service and renews no clock.

Acceptance must exercise actual managed SQL/CAS and mTLS processes, online Archive, readonly
duplicate/lost outcome, SQL rollback, original-epoch loss, Log/Snapshot catch-up, membership,
restart, complete cold recovery and genuine previous-writer refusal. These are software storage
facts. They establish no Node inventory, device stopping, physical continuity, cross-host clock
synchronization or long-term SLO.

The actual purged-Snapshot/member composition exposes retained receive-header refusal and expired
metadata delivery after independent membership/epoch changes. Explicit parent11 therefore records
`source_publication` in each new actual Snapshot body: original software member/Root and the exact
managed SnapshotBuild producer ID. The producer intent and pins precede actual publication. A new
real build has an independent durable invocation and payload identity, even at the same applied
cut; it never invents a log entry, changes application state, rewrites an old receive or renews its
budget. Explicit duplicate build IDs remain the same original operation. This source observation
never imports producer/process authority into the receiver. Older profiles keep absent fields and
their original deterministic payloads. Profile v0.30 independently joins original local producer
records where the source Root is present; receiving publication keeps its own independent proof.

Promotion also requires the leader's actual replication acknowledgement through the original
applied Raft cut before joint consensus. An unchanged application revision/bytes alone cannot
prove a returning voter's membership/Snapshot prefix. This check stays inside the original owned
transition, budget, epoch and caller permission; failure creates no joint entry. A client timeout
is retained and reconciled readonly rather than reissuing membership changes.

A retained pending joint entry can also survive an earlier interrupted promotion. Snapshot-only
source permission may use the exact old committed membership while live metrics point at its
actual durable next joint entry, or that joint's next uniform entry. The source must still be the
current actual leader in the original epoch; complete nodes/Root bindings, original SQL entry,
transition shape and current TLS are rechecked throughout. This permits only the retained Snapshot
and its byte prerequisites. Normal application proposals, Commit, ordinary reads and semantic
execution keep the strict current/committed authority. It does not claim that pending membership
is committed. Original receive records and budgets remain unchanged.
