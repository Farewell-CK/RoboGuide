# ADR-0188: Durable network Snapshot content prerequisites

Status: Accepted; Root6 software slice implemented and accepted. No complete A/N1/N3 or
SoftwareComplete claim; joint membership, GC, upgrade matrix and remaining non-release stages stay
separate gates.

The retained-Log path requires a receiver Log and cannot initialize a learner after those Logs have
been purged. Ordinary incremental application still refuses an archived initial history floor or
missing event/store provenance. Those refusals remain; a complete Snapshot needs its own receive
authority rather than fabricated Logs or implicit historical owners.

Explicit content Root6 adds a bounded complete `content_snapshot_receives` journal, mandatory
`snapshot_receive_ids` head and original/restored backing mode. Old Root4/Root5 entrypoints refuse6.
Explicit drained upgrade preserves original identity, all operation rows, clocks, receipts and CAS.
Root6 owns independent `snapshot-receive` pins before any new payload read. The first immutable
header binds actual source member/Root/TLS, exact SnapshotMeta and complete payload digest, original
target/qualified inputs, actual receiver identity and original kernel budget. Serialized readiness
or the Snapshot ID alone cannot authorize receiving bytes.

The real Raft sender first negotiates the receiving Root's supported contract over authenticated
bounded transport. An opted-in receiver durably prepares before SourceWork opens payloads. The source
uses an independently verified actual retained Snapshot scope; current epoch/membership/TLS and its
original scope stay live throughout every chunk. Source and receiver headers and opaque input owners
are independent. Complete actual input receipts/bytes precede a durable prerequisite-ready receipt;
only then are ordinary upstream metadata chunks sent and installed through the original Raft engine.
Receiver readiness and a successful Snapshot application are separate facts.

The full-Snapshot callback freezes one new fifteen-second content-operation budget before queuing;
upstream per-metadata-chunk timeouts keep their original values. Those short segment timeouts cannot
be reused as a complete-content effect duration. Cancellation preserves actual worker ownership and
the original receive budget; another upstream attempt cannot renew that durable issue. A learner's
retention-only permission may precede applied membership, but refuses a superseding local Raft term
or known different leader and rechecks actual current local TLS trust. The source independently
requires its exact current full membership/epoch. Only OpenRaft admits or installs metadata authority.

Factory transport holds only a Weak reference to the actual running server's source context, never
a strong Raft/store ownership cycle. Actual blocking and transfer work owns bounded Root capacity
through cancellation and original process/FD lifetime. Duplicate first IDs return original evidence;
Unknown, expiry and restart cannot create another receive effect or renew a budget. The complete
metadata must match its original payload digest and prerequisite header before installation. Actual
parent Snapshot/view pins and successful original publication precede retiring the receive pin.

This profile is explicit in runnable process v0.4; earlier processes and Root modes keep their old
meaning. No Node, domain checkpoint or application semantic contract changes. New membership/source
permission checks are part of this network Snapshot contract; ordinary committed and Log routes are
not widened. Unsupported peers cannot claim content-ready success.

Explicit recovery v0.20 preserves every original Root6 journal/head and current pin, validates exact
same-Root bytes for completed prerequisites and keeps incomplete receive state without invented
availability. Restored Root6 uses an explicit v0.2 backing binding, preserving raw prior v0.1 history;
old v0.19 keeps its Root4/Root5 boundary. Restore starts no service and renews no original budget.
The v0.20 Root6 component declares mandatory nullable `restore_binding_digest`, independently checked
against original/restored SQL mode and the exact binding file/head. This keeps original Root6 free
of invented restoration evidence and restored backing explicitly pinned in each deployment profile.

Acceptance must include actual learner/snapshot transfer after original Log purge, independent SQL/
CAS and process exits, missing/corrupt input refusal, SQL/response loss and original-ID observation,
source/epoch/certificate change, old-writer refusal, complete cold restore and restart. No device,
physical continuity, cross-host independence or long-term SLO is inferred from this software path.
