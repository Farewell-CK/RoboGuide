# RoboGuide Architecture Baseline V2

> Current baseline. The authoritative source is [`RoboGuide_Architecture_Baseline_V2.docx`](RoboGuide_Architecture_Baseline_V2.docx). This Markdown document is a repository-oriented summary, not a replacement for the DOCX.

![RoboGuide V2 Overall Architecture](../../images/roboguide-v2-overall-architecture.png)

## 1. System Position

RoboGuide is a general-purpose distributed operating-system framework for heterogeneous embodied-agent collaboration. It jointly coordinates Capability, Compute, Space, and Time while preserving local autonomy on each node.

Scheduling is one subsystem. RoboGuide also owns resource abstraction, shared state, task and execution lifecycle, distributed invocation, coordination, and recovery semantics.

## 2. Logical Architecture

| Component | Responsibility |
| --- | --- |
| Mission / Application | Supplies the external Mission / Goal without controlling devices directly |
| Mission Intelligence | Produces Task Graph and Execution Requirements |
| Control Plane | Matches capabilities, proposes assignments, coordinates shared resources, commits plans, manages groups, and decides recovery |
| State & Memory Plane | Maintains evidence, shared system views, allocation state, belief, and scoped memory horizontally across the system |
| Embodied Execution Group | Carries the task-scoped distributed execution context outside the Control Plane |
| Distributed Embodied Runtime | Provides discovery, messaging, invocation, heartbeat, lease, adapters, and diagnostics |
| Local Embodied Systems | Retain perception, navigation, motion, hardware control, and immediate safety |
| Physical World | Is changed by execution and continuously feeds observations back into the system |

Logical components may be co-located or distributed. Deployment topology must not change their responsibility or authority semantics.

## 3. Core Abstractions

### Embodied Node

A discoverable system participant that may execute tasks or provide resources. Node types include Robot, Perception, Interaction, Compute, and Infrastructure Nodes. A Node is not synonymous with a Robot.

### Capability and Resource

Capability describes what a Node can currently do. Static support does not imply runtime availability. RoboGuide jointly schedules four resource classes:

- Capability: executable embodied or computational ability;
- Compute: CPU, GPU, NPU, model, and execution capacity;
- Space: location, route, region, occupancy, and shared physical facilities;
- Time: precedence, synchronization window, deadline, and occupancy interval.

### Embodied Execution Group

A dynamic, task-scoped execution context composed of Members, Roles, committed Resource Bindings, Shared Context, and Lifecycle.

Members and bindings are different. A GPU Node can be a Member while GPU quota is a Compute Binding; a corridor is a Spatial Binding, not a Member. The Group Manager belongs to the Control Plane, while the Group itself is carried by Runtime across Nodes.

## 4. Decision and Commitment Semantics

```text
Plan → Match → Propose → Coordinate → Commit → Bind → Execute
```

1. Capability Matching outputs a Candidate Set and answers `Who can?`;
2. Embodied Scheduler outputs an Assignment Proposal and answers `Who should / Where / When?`;
3. Shared Resource Coordination detects contention and performs Reservation, Negotiation, or re-allocation;
4. Commit makes resource obligations effective and observable in Allocation / Reservation State;
5. Execution Group Manager creates and binds the Group from the Committed Plan;
6. Runtime carries the bound execution across Nodes.

An uncommitted Proposal must never be treated as effective allocation.

## 5. State, Evidence, Belief, and Memory

State & Memory is horizontal infrastructure. It contains:

- Node / Resource State;
- Capability State;
- Task / Execution State;
- Spatial & World Model;
- Allocation / Reservation State;
- Shared Belief;
- Distributed Memory.

```text
Observation → Source / Provenance → Timestamp → Freshness / Uncertainty
            → Fusion / Reconciliation → Shared Belief
```

Shared Belief is a decision-oriented view, not ground truth. Conflicting or stale evidence must remain representable. Memory has Local, Execution Group, and Global scopes; Group-only context is not globally broadcast by default.

## 6. Runtime and Local Autonomy

Runtime defines transport-neutral Discovery, Messaging, Invocation, Heartbeat, Lease, Adapter, and Diagnostics semantics. Concrete DDS, ROS 2, gRPC, MQTT, database, and serialization choices remain implementation decisions.

Global coordination owns `What / Who / When / Shared Where`. Local Embodied Systems retain `Immediate How`, Navigation, Local Planning, Perception, Motion, Hardware Control, and Safety.

## 7. Reconciliation and Recovery

```text
Detect → Reconcile → Adapt
```

Recovery escalates only as far as necessary:

| Level | Owner | Response |
| --- | --- | --- |
| L0 | Local Autonomy | Avoidance, short replanning, motion retry, safe stop |
| L1 | Runtime | Reconnect or recover invocation / communication |
| L2 | Execution Group | Replace a member, re-bind, or adapt the Group |
| L3 | Scheduler / Coordination | Re-propose, coordinate, and commit |
| L4 | Mission Intelligence | Re-plan when the Task Graph no longer satisfies the Mission |

## 8. Frozen Invariants

- Proposal and Commit are distinct;
- committed bindings are observable system state;
- Group membership and bindings have explicit lifecycle;
- Local Safety cannot be overridden by remote global control;
- Shared Belief expresses uncertainty, staleness, provenance, and conflict;
- Node online state is distinct from Capability availability;
- task completion is system-level Execution State, not a single action return;
- recovery reconciles against the current world rather than replaying stale commands;
- State and Memory have scopes;
- implementation replacement must not rewrite architecture semantics.

## 9. Open Architecture Questions

V2 intentionally leaves seven questions open: State Authority, Spatial Authority, Control Topology, Execution Group Authority, Scheduling vs Runtime Coordination, Temporal Assurance, and Resource Commitment Semantics. See [`implementation-backlog.md`](../../implementation-backlog.md) for the tracked list and MVP-specific decisions.

## 10. Version Relationship

V2 supersedes V1.1 as the current source of truth. V1.1 remains under [`../v1.1/README.md`](../v1.1/README.md) for historical comparison. Architecture changes must update the baseline before diagrams, presentations, papers, or implementation documents are changed.
