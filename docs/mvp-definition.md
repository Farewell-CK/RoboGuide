# MVP Definition

> Status: Draft - full MVP not frozen
> Last updated: 2026-08-19
> Authority: This document becomes binding only after explicit project-owner review
> and a status change to `Frozen`.

## 1. Purpose

This document turns the general MVP direction into a testable implementation scope.
It does not replace the V2 architecture. Until it is frozen, candidate scenarios,
node combinations, failure cases, and metrics must not be treated as requirements.

## 2. Current Decision State

### Direction Agreed

- The MVP validates a general heterogeneous multi-node task, not blind guidance.
- At least two nodes with different capabilities or responsibilities participate in
  one Mission.
- The system demonstrates Plan, Match, Propose, Coordinate, Commit, Bind, Execute,
  Observe, Reconcile, and Adapt.
- Proposal and Commit remain distinct; Execution Group membership and resource
  bindings remain distinct.
- Local Systems retain Immediate How and final safety authority.

### Approved Implementation Slice v0.1

The full MVP remains Draft, but the following narrow slice is approved as the
first implementation and team handoff baseline:

- Node A provides Transport and Compute;
- Node B provides replacement Transport;
- Edge provides shared Compute;
- one task requires Transport and Compute roles;
- Node A fails after execution has started;
- the system preserves completed observations and the Execution Group context;
- Control rebinds only the failed role to Node B and reuses the Edge binding;
- the group completes, or becomes Blocked/Escalated when no safe replacement exists.

This slice is intentionally simulator- and hardware-neutral. It does not imply
that a physical payload can be handed between arbitrary robots, and it does not
make a Drone or Arm a core MVP prerequisite.

### Engineering Direction Under Review

- Rust core with Python mission, model, and simulator edges.
- A modular monolith before any microservice split.
- Deterministic fake nodes before simulator and real-hardware validation.
- Simulator and hardware integrations behind adapters.

### Decisions Still Open

| Decision | Required output |
| --- | --- |
| Mission | One sentence describing the user-visible objective |
| Node topology | Named logical nodes, roles, capabilities, and resources |
| Physical prerequisites | Map, objects, handoff points, access and safety constraints |
| Normal flow | Ordered task steps and expected state transitions |
| Failure matrix | Injection point, detection evidence, recovery owner and expected result |
| Observable evidence | Required events and state for each lifecycle stage |
| Metrics | Quantitative success, latency, conflict and recovery thresholds |
| Non-goals | Explicitly unsupported behavior |
| Validation ladder | Fake-node, simulator and hardware responsibilities |
| Exit criteria | Evidence required to declare the MVP complete |

## 3. Deferred Scenario Candidates

The earlier scenario remains a deferred candidate, not an MVP requirement:

- a Drone provides wide-area search;
- an Arm manipulates or loads a target object;
- Dog A provides transport and local compute;
- Dog B provides backup transport;
- Edge/Cloud provides fallback compute.

This candidate still has unresolved physical and task-design questions:

- whether search is necessary and what uncertainty only the Drone can resolve;
- where the Arm is located and why it cannot already observe the target;
- when a carrier failure occurs and whether the payload is accessible;
- how a physical handoff between Dog A and Dog B is performed;
- whether one scenario is too complex for the first executable slice;
- which resource contention demonstrates Compute, Space, and Time coordination.

No implementation may encode this candidate as the required MVP until these
questions are resolved and this document is frozen.

## 4. Freeze Checklist

Before changing the status to `Frozen`:

1. Approve the Mission and task boundary.
2. Approve the node/capability/resource table.
3. Approve the normal Task Graph.
4. Approve physical prerequisites and handoff semantics.
5. Approve the failure-injection and recovery matrix.
6. Approve observable evidence for Proposal, Commit, Bind, Execute, and Reconcile.
7. Approve quantitative metrics and exit criteria.
8. Confirm what Fake Nodes, simulation, and hardware each validate.
9. Record explicit non-goals and deferred capabilities.

## 5. Status Lifecycle

`Draft` means decisions are being collected. `In Review` means all checklist
artifacts exist and are under project-owner review. `Frozen` means implementation may
treat them as requirements. `Superseded` points to a newer definition. Only an
explicit project-owner decision changes this status.
