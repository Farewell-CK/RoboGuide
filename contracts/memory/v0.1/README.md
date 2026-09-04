# Selective Memory Catalog Contract v0.1

Generic manifests use schema `roboguide.memory-manifest/v0.1` and classify immutable revisions as
Execution, Spatial, Semantic, Experience, or Artifact Memory. Ownership remains with a named local
system or RoboGuide component. Scope is Local, Execution Group, or Global; visibility is either
metadata-only Discoverable or content Exchangeable.

Scope, Visibility, and Placement are independent dimensions:

| Dimension | Meaning | v0.1 representation |
| --- | --- | --- |
| Scope | Which logical consumers may use the Memory semantics | `manifest.scope` |
| Visibility | Whether metadata is discoverable and whether content exchange may be offered | `manifest.visibility` |
| Placement | What shared or provider-local byte availability is evidenced | `manifest.artifact` for shared CAS; `replicas[].node_id`, `consumer_provider_id`, and `status` for local placement attempts |

Visibility never broadens Scope. `Local + Discoverable` is valid: other Nodes may learn that the
revision exists, but they cannot consume or import its content. `Local + Exchangeable` permits
provider-to-provider movement only within the owner Node. `ExecutionGroup + Exchangeable` and
`Global + Exchangeable` may cross Nodes when consumer admission also satisfies the logical scope.
The manifest owner is semantic authority, not a claim that it is the only byte placement.

Node Config v0.6 provider scope is a static Local/Global upper bound, not a concrete Memory scope.
An Execution Group manifest takes its logical group identity from the live execution context and
is checked against the current Node invocation. This is local validation, not complete distributed
Group authorization or handoff. Provider discovery supports exact selector, kind, scope, provider,
payload schema, and owner filters without requiring a central or vector index.
`Local` Memory may move between providers on its owner Node but cannot be imported by another Node;
cross-node exchange requires an Execution Group or Global scope.

Exchangeable manifests must reference immutable SHA-256 content and exact byte size already
verified by the Artifact CAS. Discoverable manifests may omit bytes. The catalog stores manifests,
provenance, and provider-qualified node-local staged/imported/rejected evidence; it does not copy
every local store, select an active Memory, or make Task lifecycle decisions.
An accepted `artifact` reference records verified shared CAS content identity and availability, not
a provider-local placement or the CAS backend's physical topology. Provider-local `Staged` or
`Imported` placement appears only after accepted replica evidence; `Rejected` is negative evidence.
Filesystem paths and backend-native locations remain private to the responsible provider.

The Artifact HTTP data plane provides:

- `GET /v1/memories`
- `GET /v1/memories/{memory-id}/revisions/{revision-id}`
- `POST /v1/memories/{memory-id}/revisions/{revision-id}`
- `POST /v1/memories/{memory-id}/revisions/{revision-id}/replicas`

Memory detail responses expose each replica's `node_id`, `consumer_provider_id`, status, evidence
time, and optional rejection reason.

The Controller exposes configured provider discovery at `GET /v1/memory/providers`.
Node-owned manifest publication is admitted only when owner, provider ID, kind, scope,
visibility, payload schema, and media type remain within the Node's current complete registration
snapshot. Replica requests additionally name `consumer_provider_id`; Controller validates that
exact provider on the current replica Node against the immutable manifest kind, scope, schema,
media type, and exchangeable visibility. Replica admission does not depend on the producer still
being online after publication. Generic Memory mutations also carry paired
`X-RoboGuide-Node-Id` and `X-RoboGuide-Session-Id` headers. This framework-level provider/session
fence does not replace deployment authentication or transport security. Imported evidence is
monotonic: a later rejected attempt cannot erase a previously successful import. Durable replica
identity is `(MemorySelector, NodeId, ConsumerProviderId)`, so multiple compatible providers on one
Node retain independent lifecycle and idempotency. Pre-v7 event rows that did not carry the provider
dimension replay under the reserved `~legacy-v6-unknown` identity; RoboGuide never guesses their
historical provider.
Node-side export and import workflows are idempotent for the same immutable selector. An ambiguous
Local EAIOS response remains retryable rather than being promoted to imported or rejected evidence;
Artifact digest and size verification always precedes selective import.
The workflow-connected EAIOS remains the semantic and backend-storage authority. The RoboGuide Node
filesystem ledger stores immutable manifests for idempotency and fallback discovery; it retains
payload bytes only when serving as the workflow-free reference backend.

The `discover` workflow has a deliberately narrow contract: its manifest array is the exact set of
immutable Memory that the provider has authorized RoboGuide to publish. It must not enumerate all
Memory known to the Local EAIOS. Node Service supplies the query/context, validates the returned
manifests, and executes export/upload/publication mechanically; it does not promote, rank, or select
additional Memory for sharing. A provider that wants a Memory published must include it in this
authorized response set.

The v0.1 Node engine exposes the explicit selective exchange operation used by composition and
data-plane integration tests. A Controller-to-Node command that durably selects a consumer,
provider, and exact revision is not part of Node Protocol v0.3 yet; Node Service therefore does not
autonomously pull catalog entries or interpret discovery as permission to replicate them.

Typed `roboguide.spatial-memory/v0.1` maps continue to publish through `/v1/maps`; the generic list
and detail endpoints adapt them read-only without duplicating map catalog authority. Typed maps and
generic Memory share one selector namespace, so one `{memory-id, revision-id}` cannot name both.
