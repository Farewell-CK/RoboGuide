# ADR-0030: Mission Semantic Admission and Deployment Feasibility

- Status: Accepted for Mission Semantic Admission v0.1
- Date: 2026-09-10

## Context

ADR-0018 introduced the Mission Request grounding loop and a read-only Controller inventory.
The first implementation used that potentially stale deployment snapshot as a mandatory preflight:
if no currently available Node advertised a Role requirement, Mission Intelligence moved the request
to `Blocked` without submitting its otherwise valid MissionPlan.

That behavior conflates two different questions:

- Is the requested Mission meaningful and valid in RoboGuide's semantic contracts?
- Can the current deployment schedule it now?

It also duplicates Control's Capability Matching authority. A temporarily offline provider, a Node
that registers later, and an unknown canonical capability contract all appeared as the same Mission
Intelligence rejection even though they require different handling.

## Decision

Mission Intelligence owns semantic admission. Interpreter input is the user instruction and its
dialogue only; live Node health, liveness, readiness, resources, and capacity are not interpretation
context. Planner and Reviewer continue to validate and review Mission meaning and contract shape,
but Mission Intelligence does not decide current deployment eligibility.

The Mission Request Engine depends on a narrow `MissionPlanSubmitter` contract. The HTTP adapter may
also expose inventory for other consumers, but the admission state machine cannot require that
operation through its port.

A reviewed MissionPlan is submitted to Orchestration even when the current deployment has zero
providers for one or more Role requirements. Controller acceptance means that the Mission is a valid
durable participant in RoboGuide's lifecycle. It does not mean the Mission is immediately
schedulable. Capability Matching remains the only `Who can?` authority. An empty Candidate Set is a
runtime scheduling condition: the Task remains durable and can be reconsidered when observed
deployment facts change.

`GET /v1/inventory` remains a read-only deployment view for operators, diagnostics, and a future
explicit deployability preview. Such a preview is advisory and cannot accept or reject a Mission,
select a Node, or commit a Resource.

This slice does not introduce the Canonical Capability Catalog. Consequently, deterministic
distinction between an unknown contract and a known contract with zero live providers remains an
explicit follow-up. Live inventory must not be reused as that catalog: catalog membership is stable
system-language evidence, while inventory is time-varying deployment evidence.

The existing Mission Request `Blocked` lifecycle remains for Controller submission rejection and
compatibility. Mission Intelligence no longer emits it merely because an advisory inventory lacks a
current provider.

## Consequences

- Interpreter output is stable across transient Node health, liveness, and resource changes.
- A semantically reviewed Mission may be Accepted while its Ready Task is waiting for capability.
- Control and Orchestration keep their existing Match -> Schedule -> Propose -> Commit -> Bind path;
  this decision does not change Execution Group or Runtime lifecycle semantics.
- Removing the deployment gate exposes the need for a versioned Canonical Capability Catalog and
  deterministic catalog validation before Mission submission. That is the next semantic-admission
  slice, not part of this one.
- World and semantic grounding context may be added later with provenance and freshness, but it must
  not be conflated with live Node placement inventory.
