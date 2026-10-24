# ADR-0190: Replica content GC intent and exact unlink receipt

Status: Accepted; implemented and software-validated. This is an A2 producer/reference GC slice and does
not claim complete N1/A2 or SoftwareComplete.

Retired Replica producer/receive references currently preserve their immutable history, but their
member-local CAS bytes need an explicit reclamation boundary. Explicit Root7 adds mandatory
`content_gc_commands` and `content_tombstones` SQL tables with complete `gc_ids` and `tombstone_keys`
heads under the original Root writer. Old Root4/5/6 entrypoints refuse7, even before the first GC.
Only an explicit drained Root6 upgrade adds the generation; identity, operations and old budgets stay.
A GC command freezes exact qualified content, the actual original object receipt, complete inactive
reference digest, optional current file device/inode and original nonrenewing budget. The intent,
first irreversible tombstones and both inventories commit atomically before any unlink. Missing
original heads/tables never become empty permission. At most 4096 commands and 16384 items per
command are admitted, within the original SQL size bound.

Root7 also owns mandatory `content_stream_retirements` and `stream_retirement_ids`. An explicit
local retirement freezes the exact complete original stream record and its separate original
retirement budget. Only an actual retained worker-close or independent original process-end
observation permits its pin release, atomically with the immutable retirement row. A live worker
refuses. The stream's original receipt/Unknown, target and I/O budget never change; independent
Log/View/Snapshot/reader pins remain. Duplicate retirement observes only the first row. Older
Roots never interpret this release authority. Recovery retains and independently validates the
original close/end and exact release digest, including an unfinished stream with no object bytes.

Tombstoned keys refuse new reader/producer/stream/copy/reference acquisition and current availability.
Every current reference must be inactive before tombstone Commit; still-live scopes retain their
independent pins and block it. Each actual unlink/hash/inode/parent-fsync is checked within the first
budget before its separate durable per-item receipt. A failed or interrupted effect stays Unknown.
Same-ID retries are readonly, including expired/restarted operations. A new GC may inspect remaining
bytes behind the original tombstone; actual absence is reported separately and never becomes proof
of a previous unlink. Symlink, permission and I/O errors are not absence.

GC only removes bytes with no active local producer/reader/stream/Snapshot receive reference. SQL
content objects and immutable operation history remain, so a missing byte is unavailable rather
than a new producer result. Recovery v0.21 includes all Root7 journal/head/tombstone evidence, requires
bytes for every still-active reference and refuses active/tombstone conflicts. Historical receipts
retain original file provenance. Explicit Root7 backing schema v0.3 preserves raw old v0.1/v0.2
history without a generation regression. Explicit runnable process v0.5 opens Root7; older processes
retain their original boundary. Restore starts no services and renews no budgets. The journal is
software storage evidence and creates no Node, device, hardware or physical-continuity authority.

Actual native CLI, interrupted original stream/GC, SQL outcome failure, three drained upgrades and
complete Root7 cold recovery passed. See the [acceptance record](../development/acceptance/2026-10-24-replica-content-gc.md).
The first cold-runtime failure and ADR-0191 fix remain recorded against the same pinned bundle.
