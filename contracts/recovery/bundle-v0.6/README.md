# Recovery profile v0.6

This explicit profile retains v0.5's complete original writer/inode, Memory, content and SQL
contracts, and requires Replica storage v2. The original member-local transfer history is
included in the full SQL backup and remains separate from Raft application snapshots.
It never repeats an election, starts a service or renews an original effect budget.

The v2 storage marker, physical tables, read-only old-writer views and absence of writer
triggers are checked on backup, immutable bundle verification and restore. Missing transfer
history refuses recovery. v0.5 retains its exact v1 requirement; older binaries refuse v2.
Storage upgrade first drains actual SQLite connections and commits the new marker to the
main database, preventing an old writer through an alias from overlooking a WAL-only fence.

See [ADR-0136](../../../docs/decisions/0136-member-local-leader-transfer-evidence.md).
Recovery of declared data alone grants no device or physical continuity evidence.
