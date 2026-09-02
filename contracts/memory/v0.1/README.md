# Selective Memory Catalog Contract v0.1

Generic manifests use schema `roboguide.memory-manifest/v0.1` and classify immutable revisions as
Execution, Spatial, Semantic, Experience, or Artifact Memory. Ownership remains with a named local
system or RoboGuide component. Scope is Local, Execution Group, or Global; visibility is either
metadata-only Discoverable or content Exchangeable.

Exchangeable manifests must reference immutable SHA-256 content and exact byte size already
verified by the Artifact CAS. Discoverable manifests may omit bytes. The catalog stores manifests,
provenance, and node-local staged/imported/rejected evidence; it does not copy every local store,
select an active Memory, or make Task lifecycle decisions.

The Artifact HTTP data plane provides:

- `GET /v1/memories`
- `GET /v1/memories/{memory-id}/revisions/{revision-id}`
- `POST /v1/memories/{memory-id}/revisions/{revision-id}`
- `POST /v1/memories/{memory-id}/revisions/{revision-id}/replicas`

The Controller exposes configured provider discovery at `GET /v1/memory/providers`.
Node-owned manifest publication is admitted only when owner, provider ID, kind, scope,
visibility, payload schema, and media type remain within the Node's current complete registration
snapshot. Replica evidence must name a registered Node. Authentication remains deployment policy;
these checks enforce semantic ownership even on a trusted internal data plane. Generic Memory
mutations also carry paired `X-RoboGuide-Node-Id` and `X-RoboGuide-Session-Id` headers. The
Controller accepts a manifest only from the current active session of its owner Node, and replica
evidence only from the current active session of the replica Node. This framework-level session
fence does not replace deployment authentication or transport security.

Typed `roboguide.spatial-memory/v0.1` maps continue to publish through `/v1/maps`; the generic list
and detail endpoints adapt them read-only without duplicating map catalog authority. Typed maps and
generic Memory share one selector namespace, so one `{memory-id, revision-id}` cannot name both.
