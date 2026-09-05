# ADR-0026: Execution Coupling Modes and Group Views

> ADR-0027 makes `SharedSpatialReference` executable, adds identified two-ended peer readiness,
> and introduces the Controller implementation-support gate. This ADR still defines the v0.4
> vocabulary and authority split.

## Status

Accepted

## Context

Execution Coordination Relations v0.1 expresses logical `(TaskId, RoleId)` endpoints and Runtime
lifecycle fencing, but it does not state how a Context expects its running members to coordinate.
Cross-device execution needs a small vocabulary for independent work, handoff, concurrent
cooperation, and tightly coupled cooperation without putting control algorithms in RoboGuide.

## Decision

MissionPlan v0.4 adds `ExecutionCouplingMode` to `CoordinationContext`, with an optional Task
override. The modes are `Independent`, `SequentialHandoff`, `ConcurrentCooperation`, and
`TightlyCoupledCooperation`. A mode only declares required mechanisms. The Task DAG retains
phase/readiness semantics; relations retain runtime cross-Role coupling semantics.

Contexts may declare a selective Group shared view. Pose/velocity use explicit
`(ContextRoleId, GroupViewField, StateExportId, payload schema)` bindings; execution status is
resolved from Runtime by logical Task/Role and never masquerades as Node State. An optional typed
`MapRevisionSelector` plus common frame interprets spatial evidence. Freshness is derived from each selected State record's
existing receive-time/TTL semantics and returned as `Fresh`, `Stale`, or `Unknown`; field meaning is
never guessed from a channel name. State remains the evidence authority and Runtime/Orchestration
exposes a read-only Group view assembled from currently bound members. Runtime may retain a
transport-neutral peer channel descriptor and lifecycle (`Planned`, `Ready`, `Fenced`, `Closed`)
for tightly coupled contexts. ADR-0027 adds a readiness-evidence message without prescribing peer
middleware; Local EAIOS owns high-frequency relative-state computation and corrective control.
The Runtime-backed execution field has no TTL freshness classification; its `Unknown` execution
status already carries physical ambiguity and uses the existing reconciliation boundary.

Typed relation families reserve state keys, shared spatial references, frame identifiers, state
requirements, and provider-defined freshness policy identities. They deliberately contain no
distance/angle thresholds, formulas, or expression DSL. ADR-0027 implements shared spatial
evidence and rejects the other reserved families through implementation preflight.

`SequentialHandoff` requires the existing Task handoff mechanism: DAG readiness plus application
Task lifecycle evidence. It does not create a second Runtime handoff registry in v0.4. Concurrent
modes require at least one declared relation and a selective shared view; tightly coupled mode also
requires a peer channel confirmed `Ready` by deployment/local integration.
Mission acceptance validates these static declarations against both the Context default and every
Task override; Runtime readiness owns only dynamic mechanism lifecycle and conservative fencing.

Typed coupling/relation event evidence uses `domain.EventPayload.json/v8`. Legacy
`RequiresActive + Independent` evidence remains replayable under v5, while typed relation or
non-independent coupling payloads cannot be relabeled as pre-v8 rows.
The inner Controller checkpoint advances to v10 and the server wrapper to v11; each accepts only
its immediately previous marker for explicit one-step migration.

## Consequences

The accepted plan is the specification authority, Control keeps commitment/recovery authority,
Runtime keeps live relation and channel lifecycle authority, and State keeps observed evidence.
Logical endpoints survive Node replacement and rebind. v0.2/v0.3 plans remain decodable and are
normalized to canonical v0.4 output. Group-scoped distributed authorization and selective-import
commands remain outside this ADR and require a separate Control/Node Protocol design.
