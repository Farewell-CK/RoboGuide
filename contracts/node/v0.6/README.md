# RoboGuide Node Service Contract v0.6

Node configuration v0.6 and Node Protocol v0.3 add selective State exports and Memory provider
declarations plus optional heterogeneous Memory workflows to the existing declarative Local
Integration Engine. Node Protocol remains v0.3.

Each `state_exports` entry fixes its local-system owner, Node/World object identity,
Reported/Observed semantic, JSON payload schema, receive-relative validity, sample interval, and
one fixed HTTP GET observation workflow step. Mission input cannot select the endpoint, method,
object, or schema. Dynamic gRPC and MCP observations remain disabled until their contracts can
declare a mechanically enforceable read-only operation. The deployment facade is responsible for
keeping the HTTP observation side-effect free; offline conformance cannot prove external behavior.
Sampling failure does not change node health, capability
readiness, execution lifecycle, or recovery; the last accepted record instead becomes stale.

Each optional `peer_channel_observers` entry likewise fixes an owner, HTTP GET operation, sample
interval, receive-relative lifetime, and response array pointer. Every array item contains
`group_id`, `context_id`, `context_role_id`, `channel_instance_id`, `profile_id`,
`message_schema`, and `ready`; it cannot choose `local_system_id` or TTL. Node Service injects both
from startup-validated configuration and accepts at most 64 items and 64 KiB per response. The
response is evidence about endpoints that the Local EAIOS has already established, not a request to
create a transport. One observer owns the complete set for one Local EAIOS; duplicate owner
declarations or duplicate logical Role endpoints are rejected rather than resolved by arrival order. A
missing/malformed response emits no fact, so previously accepted readiness expires
and fences instead of being refreshed or converted into an invented negative.

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

Protocol v0.3 adds complete provider snapshots to Register/RegistrationUpdate, a bounded
management-sequenced `StateObservationBatch`, and identified `PeerChannelReadiness` evidence.
The latter reports only Local EAIOS channel establishment for one logical ContextRole and exact
registered `local_system_id`; RoboGuide does not carry peer traffic or implement high-frequency
coordination. Controller checks that Node, Local EAIOS, committed ContextRole, and capability owner
agree. All declared peers must name the same channel instance/profile/schema with a non-expired
1-60000 ms receive-relative lifetime before Runtime marks it Ready; negative/conflicting facts,
expiry, route loss, and restart fence it. `Registered` and `Ack` retain application plus durable
checkpoint acceptance semantics. The v0.2 gRPC endpoint is retained only to return an explicit
`FailedPrecondition` migration diagnostic.
Protocol v0.3 does not contain a channel-setup command: deployment integration establishes the
actual channel idempotently from the accepted Context/binding identities and reports evidence
through the fixed `peer_channel_observers` workflow (or the equivalent embedded engine API).
Adding reliable setup/close delivery remains a separate protocol decision.
If Node Service overruns its local readiness fact stream, it reconnects so route loss fences any
possibly missed negative evidence; it never leaves an old `Ready` state live until a best-effort retry.

Node configs v0.2 through v0.4 remain parseable and normalize to empty State/Memory/peer-observer
declarations. v0.5 remains bootable with metadata-only providers and no peer observers; v0.6 is the
current extension baseline.
Existing typed map bindings remain the strong Spatial Memory workflow and retain localization
evidence rules. Generic Spatial Memory cannot bypass the typed map schema or verification API.
