# Recovery profile v0.11

This explicit profile preserves v0.10 Catalog/lineage closure, Memory SQL v4 and Replica storage
v3. Its Artifact components require storage v2 and the exact mandatory original maintenance
journal. Quiescent recovery holds every original writer lease before acquiring any Snapshot pin
or reading source content. The actual copy producer first persists an immutable operation,
actual owner, full original content set, first kernel deadline, UID and complete copy purpose.
Original command IDs cannot start another copy, even after an uncertain process exit.

Bundle v0.3 includes every original Artifact journal file and exact producer descriptors, alongside
the unchanged Memory Snapshot descriptors. Verification checks original identity, full active
reference history and exact same-owner CAS bytes. Only the verified original complete publication
allows its source-side release receipt. Readonly reconciliation copies zero files; uncertain
partial copies retain pins until explicit all-writer abort. Aborted history never permits another
copy with the original command ID. No deadline or provider permission is renewed.

Restore receipt v0.11 retains original journal/owner authority. The restored snapshot still contains
its original active intent, even if the original source later released it. Missing identity,
intent, active content or descriptor refuses verification, including after re-pinning the manifest.
Older tools/profiles reject the new format rather than omitting the maintenance journal.

This is declared-store software/file recovery. It starts no service, Node, device, simulator or
model and provides no online majority/content replication, physical continuity or global GC proof.
Archive/Raft Snapshot producers, Artifact GC and full P1-P7 remain required separately. See
[ADR-0151](../../../docs/decisions/0151-original-artifact-maintenance-reference-journal.md).
