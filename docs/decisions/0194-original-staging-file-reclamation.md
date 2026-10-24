# ADR-0194: Reclaim original retired staging files without deleting current CAS content

Status: Accepted; implemented with actual file/process and complete recovery acceptance.

The existing original copy path keeps its staging hard link after publishing CAS. Removing the
qualified CAS name can therefore leave the bytes reachable through staging. Interrupted streams
also retain private partial files. Root7 GC covers finalized qualified objects; it grants no
permission to remove arbitrary staging files or to reinterpret an original Unknown as completion.

## Authority and lifecycle

An explicit Root8 adds staging-GC command and per-item outcome journals plus mandatory command/item
inventories. Old Root7 and earlier entry points refuse Root8 even with zero commands. Fresh create
or a fully drained Root7 upgrade retains the exact original root/database inodes, logical identity,
all old operations, budgets, Unknown and references. No Node, application checkpoint or Raft wire
generation changes are required.

A caller selects at most 64 typed original copy/stream command IDs, never a filesystem path or an
asserted close flag. Under the actual Root writer, the provider derives each private staging leaf
from that original operation. A copy requires its original receive reference to be released and
either its original Completed receipt or independent original process-end evidence. A stream
requires the exact retained Root7 retirement, preserving actual worker close or original process
end. Active/Unknown owners without these proofs refuse the entire admission before effects.

The intent freezes sorted source IDs, complete original-record/release digests, original qualified
key and actual staging device/inode/size or actual absence. It commits with every initially Unknown
item and both inventories before any staging payload open/unlink. Actual regular-file/FD identity,
bounded hashing, unlink and parent fsync precede each small durable outcome. The actual partial-file
digest is an effect observation, never the full original CAS digest or a semantic availability fact.
The file identity is the actual observation at cleanup admission in that retired reserved namespace;
the operation does not infer a historical file birth from a pathname or a restoration receipt.
One queue/scan/effect/SQL clock is fixed before work. Same-ID calls only observe the original record;
failure/restart/absence never fills its old Unknown. A different explicit command can record its own
actual absence or deletion. Original stream/copy outcomes never change.

Only that staging name is removed. Final CAS names, live readers/producers, original qualified-key
tombstones and semantic permissions remain independent. No arbitrary directory sweep is allowed.
Successful copy publication also removes its own private staging link durably under its original
operation before returning, preventing new completed-copy leaks. Interrupted history remains intact.

## Product and recovery

Explicit process v0.6 selects Root8 without changing the original application policy identity.
OS-admin maintenance exposes drained upgrade, bounded typed staging collection and readonly original
observation. Earlier process/maintenance declarations retain their meanings.

Explicit recovery v0.22 validates every original command/item/source/release and mandatory inventory,
retaining original clocks, Unknown and irreversible Root7 GC evidence. Restored Root8 backing uses
an explicit new binding interpretation while keeping all old binding rows. Private staging payloads
are excluded from new v0.22 bundles and restoration. Their historical outcomes remain exact;
missing restored partial files neither complete an old cleanup nor grant use. Restore starts no
service. Old recovery profiles refuse Root8.

Actual bundle conformance exposed the legacy selected-tree copier including a retired private
staging hard link. New v0.22 therefore copies only the empty `staging-v1` directory, refuses declared
files/nested directories there before payload reads during verification, and retains all SQL evidence.
Earlier profiles keep their frozen tree interpretation; their historical bundles are not relabelled.

## Required software evidence

- Original completed-copy hard links and actual nonempty interrupted staging are reclaimed only
  after exact original retirement; active owners and wrong/missing lineage refuse without effects.
- Actual SIGKILL/OS quota interruption, SQL outcome failure and original readonly replay preserve
  Unknown; independent later deletion/absence has its own command and budget.
- Actual current CAS bytes remain readable after staging unlink, while complete original producer
  retention still gates finalized-object GC.
- Drained upgrade, old-entry refusal, complete bundle verification/restore and original operational
  history retention pass with real files/SQL/processes and zero Node/device/model/simulator facts.

These are local software storage facts. They do not establish global erasure, cross-host replication,
physical continuity, device stopping, long-term SLO or overall SoftwareComplete.

Original failures, frozen binaries, actual waits and recovery scope are retained in
[the acceptance record](../development/acceptance/2026-10-24-original-staging-reclamation.md).
