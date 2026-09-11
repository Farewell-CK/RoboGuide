# ADR-0036: Operation Support and Integrated Capability Baseline

- Status: Accepted
- Date: 2026-09-11

## Context

Node Contract v0.5 separated exact capability profiles from configured operation workflows and
carried semantic `ExecutionIntent` to the Local Integration Engine. Registration did not expose
which canonical operations the Node could actually receive. Control could therefore commit a Node
that satisfied every Role capability requirement but lacked the requested operation; only final
Node-local dispatch would reject it.

The Catalog also described `object.relocate@v1` as requiring grasp, navigation, and relocate
capabilities. For an integrated Local EAIOS goal, those first two entries expose likely internal
implementation steps as global provider requirements.

## Decision

Node Protocol remains `roboguide.node-protocol/v0.4`. Repository release `contracts/node/v0.9`
introduces semantic Node Contract `roboguide.node.v0.6` with additive `OperationSupport` registration
entries. Each contains only an exact canonical `OperationRef` and its `LocalSystemId` owner. It never
contains a workflow, Skill, service, command, route, parameter mapping, or other Local How.

The three layers remain independent:

- Capability Profile is current provider feasibility evidence, including readiness and attributes.
- Operation Support proves that a Node can receive one canonical semantic operation.
- Operation Binding remains private Node configuration mapping the operation and intact intent to
  Local EAIOS How.

Normalized Mission matching requires both Role capabilities and exact operation support. Candidate
Set retains the operation constraint for Proposal. Operation-aware Commit reads current Shared Node
State and revalidates support before reservation mutation. Recovery uses the same constraint in
assessment, role-scoped matching, proposal, and commit. Scheduler receives only the filtered
Candidate Set and does not interpret operations.

Node Contract v0.5 and v0.4 remain explicit compatibility contracts. v0.5 does not infer operation
support from profiles. v0.4 may treat its historical combined exact declaration as compatibility
evidence. Old wire fields remain unchanged.

Catalog v0.3 defines `required_capabilities` as the direct provider-level feasibility baseline, not
an expansion of an operation's internal workflow. `object.relocate@v1` therefore requires only
`object.relocate@v1`. Cross-node grasp, movement, handoff, or placement becomes separate Mission
Task/Role boundaries only when RoboGuide must schedule, commit, coordinate, or recover them
independently.

## Consequences

- A capability-compatible Node without the operation is excluded before Proposal and Commit.
- Removal of support between Match, Proposal, and Commit is detected at the next boundary.
- Shared Node State stores support evidence; Control owns feasibility policy and Commit authority.
- Operation support neither replaces the Catalog nor advertises Local How.
- Node Service retains final local operation lookup as defense against stale State or session change.
- Dynamic operation readiness, operation-catalog negotiation, structured parameters, and generic
  verifier evidence ingress remain future work.
