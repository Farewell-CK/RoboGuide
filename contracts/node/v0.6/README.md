# RoboGuide Node Service Contract v0.6

Node configuration v0.6 and Node Protocol v0.3 add selective State exports and Memory provider
declarations plus optional heterogeneous Memory workflows to the existing declarative Local
Integration Engine. Node Protocol remains v0.3.

Each `state_exports` entry fixes its local-system owner, Node/World object identity,
Reported/Observed semantic, JSON payload schema, receive-relative validity, sample interval, and
one fixed HTTP, dynamic gRPC, or MCP observation workflow step. Mission input cannot select the
endpoint, method, service, tool, object, or schema. The deployment facade is responsible for
keeping observation side-effect free; offline conformance cannot prove that external behavior.
Sampling failure does not change node health, capability
readiness, execution lifecycle, or recovery; the last accepted record instead becomes stale.

Each `memory_providers` entry declares local ownership, one of the Execution, Spatial, Semantic,
Experience, or Artifact kinds, maximum local/global scope, discoverable/exchangeable visibility,
payload schema, and media type. Optional `discover`, `export`, and `import` steps use the same
startup-fixed HTTP, dynamic gRPC, or MCP routes as capability workflows. Those workflows form the
Local Memory Provider integration boundary; the real EAIOS retains semantic and backend-storage
authority and may use any internal representation. The `discover` workflow response is the exact
provider-authorized publish-eligible manifest set, not all Memory known to the Local EAIOS.

Every operational v0.6 declaration also has a RoboGuide Node-side filesystem manifest ledger for
idempotency and deterministic fallback discovery. It stores immutable manifest objects and rebuilds
a JSONL metadata index from them. When a real import workflow is configured, a successful import
records only the manifest in this ledger and does not create a second payload copy. If an operation
has no workflow, the same component acts as the reference backend fallback and may retain bytes.
The configured `storage_directory` names this Node ledger, controlled export handoff, and reference
fallback, never the EAIOS store.
Exchangeable content uses the existing digest-verified Artifact data plane;
discoverable records may be metadata-only.
`discover` alone returns a manifest array pointer, `export` alone may return a handoff-relative
artifact path pointer, and `import` has no result pointer; incompatible combinations fail startup
validation.

Provider scope is a static upper bound. A concrete `ExecutionGroup` scope is taken only from a live
execution operation and must match its `group_id`; it is never node configuration. Discovery is
provider-local and deterministic. Node Service only validates and mechanically publishes the set
returned by that provider; it does not promote or select additional Memory. Publishing is proactive,
while import is selective and records
staged/imported/rejected catalog evidence without changing Runtime or Task lifecycle.
An export workflow returns only a traversal-free path relative to its configured provider storage
root; it cannot select an arbitrary host file.
Provider workflows must be idempotent for repeated operations on the same immutable selector.
In particular, an ambiguous Local EAIOS import response remains staged and may be retried; the
workflow must not create a second semantic Memory revision or duplicate non-idempotent effects.
The public selective-import path always verifies Artifact digest and size before invoking the
provider workflow. A retry first checks the provider-owned immutable selector, so a completed local
import is not repeated merely to reconstruct transfer state. Each replica mutation names the exact
local consumer provider; durable identity and idempotency are scoped by revision, Node, and provider,
and accepted evidence cannot regress from Imported to Rejected.

For v0.1 wire compatibility, conformance still reports the Node ledger/reference-fallback capability
as `local_backend`; it does not assert Local EAIOS authority. The separate workflow flags report
configured EAIOS operation routes, and `shared_data_plane` reports Artifact/catalog availability.
Workflow declarations without a shared endpoint remain inspectable local metadata, not a claim that
publication or exchange can run.
Consumer selection and a durable Controller-to-Node import command remain outside Protocol v0.3;
the node never turns discovery into implicit full replication.

Protocol v0.3 adds complete provider snapshots to Register/RegistrationUpdate and adds a bounded,
management-sequenced `StateObservationBatch`. `Registered` and `Ack` retain application plus durable
checkpoint acceptance semantics. The v0.2 gRPC endpoint is retained only to return an explicit
`FailedPrecondition` migration diagnostic.

Node configs v0.2 through v0.4 remain parseable and normalize to empty State/Memory declarations.
v0.5 remains bootable with metadata-only providers; v0.6 is the current extension baseline.
Existing typed map bindings remain the strong Spatial Memory workflow and retain localization
evidence rules. Generic Spatial Memory cannot bypass the typed map schema or verification API.
