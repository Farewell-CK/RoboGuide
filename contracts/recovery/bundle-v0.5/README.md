# Recovery profile v0.5

This explicit profile adds Replica storage v1 to the complete quiescent recovery contract.
It preserves v0.4 Memory composite writer leases and all original component identities,
SQLite rows, immutable content, budgets and declared dependencies.

Replica backup requires the original supervisor, sibling writer and actual database inode
leases. The six physical tables, storage marker, five exact read-only legacy views and
absence of writer triggers are checked. Verification and restore repeat storage validation
on the immutable bundle; pinning a manifest never authorizes an incompatible SQL layout.
Older profiles refuse v1 Replica stores. The CLI never starts services or renews authority.

See [ADR-0130](../../../docs/decisions/0130-replica-database-writer-and-legacy-fence.md) and
[ADR-0135](../../../docs/decisions/0135-replica-upgrade-quiescence-and-recovery-validation.md).
Local recovery evidence does not establish online HA, device identity or physical continuity.
