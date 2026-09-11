# ADR-0035: Node Semantic Profile and Intent Boundary

- Status: Accepted
- Date: 2026-09-11

## Context

MissionPlan v0.7 and the Core Domain already distinguish exact capability requirements with typed
attribute constraints from a semantic `ExecutionIntent` containing an objective, canonical
`OperationRef`, and parameters. Node Contract v0.4 can carry only coarse combined capability
declarations and an operation-like contract plus parameters. It therefore cannot preserve either
the feasibility envelope used by Control Matching or an objective distinct from the operation name.

Changing existing v0.4 fields in place would make negotiation lie about peer semantics. Keeping
capability declarations and executable workflow mappings combined would also let operation support
masquerade as live capability evidence.

## Decision

The established bidirectional stream, sequencing, command receipts, and execution lifecycle remain
`roboguide.node-protocol/v0.4`. Repository contract release `contracts/node/v0.8` adds an explicitly
negotiated semantic identity, `roboguide.node.v0.5`, and node configuration identity
`roboguide.node-config/v0.7`.

Node Contract v0.5 has two non-overlapping representations:

- registration carries exact capability profiles with owner, readiness, transitional coarse kind,
  and typed scalar attributes;
- invocation carries one `ExecutionIntent` with canonical `OperationRef`, nonblank semantic
  objective, and typed scalar parameters.

Node config v0.7 separates `capability_profiles` from `operations`. A profile is evidence consumed
by Shared Node State and Control Matching. An operation binds a canonical invocation to one
startup-validated Local Integration Engine workflow. Operation support does not synthesize a
capability profile, and a profile does not imply a local workflow mapping.

Node Contract v0.4 and node-config/v0.2-v0.6 remain compatibility inputs. Their original combined
fields are unchanged. Negotiated v0.4 routes accept their original invocation. A v0.5 semantic
intent may be downgraded to v0.4 only when its objective exactly equals its canonical operation
identity; otherwise routing fails because the meaningful objective cannot be preserved. Mixed v0.4
and v0.5 representations are rejected.

Protocol version, semantic Node Contract version, Node config version, and each canonical operation
version are independent identities. Compatibility is selected during Hello/Welcome negotiation,
never inferred from protobuf field presence.

Verifier evidence is not added to this Node Contract release. A later generic evidence-ingress
contract may use Nodes as one evidence producer, but Nodes and transport acceptance do not gain Task
satisfaction authority.

## Consequences

- Capability attributes now travel Node Service -> Node Protocol -> Shared Node State -> the existing
  Control eligibility predicate without changing Matching authority.
- Objective now travels Domain command -> Node Protocol -> durable Node workflow context -> the
  configured Local EAIOS invocation without Runtime interpreting it.
- Local EAIOS-specific Skill, service, route, trajectory, and safety behavior remain deployment-owned
  Local How.
- Current scalar values and transitional coarse capability kinds remain limitations. Structured
  entities/constraints, operation discovery, catalog negotiation, and generic verifier evidence are
  later versioned slices.
