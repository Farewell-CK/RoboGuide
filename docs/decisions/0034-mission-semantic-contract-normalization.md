# ADR-0034: Mission Semantic Contract Normalization

- Status: Accepted
- Date: 2026-09-11

## Context

MissionPlan v0.6 correctly separates semantic admission from deployment feasibility, provides a
Canonical Capability Catalog, records bounded Review/Repair evidence, and distinguishes local
execution completion from Task satisfaction. Its Role contract still carries historical bootstrap
coupling:

- one coarse `CapabilityKind` plus one exact contract;
- the same contract repeated as Role requirement and execution invocation;
- `actor` repeated beside a `context_role` that already identifies the Actor;
- an `ExecutionIntent` shaped as an operation-like contract plus scalar parameters;
- Mission-authored duration estimates mixed with user timing constraints;
- unstructured dialogue and contract-name-only approval policy.

Those shapes make heterogeneous capability matching, semantic Local EAIOS invocation, auditable
dialogue, and physical-goal verification harder to evolve without moving authority to the wrong
plane.

## Decision

RoboGuide evolves these contracts in independent, versioned slices under one ownership model.

### Capability, Operation, and Role Requirement

`CapabilityContractRef` identifies one extensible provider-independent capability. A TaskRole may
require multiple exact capabilities. A requirement may add typed predicates over attributes defined
by the Canonical Capability Catalog and reported in a Node capability profile. Control Matching must
require every capability and predicate; it remains the sole current-deployment `Who can` authority.

`OperationRef` is a distinct canonical identity for what a selected Local EAIOS is asked to execute.
The Catalog defines operations, parameter schemas, and their baseline capability requirements.
Catalog validation proves semantic vocabulary; it never proves that a live provider exists.

`ExecutionIntent` contains an explicit semantic objective, one `OperationRef`, and transport-neutral
typed parameters. The first profile retains scalar parameter values while the objective carries the
semantic task; richer entity and constraint structures require a later Node Protocol version. It does
not contain a vendor Skill, shell command, ROS action, route, trajectory,
or other Local How. Matching and Scheduler do not interpret the objective or parameters. Runtime
routes the immutable intent. The deployment-owned Local Integration Engine maps the operation and
intent to Local How.

Legacy Node contracts may normalize one combined declaration as both an operation and its single
provided capability during migration. Node Contract v0.5 now transports the independent objective,
canonical operation, and scalar parameters as one `ExecutionIntent`, while capability profiles carry
typed matching evidence separately. Peers must not infer objective or operation from `RoleId`.
Node Contract v0.4 remains an explicit compatibility representation and cannot silently discard a
meaningful objective. The versioned boundary is specified by ADR-0035.

### Actor and Role Identity

Mission Actor is Mission-scoped logical continuity. ContextRole maps one Actor into one collaboration
context. TaskRole references exactly one ContextRole and no longer repeats Actor identity. Control
derives Actor continuity through the accepted ContextRole reference before Matching and Binding.

### Dialogue and Deliberation

User dialogue is an ordered sequence of versioned turns with stable identity, speaker, semantic kind,
content, process-local receive time, and optional reply identity. Internal Draft/Review/Repair evidence
remains a separate revision-bound deliberation trace. Reviewer issues never masquerade as user turns.

### Time Evidence

MissionPlan owns user/policy constraints: earliest start, latest start, and completion deadline. A
duration estimate is separate task-scoped planning evidence with source identity, receive time, and
freshness. Planner omits it and must not invent a number merely to satisfy schema. Scheduler may
consume admitted estimates; candidate/profile-specific estimates remain a future refinement, and no
estimate ever proves completion.

### Task Satisfaction

Runtime execution completion and Task satisfaction remain separate. `execution-report` is the
compatibility basis. A State/Verifier basis must identify its predicate/evidence contract, source,
freshness policy, and positive or negative verdict. Orchestration alone applies the Mission-declared
basis to advance the DAG; State stores evidence and does not declare global truth.

### Approval

Approval policy evaluates a versioned risk input containing Operation, semantic parameters/objective,
grounded context, and policy rules. Contract membership may be one signal but is not the complete risk
decision. Approval remains revision/digest-bound and cannot select Nodes or commit resources.

## Consequences

- MissionPlan and Mission Request evolve through compatibility readers; existing v0.2-v0.6 plans do
  not silently acquire stronger semantics.
- Capability Matching can become heterogeneous without adding every capability to a global Rust enum.
- Proposal -> Commit -> Bind, Execution Group, Runtime lifecycle, and Local EAIOS Local How remain
  unchanged.
- Catalog, Node evidence, scheduling estimates, Task satisfaction evidence, dialogue, and review trace
  remain different authorities even when one application composes them in-process.
- Benchmark-specific object types, EMOS/Habitat actions, vendor APIs, and transport schemas do not enter
  the Mission semantic model.
- The implemented v0.7 profile retains exclusive `ResourceKind` demands for compatibility. Task
  timing expresses temporal constraints; a deployment-declared `time` resource is an exclusive token,
  not elapsed time itself. Spatial geometry and divisible capacity require later evidence-backed models.
