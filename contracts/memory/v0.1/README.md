# Selective Memory Catalog Contract v0.1

Generic manifests use schema `roboguide.memory-manifest/v0.1` and classify immutable revisions as
Execution, Spatial, Semantic, Experience, or Artifact Memory. Ownership remains with a named local
system or RoboGuide component. Scope is Local, Execution Group, or Global; visibility is either
metadata-only Discoverable or content Exchangeable.

Node Config v0.6 provider scope is a static Local/Global upper bound, not a concrete Memory scope.
An Execution Group manifest takes its logical group identity from the live execution context and
must match that context across restart or rebind. Provider-local discovery supports exact selector,
kind, scope, provider, payload schema, and owner filters without requiring a central or vector index.
`Local` Memory may move between providers on its owner Node but cannot be imported by another Node;
cross-node exchange requires an Execution Group or Global scope.

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
snapshot. Replica requests additionally name `consumer_provider_id`; Controller validates that
exact provider on the current replica Node against the immutable manifest kind, scope, schema,
media type, and exchangeable visibility. Replica admission does not depend on the producer still
being online after publication. Generic Memory mutations also carry paired
`X-RoboGuide-Node-Id` and `X-RoboGuide-Session-Id` headers. This framework-level provider/session
fence does not replace deployment authentication or transport security. Imported evidence is
monotonic: a later rejected attempt cannot erase a previously successful import.
Node-side export and import workflows are idempotent for the same immutable selector. An ambiguous
Local EAIOS response remains retryable rather than being promoted to imported or rejected evidence;
Artifact digest and size verification always precedes selective import.

The v0.1 Node engine exposes the explicit selective exchange operation used by composition and
data-plane integration tests. A Controller-to-Node command that durably selects a consumer,
provider, and exact revision is not part of Node Protocol v0.3 yet; Node Service therefore does not
autonomously pull catalog entries or interpret discovery as permission to replicate them.

Typed `roboguide.spatial-memory/v0.1` maps continue to publish through `/v1/maps`; the generic list
and detail endpoints adapt them read-only without duplicating map catalog authority. Typed maps and
generic Memory share one selector namespace, so one `{memory-id, revision-id}` cannot name both.
