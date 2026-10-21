# ADR-0145: Bind active Memory references to recovery CAS bytes

Status: Accepted, 2026-10-21.

## Context

The quiescent recovery tool copied Memory SQLite authorities and their local CAS tree, but it
validated those files independently. A bundle could therefore retain an active
`content_references` or Active Map row while omitting the referenced CAS object. Restoring that
bundle would preserve a retention blocker whose bytes were unavailable, which is fail-closed for
deletion but incomplete for recovery and content availability.

## Decision

During backup and verification, every active Memory `content_references` row must resolve to an
exact `stores/<memory>/content/<sha256>` file declared in the same immutable bundle with the same
digest. An active reference that points at a durable tombstone is rejected. Active Map `refs`,
active `heads` and prepared handoff source/target are checked against the same provider CAS set.
Memory v3 cannot omit its mandatory reference authority tables. Released historical references do not
require their old bytes, because they do not authorize current retention or use.

Immutable bundle verification repeats the original profile's composite Memory writer marker/version
check. A repinned file or manifest cannot downgrade that profile's required v3 storage contract.
No input, receipt or storage schema version changes; original rows and budgets remain untouched.

The check runs after each SQLite/CAS file has been independently hashed and before a recovery
manifest is published. Original operation time and metadata bounds apply to the additional scans.
Restore therefore cannot publish an authority snapshot whose stored active local Memory references
lack their original bytes. The check is local to one declared provider and does
not create remote replica, Archive, Catalog or device evidence.

## Validation boundary

The recovery-tool tests use actual provider SQLite, Active Map SQLite and CAS files. Explicit
corruption cases cover missing bytes, tombstone conflicts, absent authority tables, Active Map
retention and re-pinned incomplete manifests. A real provider/CLI/restart gate checks the same
reference over private Unix HTTP after restore, then rejects independently damaged copies. No
Node, robot, model, simulator or physical outcome is created. Unconnected reference producers,
cross-host content replication and global deletion remain F4/F5 work.
