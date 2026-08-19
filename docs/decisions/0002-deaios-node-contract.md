# ADR-0002: DEAIOS Node Contract v0

- Status: Proposed for MVP Slice v0.1
- Date: 2026-08-19
- Scope: Semantic boundary between DEAIOS and a local EAIOS or vendor runtime

## Context

DEAIOS coordinates heterogeneous nodes globally, while each node may run a
different EAIOS, vendor SDK, local planner, or safety controller. The current
Rust bootstrap has an in-process `NodeGateway`, but it must not become an
assumption that all nodes share one implementation. The boundary needs to be
stable enough for fake nodes and adapters while leaving transport and deployment
choices open.

## Decision

The Node Contract is a semantic contract, not a wire protocol. It has five
responsibility groups:

1. **Registration**: `NodeId`, local runtime identity/version, capabilities,
   resources, and the latest health snapshot.
2. **Scheduling evidence**: capability availability, resource ownership,
   freshness, and the conditions under which a node may be considered.
3. **Execution command**: mission, task, execution group, role, target node,
   resource bindings, and correlation identity. DEAIOS sends goals, roles,
   constraints, and bindings; it does not send raw actuator trajectories.
4. **Observation**: completion, failure, safe stop, health changes, timestamps,
   source identity, and correlation/causation information.
5. **Lifecycle**: registration/refresh, lease and heartbeat semantics, command
   acceptance, execution observation, release, and recovery escalation.

The global/local authority split is explicit. DEAIOS owns matching, proposal,
coordination, commit, group membership, rebinding, and escalation. The local
EAIOS owns immediate how, local planning, hardware control, and final safety.
An adapter translates a concrete EAIOS or vendor API into this contract and keeps
SDK types outside the Rust core.

## MVP Slice Scope

Slice v0.1 requires only typed in-process ports and deterministic fake nodes. It
must prove registration, matching, Proposal versus Commit, group binding,
execution observation, recoverable node failure, role rebinding, and blocked
escalation when no replacement exists. Lease/heartbeat behavior is part of the
contract semantics and must be covered before a live multi-process adapter is
accepted.

This ADR does not select gRPC, ROS 2, NATS, a serialization format, a database,
service topology, simulator, or hardware API. Those choices require evidence from
the contract tests and a separate decision when they affect ownership or public
interfaces.

## Consequences

- Different EAIOS implementations can join through separate adapters.
- Core tests remain offline and independent of simulators and hardware.
- Contract objects need explicit versioning and compatibility tests at adapter
  boundaries.
- A successful local command is not the same as a global Commit or task success.

## Acceptance Evidence

Acceptance requires an ordered event trace for the normal path, rejection,
resource conflict, timeout/lease failure, replacement recovery, and unrecoverable
blocked path. The owner changes this ADR to `Accepted` only after reviewing those
traces and the corresponding MVP Slice evidence.
