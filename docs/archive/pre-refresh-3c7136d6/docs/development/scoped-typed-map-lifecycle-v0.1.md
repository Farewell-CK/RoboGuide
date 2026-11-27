# Scoped typed-map lifecycle v0.1

Owner: Domain/Control, Controller application `scoped_maps`, Node preparation and the configured
Local Memory provider. [ADR-0208](../decisions/0208-scoped-typed-map-lifecycle.md) defines the
authority change. The whole module is implemented and passes [actual process acceptance](acceptance/2026-10-25-scoped-typed-map-lifecycle.md).

The original typed map remains the production result. A receiving Memory child changes only
its selector, exact Execution Group scope and monotonic creation time. Its owner, provider,
payload/media contracts, content digest/size and production Mission/Task/attempt remain exact.
The complete typed map retains frame, anchor and all original lineage; byte equality alone
cannot establish that relationship.

MissionPlan v0.11 introduces the optional semantic requirement
`source_contract: scoped_typed_map`. It contains no Node, provider, path or transport selection.
Control matches only actual Node Contract v0.14/provider availability v0.2 declarations. Node
config v0.15 explicitly selects that availability contract; absence keeps v0.1. Wire catalog
v0.17 preserves all previous field numbers and quantitative resource grants.

Preparation v0.3 retains the complete scoped manifest plus original `typed_map`. Each Task gets
its own import and current-use permission; another Task's Completed import remains history.
Old preparation v0.1/v0.2 retains its exact projection semantics. A generic legacy scoped input
may supply original source-context evidence for a new transfer only when its actual retained
publication independently establishes the typed root. That never upgrades the old use record.

Group handoff v0.3 freezes both original memberships and complete source/target attempt sets.
The actual source preparation must match the exact original map and current consumed parent.
Original typed Handoff permission, scoped-parent Handoff where applicable and independent child
Read/Import grants remain required. Every target Task must request the explicit new source
meaning. Complete original software stop and all provider revoke proofs precede publication;
receiver preparation/current use precedes explicit Commit and normal Execute. v0.2 keeps its
original producer-only behavior.

Runtime revision v0.5 starts from an already scoped parent, retaining its original typed root.
Declarations precede candidate output/provider claims; independent Review covers the complete
declaration. The original Mission goal, stop/revoke and normal Control creation of the new Group
remain mandatory. v0.4 retains its global typed-source contract. Applied revisions and published
handoffs form immutable provenance edges, checked for missing parents, ambiguity and cycles.

The provider independently checks immutable projections at import/use. A fresh receiving lease
can select/query the original map under that Task's Node, Group, execution and current grant.
An old head or revoked source lease cannot authorize that query. This does not create new map
geometry, localization or physical handoff. Local active-map handoff retains its separate meaning.

Controller wrapper v57 and Node journal v15 fence old writers while preserving original history
and budgets. Reference provider opt-in `--enable-scoped-typed-maps` selects Memory SQL v5, including
the complete prior v4 Snapshot authority. Migration holds the original composite inode leases,
drains legacy SQLite connections, commits the new marker atomically and keeps actual instance,
imports, permissions and content. SQL failure leaves the prior generation; default writers
cannot reopen v5. Existing worker/current-use/active-map/pending-handoff references block local
GC. Original revoke and actual worker evidence precedes tombstone, then exact unlink.

Recovery profile v0.26 retains all v0.25 contracts and admits these explicit generations. The CLI
reconstructs the original checkpoint, verifies the complete scoped ancestry and independently
matches every typed root against original hot/archived Catalog publication. Existing reference
scans still require actual currently retained bytes; released history does not require deleted
local content. Restore changes no original permission, receive clock or Unknown and starts no
service.

Maintained acceptance entries are `check_spatial_node_processes.py --mtls --memory-consumer
--typed-revision scoped-lifecycle`, `check_revision_memory_schemas.py`, and
`check_scoped_map_recovery_processes.py`. They require explicit attributed map inputs and original
real binaries/packages. Actual CPU/file/software Node/SQL/CAS/mTLS observations stay separate
from device identity, physical stopping, localization, physical continuity and long-term SLO.
