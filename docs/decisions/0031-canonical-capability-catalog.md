# ADR-0031: Canonical Capability Catalog v0.1

- Status: Accepted for Mission Semantic Admission v0.2
- Date: 2026-09-10

## Context

ADR-0030 separated Mission semantic admission from current deployment feasibility. Once live
inventory no longer rejects a plan with zero providers, Mission Intelligence needs a stable way to
distinguish an unknown model-invented contract from a known contract that is temporarily
unschedulable.

MissionPlan JSON Schema validates only the syntax of `namespace`, `name`, and `version`. It cannot
prove that `delivery.magic_move@v1` belongs to RoboGuide's current semantic language or that its
parameters match the contract. Live Node registration cannot serve as that language because it is
time-varying, partial, and owned by deployment observation.

## Decision

Add the versioned `roboguide.capability-catalog/v0.1` artifact under
`contracts/capability/v0.1/`. Each entry contains:

- one exact canonical `CapabilityContractRef` using ADR-0017 identity rules;
- a provider-neutral semantic description;
- a closed scalar parameter definition with requiredness and value type.

Mission Service loads the configured Catalog at startup. The Planner receives the complete Catalog
alongside GroundedIntent and may only emit listed contracts and parameters. Deterministic Catalog
validation runs before semantic review and again at Mission Request admission. Unknown contracts,
unknown parameters, missing required parameters, and incompatible scalar types fail admission.

The Reviewer receives the same Catalog so it can assess the selected canonical semantics, but model
review never replaces deterministic validation.

Catalog membership is independent of live provider availability:

```text
unknown contract
  -> invalid Mission draft

known contract + zero current providers
  -> valid Mission
  -> Control Matching produces no candidate
  -> durable scheduling deferral
```

The Catalog is configuration-owned semantic evidence. It is not Shared State, Node inventory,
operation discovery, resource authority, or a Scheduler input. This slice does not change Control,
Proposal -> Commit -> Bind, Execution Group, Runtime, or Local EAIOS authority.

The existing MissionPlan name `capability_contract` currently identifies the canonical execution
contract. This ADR does not freeze that coupling as the final Capability model. Multiple capability
requirements, feasibility envelopes, embodiment metadata, and a distinct semantic Operation or
objective-bearing ExecutionIntent require a later versioned MissionPlan decision.

The trusted internal `POST /v1/missions` compatibility path is unchanged in this slice. Catalog
admission is enforced by the external Mission Request front-half; moving the same validation into a
cross-language Orchestration boundary requires a separately shared contract implementation.

## Consequences

- Planner prompts receive a bounded, inspectable vocabulary instead of inventing contract names.
- Current Node outages and future registration do not alter Mission meaning.
- Catalog and individual contract versions evolve independently.
- Parameter validation is deterministic, offline, and transport-neutral, but v0.1 remains limited to
  the scalar parameter model already supported by MissionPlan.
- Node-advertised operation discovery, constraint schemas, structured values, and catalog
  distribution/version negotiation remain deferred.
