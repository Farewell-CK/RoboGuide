# ADR-0196: Preserve full Root validation across exact managed reader transactions

Status: Accepted; implemented and actual process/cold recovery acceptance passed.

The archived-source process gate completes a real Prepared takeover in roughly 141 seconds.
Every managed reader admission and closure changes the private SQLite connection, invalidating
ADR-0178's full-history witness. Revalidating all retained operations after each small exact reader
transaction repeats substantial work. Original history, operation bounds and Unknown cannot be
discarded to improve latency.

## Authority and transaction boundary

Only the actual original Root connection under its existing mutex may extend a previously complete
validation. Before a reader transaction, sample its real total_changes/data_version/schema_version/
user_version/journal_mode and remove the old ephemeral witness. A missing or different prior
witness grants no extension. No witness survives reopen, recovery, aliasing or a failed transaction.

An ordinary reader admission has exactly four known writes: its original reader, independent pin,
reader inventory and reference inventory. A reader close/process-end publication has exactly two:
the original reader result and its exact release pin. After successful actual SQL Commit, require
the exact expected total_changes delta and unchanged independent headers. Verify the complete
actual affected reader/pin and, for admission, both complete expected inventories in a consistent
readonly transaction. A second real header sample must remain exact after verification before the
new ephemeral witness can be published. Extra trigger/cascade writes, external commits, DDL,
generation/journal changes, rollback, missing rows or different content cannot borrow it.

This optimization does not cache actual bytes, file/restore identity, current authority, source
permission, kernel/process budgets, GC/tombstones or physical stopping. Existing checks and byte
hashing retain their original boundaries. All other mutations continue to invalidate full
validation. Original operation rows, receipts/Unknown and fixed scan bounds remain unchanged.
No storage, checkpoint, Node, transport or recovery generation changes.

## Owners and required acceptance

`content_store/validation.rs` owns the connection witness and exact transaction check;
`reader.rs` and the existing Snapshot-scope close adapter supply their actual original rows.
Admission intent/pin still precedes the first file open. Closing a descriptor without durable SQL
still retains Unknown and its independent pin.

Required evidence includes real complete reads/closures, retained old-history corruption after
managed transactions, exact SQL failure/rollback without invented close proof, existing external/
same-connection/DDL/version/reopen fences, actual archived-source takeover and full original cold
recovery. Report actual timing separately from long-term SLO. No Node/device/model/simulator,
physical-continuity or overall SoftwareComplete claim follows.

Actual acceptance is recorded in
[the maintained report](../development/acceptance/2026-10-25-managed-reader-validation.md).
