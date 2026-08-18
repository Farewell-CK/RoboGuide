# ADR-0001: Rust Core and Python Edge Responsibilities

- Status: Proposed
- Date: 2026-08-17
- Scope: Implementation boundary; no change to V2 architecture semantics

## Context

RoboGuide needs deterministic lifecycle and recovery behavior while also integrating
rapidly changing planners, models, simulators, and research code. A single-language
choice would either slow experimentation or weaken the core's correctness boundary.
Beginning with distributed microservices would add transport and deployment choices
before the contracts are validated.

## Proposed Decision

Rust owns the long-lived system core:

- domain types, invariants, and lifecycle state machines;
- Capability Matching, Proposal, Coordination, Commit, and Group management;
- Runtime semantics, heartbeat, lease, invocation, diagnostics, and recovery;
- evidence, shared state views, allocation state, and event persistence ports.

Python owns change-heavy edge capabilities:

- Mission Understanding, task-planning, LLM/VLM, and experimental policies;
- Isaac Sim and other simulator adapters;
- dataset, evaluation, and research tooling.

If accepted, the MVP starts as a Rust modular monolith with in-memory port
implementations and deterministic fake nodes. Python first produces fixtures and
adapter outputs against versioned contracts. A live process boundary is introduced
only after those contracts have integration evidence.

Rust must not embed a Python interpreter during MVP. Python must not reimplement
Commit, Lease, Execution Group, recovery authority, or final local safety semantics.
No transport, serialization framework, service topology, database, or RPC technology
is selected by this ADR.

## Expected Consequences

- Compile-time Rust crate boundaries can enforce core dependency direction.
- Core tests run without Python, a simulator, the network, or hardware.
- Python remains fast to iterate but is replaceable behind an adapter contract.
- Cross-language contracts need versioning, compatibility tests, and correlation IDs.
- Some types are translated at adapter boundaries; SDK objects cannot leak inward.

## Acceptance and Revisit Triggers

The project owner changes this ADR to `Accepted` only after reviewing language
ownership and bootstrap costs. After acceptance, revisit it only with measured
evidence that the boundary prevents required latency, deployment, safety, or
developer-productivity goals. Changing language ownership, embedding Python, or
moving core authority into an adapter requires a new ADR and architecture-impact
review.
