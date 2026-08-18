# Development Baseline

> Status: Proposed for review; MVP definition pending. Drafted on 2026-08-17.
> The V2 architecture remains authoritative. This proposal translates V2
> responsibilities into engineering boundaries without freezing transports,
> databases, schemas, or algorithms.

## 1. Development Principles

1. Architecture precedes directories and frameworks. A module exists only when it
   has one responsibility, an explicit dependency direction, tests, and an owner.
2. Start as a modular monolith. Logical boundaries must be enforceable in code, but
   MVP deployment does not begin as a collection of microservices.
3. The core is simulator- and hardware-neutral. Isaac Sim, ROS 2, vendor SDKs, and
   real robots enter through adapters.
4. The first executable evidence uses deterministic fake nodes. Simulation and real
   hardware are parallel validation tracks, not prerequisites for core development.
5. Frozen V2 semantics win over implementation convenience. A boundary change needs
   an ADR and, when architecture semantics change, an updated V2 baseline.

## 2. Proposed Repository Layout

The following layout is proposed, not yet accepted and not a request to create empty
folders. After acceptance, create each path only in the change that adds its first
maintained implementation.

```text
crates/
  roboguide-domain/       Pure domain types, invariants, and state machines
  roboguide-ports/        Transport-neutral interfaces owned by the core
  roboguide-control/      Matching, proposal, coordination, commit, group manager
  roboguide-runtime/      Discovery, invocation, heartbeat, lease, diagnostics
  roboguide-state/        Evidence, shared views, allocation state, scoped memory
  roboguide-adapters/     Rust transport, persistence, ROS, and vendor adapters
  roboguide-testkit/      Fake nodes, virtual clock, fixtures, failure injection
apps/
  roboguide-controller/   Composition root and process lifecycle only
python/
  roboguide_mission/      Mission planning and model-backed reasoning adapters
  roboguide_sim/          Simulator integration adapters
scenarios/                Versioned scenario inputs and expected event traces
tests/system/             Black-box, cross-process tests only
tools/quality/            Repository-specific documentation and boundary checks
```

Do not create implementation directories while this baseline is under review. After
acceptance, any additional top-level implementation directory requires a baseline
update or accepted ADR. Never commit empty placeholder directories.

## 3. Module Boundaries

| Module | Owns | Must not own |
| --- | --- | --- |
| Domain | IDs, value objects, invariants, lifecycle state machines | I/O, SDKs, storage, scheduling infrastructure |
| Ports | Core-required interfaces such as clock, event log, node registry | Vendor or transport types |
| Control | Match, Propose, Coordinate, Commit, Group lifecycle, recovery decisions | Hardware commands or local motion |
| Runtime | Discovery, messaging semantics, invocation, heartbeat, lease | Global resource selection |
| State | Observation, provenance, freshness, uncertainty, belief and allocation views | Mission planning or device control |
| Adapters | Protocol, simulator, storage, model, ROS and vendor translation | Core policy decisions |
| Apps | Dependency wiring, configuration, startup and shutdown | Domain rules |
| Quality tools | Static repository checks not covered by standard linters | Runtime behavior or production dependencies |

Allowed Rust dependency direction:

```text
apps -> control/runtime/state/adapters -> ports -> domain
```

`domain` has no internal project dependency. Cycles are forbidden. Python is never
embedded into the Rust core during MVP; it communicates through an adapter boundary.

## 4. Contract Rules

- Proposal and Commit are different types and transitions.
- Node online state and Capability availability are different facts.
- Members, Roles, Resource Bindings, and Shared Context remain distinct.
- Observations carry source, timestamp, freshness, and uncertainty.
- Events are immutable and include event, correlation, and causation identities.
- Durations use a monotonic clock; externally exchanged timestamps use UTC.
- Adapter messages are versioned. Serialization details are not domain types.
- Control issues goals, roles, constraints, and bindings; Local Systems retain
  Immediate How and final safety authority.

## 5. First Vertical-Slice Gate

The scenario and node topology are selected in [`../mvp-definition.md`](../mvp-definition.md),
which is currently a Draft. Regardless of the final scenario, the first slice must:

1. Register nodes, capabilities, health, and resources.
2. consume an approved Task Graph and Execution Requirements fixture;
3. produce Candidate Set, Assignment Proposal, Commit, and Execution Group;
4. execute through Runtime and record an ordered event trace;
5. inject at least one approved failure at a physically valid boundary;
6. preserve completed work and escalate only to the necessary recovery level;
7. report an unrecoverable physical state as blocked/escalated, never as success.

## 6. Change Gate

Once this baseline is accepted, every implementation change must include:

- the V2 responsibility and module it implements;
- documented functions and public types;
- deterministic tests for normal, rejected, timeout, and recovery paths;
- structured evidence such as an event trace for cross-module behavior;
- an ADR when dependency direction, authority, lifecycle, or public contracts change;
- synchronized commands and layout documentation.

Detailed code requirements are in [`coding-standards.md`](coding-standards.md).
Language ownership is recorded in
[`../decisions/0001-rust-core-python-edges.md`](../decisions/0001-rust-core-python-edges.md).
