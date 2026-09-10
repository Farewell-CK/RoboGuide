# ADR-0033: Task Satisfaction Boundary v0.1

- Status: Accepted for Task Satisfaction Boundary v0.1
- Date: 2026-09-10

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

MissionPlan v0.6 adds one required Task field:

```json
{
  "satisfaction": {
    "basis": "execution-report"
  }
}
```

The Task `description` remains its human-readable expected outcome. `satisfaction.basis` specifies
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

MissionPlan v0.6 does not add an `expected_effect` duplicate because Task `description` already owns
that human-readable outcome. Reviewer/Planner prompts must treat it as an outcome, not a procedure.
A future machine-checkable effect or State evidence basis requires its own versioned contract,
source identity, freshness, provenance, and admission rules.

This decision does not change Proposal -> Commit -> Bind, Execution Group ownership, Runtime command
routing, Node Protocol, Local EAIOS Local How, or recovery selection authority.

## Consequences

- Execution completion can no longer silently mean Task satisfaction inside one lifecycle mutation.
- Existing plans preserve behavior through an explicit compatibility basis, while new plans state
  that policy rather than relying on an undocumented default.
- Audit traces can distinguish Local EAIOS completion from Orchestration satisfaction acceptance.
- The current basis is not independent physical-world verification and must not be described as one.
- `state-evidence`, verifier identity, negative satisfaction evidence, disputed evidence, and
  post-execution recovery remain deferred.
