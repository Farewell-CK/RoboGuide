# RoboGuide Node Service Contract v0.5

Node configuration v0.5 and Node Protocol v0.3 add selective State exports and Memory provider
declarations to the existing declarative Local Integration Engine.

Each `state_exports` entry fixes its local-system owner, Node/World object identity,
Reported/Observed semantic, JSON payload schema, receive-relative validity, sample interval, and
one fixed HTTP, dynamic gRPC, or MCP observation workflow step. Mission input cannot select the
endpoint, method, service, tool, object, or schema. The deployment facade is responsible for
keeping observation side-effect free; offline conformance cannot prove that external behavior.
Sampling failure does not change node health, capability
readiness, execution lifecycle, or recovery; the last accepted record instead becomes stale.

Each `memory_providers` entry declares local ownership, one of the Execution, Spatial, Semantic,
Experience, or Artifact kinds, default local/global scope, discoverable/exchangeable visibility,
payload schema, and media type. This is a discovery contract, not a requirement that local EAIOS
implementations share a database. Exchangeable content uses the existing digest-verified Artifact
data plane; discoverable records may be metadata-only.

Protocol v0.3 adds complete provider snapshots to Register/RegistrationUpdate and adds a bounded,
management-sequenced `StateObservationBatch`. `Registered` and `Ack` retain application plus durable
checkpoint acceptance semantics. The v0.2 gRPC endpoint is retained only to return an explicit
`FailedPrecondition` migration diagnostic.

Node configs v0.2 through v0.4 remain parseable and normalize to empty State/Memory declarations,
but v0.5 is the current extension baseline. Existing typed map bindings remain the first strong
Spatial Memory workflow and retain localization evidence rules.
