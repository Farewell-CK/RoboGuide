# Recovery profile v0.10

This explicit profile retains v0.9's original Artifact owner/fence/CAS, Memory SQL v4 Snapshot
producer, Replica storage v3 and all original writer/inode leases. It additionally verifies
Controller Catalog/lineage reference authority from wrapper v48 and event codec v29.

Each original publication must have its original acquisition record. Exact replay keeps its
tenant, principal, generation, time, authority and dependency set. Retirement is irreversible;
missing acquisition, changed authority, reused retirement commands or resurrection refuses.
The actual owner of every active dependency must be declared in the same bundle. Its exact CAS
path, digest and size must match independently verified bytes. Equal hashes in a different owner
do not satisfy the claim. Each child independently retains its original parent's content, even
after the parent's own Catalog record is retired. Released history needs no deleted content.

Generic metadata is bound by its complete canonical manifest digest. Typed Spatial dependencies
also preserve exact original canonical JSON and its hash, including frame, anchor, format and
producer provenance. Verification compares it with the original published Spatial metadata;
another language's floating-point reserialization cannot silently change its digest. Original
mandatory event tables/columns and publication/reference coverage are checked. Scans retain the
caller's original deadline and byte bound; no restore or retry renews a permission or budget.

`roboguide.restore-receipt/v0.10` keeps exact authority and owners. Restore starts no service and
grants no Node registration, current Memory use, physical stopping, global reference coverage or
online HA. Older profiles retain their original semantics and cannot verify actual v29 reference
authority. Legacy Catalog ownership without original records needs explicit migration; it is
never assigned a replacement tenant or publisher by the recovery tool.

See [ADR-0149](../../../docs/decisions/0149-catalog-and-lineage-content-references.md). Complete
Archive/Raft Snapshot/online reference producers and their transitive content closure remain
required separately.
