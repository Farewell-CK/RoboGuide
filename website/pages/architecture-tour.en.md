# Architecture tour

Keep three views separate: logical architecture explains responsibility, code modules locate implementation, and deployment topology identifies actual processes. The [V2 architecture handbook](docs/architecture/v2/architecture-handbook.md) develops all three.

## Purpose

RoboGuide coordinates Capability, Compute, Space and Time around a long-lived Mission. Global coordination handles objectives, resources and collaboration constraints; Local EAIOS retains Local How and final safety.

## Layers and authority

| Layer | Decision authority | Read more |
| --- | --- | --- |
| MI | Interpretation, clarification, planning, Review and approval | [Mission Intelligence](modules/mission.md) |
| Orchestration | Complete-plan acceptance, DAG, satisfaction and Mission completion | [Orchestration](modules/orchestration.md) |
| Control | Eligibility, resource commitment, binding, Groups and recovery decisions | [Control](modules/control.md) |
| Runtime | Current attempts, durable intents, fact reduction and relation fences | [Runtime](modules/runtime.md) |
| Integration / Node | Formal transport, Node journal and local workflows | [Nodes and protocol](modules/integration-node-service.md) |

State, Memory, identity and persistence span these responsibilities; they are not a final serial processing stage.

## Current process deployment

```mermaid
flowchart TB
  MI[Mission Service / MI] -->|HTTP| APP
  subgraph Controller[integration-server process]
    APP[Application / writer / transaction]
    O[Orchestration] --- C[Control]
    R[Runtime] --- I[Integration]
    S[State / catalog / Artifact adapter]
    APP --- O
    APP --- R
    APP --- S
  end
  I <-->|Node Protocol| N[Node Service]
  N <--> L[Local EAIOS / Memory provider]
  VF[Independent verifier] --> APP
  HA[Explicit Replica / supervisor] -.-> APP
```

Zero Nodes is valid, and multiple Nodes may share a Host. Replica consensus, content replication, cross-domain Federation and physical continuity require separate contracts and evidence. See [implementation topology](docs/architecture/v2/implementation-topology.md).

## Invariants

- Proposals create no reservations; Commit rechecks current evidence.
- Control manages Groups; Runtime carries execution context. Releasing a Task does not destroy its Group.
- Local Completed does not automatically mean TaskSatisfied. Unknown retains ambiguity and ownership.
- State preserves source, receive time and TTL without silently merging independent sources.
- Memory Scope, Visibility and Placement are independent. A CAS reference does not establish receiver import.
- Recovery preserves original facts, budgets and permission. Local EAIOS and independent evidence constrain device safety.

## Continue reading

[Concepts](concepts.md) explains identities; [Lifecycle](lifecycle.md) follows normal and failure paths; [State and Memory](state-memory.md) covers evidence and content; [Status](status.md) records implementation limits.
