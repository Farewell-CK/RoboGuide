# ADR-0203: Complete typed-map authority in runtime revision

- Status: Accepted
- Date: 2026-10-25

Runtime revision v0.3 accepts only generic Group-owned Memory. A published typed map has a Global
discovery projection and complete Spatial provenance outside that projection. Treating the projection
as a generic Group source would erase the frame/anchor/lineage permission check. Rejecting every map
instead leaves the existing map consumer unable to revise its unfinished work.

Explicit `roboguide.runtime-plan-revision/v0.4` adds only `authorize-memory` with a mandatory complete
original `typed_map`. Earlier action versions retain their exact meaning. The source must equal the
actual published Catalog map and an original preparation input in the source Group. Control checks
the original source Group/membership/Mission tenant, the full typed source policy and the new scoped
child's explicit target Read/Import policy. The prospective target Group remains absent. Its declaration
grants no reservation, import, current use or availability.

A Global map may have been produced by another Mission. Its original production Mission, Task,
attempt, owner/provider, bytes, format, frame, anchor and complete lineage remain immutable. The
current revision authority belongs to the actual consuming Mission and its original preparation;
these two provenances are never rewritten to agree. Only selector, narrowing scope and child creation
time can change. The scoped child is the existing generic Spatial Memory projection; it does not
invent a new map revision, frame, localization result or Active Map selection.

Review hashes the complete declaration. Actual original stop/revoke, current original policy/principal,
the nonrenewing source/candidate/switch budgets and original CAS verification precede publication and
normal Control creation of the target Group. Every new Task keeps its own actual preparation/import
and current-use gates. Restore validates the original command's explicit v0.3/v0.4 identity, exact
typed policy history and original preparation, preserving old v0.3 bytes and receipts unchanged.

Shared wrapper v55 fences older application/supervisor writers, including zero-event declarations;
v54 standalone migration preserves its complete original Federation and revision history. Complete
recovery retains v55 only through the existing explicit full SQL/content profile. Node contracts,
journals, event tags, original budgets and provider semantics do not change.

The maintained native path extends `check_spatial_node_processes.py --mtls --memory-consumer` with
`--typed-revision`. It builds from attributed original files, uses actual owned CPU children and
normal Node/CAS/Control APIs, then independently recomputes byte results. It creates zero devices,
models or simulators. Its observations do not prove physical localization, device stopping, global
reference retirement, cross-host recovery or overall SoftwareComplete. Full B and later work remain
subject to the software completion plan.
