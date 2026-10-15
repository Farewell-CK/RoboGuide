# Producer-bound typed maps v0.2

`roboguide.map-manifest/v0.2` preserves all v0.1 immutable map/bytes/frame/anchor/source fields and
requires original `producer_local_system_id`, `producer_provider_id`, `payload_schema`,
`source_execution_id` and `source_task_ref`. Content schema describes opaque map bytes, distinct
from this metadata envelope. Original provider identity is exact and Node-registered; a compatibility
catalog name cannot stand in for a Local EAIOS provider.
`created_at_ms` is producer creation metadata; it does not establish Controller receive time,
cross-host clock synchronization, evidence freshness or a renewed deadline.

`POST /v1/maps/{map_id}/revisions/{revision_id}` uses the closed
`roboguide.map-publication/v0.1` envelope: `schema`, `manifest` (v0.2), `execution_id` (current publish
attempt). Current Node/session context uses the existing `X-RoboGuide-Node-Id` and
`X-RoboGuide-Session-Id` headers. Context alone grants no authority. Actual certificate, stable grant,
accepted current route, original provider and exact Control/Runtime ownership are checked inside
the catalog writer gate. A remote legacy v0.1 manifest cannot authorize a new data-plane write.

The source build attempt must be Completed, on the original Node/LocalSystem and same Mission/Group
as the distinct current publishing attempt. Both semantic artifact directives must match map/revision,
anchor and output slot. Publication must still be the exact active Role attempt on that Node and not
cancelling, held, released or terminal. A current provider declaration must cover the original
content schema/media/kind and its exact owner. Manifest bytes must already have verified CAS ownership.
Both attempts retain their actual original Node Contract/session/provider declarations. Missing legacy
dispatch evidence remains unavailable after a new Node registers; current declarations never fill
that gap. A changed provider cannot reinterpret the original source build under another identity.

Typed-map discovery/content policy projections retain real provider and content schema for v0.2.
Historical v0.1 stays unchanged and retains its explicit `typed-map-catalog` compatibility projection;
that name never establishes current provider registration or placement. Group-scoped content and
scope exchange still require their separate distributed authorization. No map manifest proves current
Node localization, device geometry truth, safe stopping or Task satisfaction.
