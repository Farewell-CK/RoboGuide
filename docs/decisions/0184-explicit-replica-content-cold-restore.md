# ADR-0184: Explicit complete Replica content cold restore

Status: Accepted; implemented. Actual cold data, second restore and three-leader software acceptance passed; full HA/non-release matrices remain open.

## Problem

Recovery v0.18 deliberately refuses parent8/Root4 and Prepared recovery events. Root4 records
its original actual directory/database inodes in every original receiver identity. Copying those
bytes to new storage cannot pass the original open contract, and rewriting that identity would
falsify original readers, producers, streams and Unknown operations.

## Decision

Add explicit recovery-profile v0.19 with a `replica-content` component paired one-to-one with its
original parent8 member. Freeze supervisor, parent SQL inode, Root writer and Root SQL inode before
reading. Require the complete original tables, inventory heads, parent/root/member identity and
every independently active qualified reference's exact bytes. Preserve partial staging and complete
operational history. Verify Prepared recovery records against original source/replacement events
and, where completion exists, the independently matching original Root reader receipt. Missing
mandatory stores, references, bytes or proofs refuse before bundle publication.

Restore never starts services. It copies original operational records unchanged and explicitly
adds Root layout v0.5 restoration history: pinned source manifest/database digest, original identity,
actual new directory/database device/inode and previous binding digest. The bounded history remains
immutable. Original identity headers and original byte/reader/producer/stream receipts stay exact;
old observed file inodes are historical provenance, not current backing facts. The current binding
has its own mandatory file, SQL head and explicit configured digest. Original v0.4 open refuses it.
Only an explicitly selected restored-root entry point accepts v0.5 after full validation and actual
writer leases. A missing/replaced binding or changed actual backing refuses.

The runnable supervisor opts in through process v0.3 plus an explicit local restore-binding digest.
This local storage route does not change application binary/policy, original logical Root identity,
Raft membership or wire content contract. It does not authorize an application binary upgrade.
Startup still checks original child process history, actual exit/absence, current epoch/quorum,
complete qualified content and all existing Controller fences. Interrupted original commands remain
Unknown and never run again; no original clock, permission or capacity is renewed by restore.

Local supervisor status may additionally retain a bounded actual `raft_error`, omitted while absent.
It is readonly infrastructure evidence, never readiness, current use, stop or recovery authority.

## Acceptance and limits

Require actual quiescent three-member parent8/Root4 backup, immutable verification, new-inode restore,
old-profile/old-binary rejection, original history/Unknown equality, actual new process waits and
qualified original-byte reads through restored mTLS services. Also require missing table/head/Root/
byte, mismatched pairing/binding and read-only retry counterexamples. Re-backup/restore must retain
the complete bounded restoration chain. No Nodes, devices, models or simulators are created.

This proves declared cold data recovery and local process restart only. It does not establish a
global deployment lease, cross-host replication, online Archive/Snapshot recovery, binary upgrade,
physical stopping/continuity, SLO or SoftwareComplete. Remaining P1–P7 requirements stay mandatory.
