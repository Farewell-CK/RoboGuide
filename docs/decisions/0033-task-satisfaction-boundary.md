# ADR-0033: Task Satisfaction Boundary v0.1

- Status: Accepted historical boundary; extended by ADR-0034, ADR-0039, ADR-0048 and ADR-0093
- Date: 2026-09-10

> Current scope: this ADR records the original v0.1/v0.6 boundary. ADR-0034 and MissionPlan v0.7
> add explicit expected effects plus source-aware `verifier-evidence`; ADR-0048 limits the
> implemented verifier ingress to a configured deployment-owned final source. The State plane now
> persists independently attributed evidence, while Orchestration remains the only satisfaction
> authority. ADR-0093 defines negative-verdict rejection and the replay/artifact boundary.

## Context

Runtime previously reduced successful terminal role facts to `ObservedTaskResult::Succeeded`.
Integration Server then called `MissionOrchestrator::task_succeeded()`, which marked the
Control TaskExecution `Completed`, releases Task-scoped resources, unlocks dependent Tasks, and may
complete the Mission. One transition therefore conflates two materially different claims:

- the selected Local EAIOS executions reached their successful terminal state;
- the Task's expected semantic or physical effect is accepted as satisfied.

A workflow can finish while its world effect is absent. Runtime can authoritatively report the
former from execution facts, but it does not own the latter. Conversely, requiring a generic world
predicate now would invent a verifier language and State authority that RoboGuide has not designed.

## Decision

At the time of this decision, MissionPlan v0.6 added one required Task field:

```json
{
  "satisfaction": {
    "basis": "execution-report"
  }
}
```

At that baseline, the Task `description` remained its human-readable expected outcome. `satisfaction.basis` specifies
which admitted evidence may close that outcome. v0.1 implements only `execution-report`: successful
terminal reports from every current Role execution, after Runtime relation fences are satisfied,
may be accepted by Orchestration as Task satisfaction. Historical MissionPlan v0.2-v0.5 inputs
normalize to this basis for compatibility; Mission Intelligence emits v0.6 explicitly.

The lifecycle and evidence transitions are separate:

```text
Role execution facts all Completed
  -> Runtime reports ExecutionCompleted
  -> Control TaskExecution = AwaitingSatisfaction
  -> Event: TaskExecutionCompleted
  -> Orchestration applies TaskSatisfactionBasis::ExecutionReport
  -> Control TaskExecution = Completed
  -> Event: TaskSatisfied
  -> release Task-scoped resources / unlock DAG / evaluate Mission completion
```

`TaskExecutionCompleted` proves only the terminal local execution aggregate. `TaskSatisfied` is the
explicit orchestration decision that permits DAG and Mission progression. Runtime never emits
`TaskSatisfied` and never completes a Mission. Control enforces the Task lifecycle transition but
does not choose the satisfaction policy. State remains evidence/projection infrastructure and does
not evaluate a global truth predicate.

The current Integration Server applies both transitions in one application transaction for the
implemented `execution-report` basis. The intermediate `AwaitingSatisfaction` state and distinct
events remain observable and checkpointable. Task-scoped resources are retained until satisfaction,
not merely execution completion.

At that baseline, MissionPlan v0.6 did not add an `expected_effect` duplicate because Task
`description` already owned that human-readable outcome. ADR-0034 later introduced an explicit
machine-checkable effect and source-aware verifier basis with its own contract, freshness,
provenance, and admission rules.

This decision does not change Proposal -> Commit -> Bind, Execution Group ownership, Runtime command
routing, Node Protocol, Local EAIOS Local How, or recovery selection authority.

## Consequences

- Execution completion can no longer silently mean Task satisfaction inside one lifecycle mutation.
- Existing plans preserve behavior through an explicit compatibility basis, while new plans state
  that policy rather than relying on an undocumented default.
- Audit traces can distinguish Local EAIOS completion from Orchestration satisfaction acceptance.
- The current basis is not independent physical-world verification and must not be described as one.
- Verifier identity, freshness and a deployment-owned final positive/negative verdict profile are
  implemented by [ADR-0039](0039-mission-satisfaction-freshness-policy.md) and
  [ADR-0048](0048-deployment-task-verifier-ingress.md). Production `task_verifier.rs` preserves exact
  Task/Role/attempt and source identity, records the normalized verdict in the State projection,
  and emits a durable receipt event. Positive evidence produces `TaskSatisfied`; a final negative
  verdict produces `TaskSatisfactionRejected` and the Task's `SatisfactionRejected` terminal state.
- `TaskSatisfactionRejected` is a semantic policy outcome, not a claim that the local execution
  failed. The Mission may still become `Failed` and release its Group according to the configured
  final-verdict policy, while the earlier `TaskExecutionCompleted` evidence remains distinct.
- The event stores the immutable source/artifact digest and normalized State stores source and
  receive-time evidence. Full replay of the producer document still requires the deployment-owned
  artifact identified by that digest; the event is not a replacement for that artifact.
- General State evidence beyond this verifier projection, disputed or multi-source truth, streaming
  intermediate predicates and semantic post-execution replanning remain deferred.

## Current implementation boundary (2026-10-13)

The active MissionPlan contract is v0.8; v0.2-v0.7 remain compatibility inputs. The satisfaction
slice introduced here is extended by ADR-0034/0039/0048/0093 as follows:

```text
local Role execution completes
  -> Control TaskExecution = AwaitingSatisfaction
  -> TaskExecutionCompleted
  -> State records exact-source verifier evidence (when configured)
  -> positive evidence: TaskSatisfied -> Task Completed -> release/advance
  -> negative final evidence: TaskSatisfactionRejected -> SatisfactionRejected
     -> Mission policy may fail/release, without emitting TaskExecutionFailed
```

Only the configured final-verdict profile is implemented. Missing, malformed, stale-attempt, or
source-mismatched evidence remains unavailable and cannot produce `TaskSatisfied`.
