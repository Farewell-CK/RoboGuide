# ADR-0048: Deployment-owned final Task verifier ingress

- Status: Proposed for review
- Date: 2026-09-28

## Context

MissionPlan can require an independent `verifier-evidence` basis, but a local
`Completed` only proves that execution ended. Before this change, production
Controller had no generic path for an external verdict to satisfy such a Task.
A B1 run could therefore report official Habitat success while its Task stayed
`AwaitingSatisfaction` and its Mission stayed `Running`. Treating local skill
completion, a capability name, or the benchmark metric copied into a Mission
result as proof would collapse separate authorities.

## Decision

The Controller composition may be configured with one run-local verifier
source and one atomic verdict artifact. Both are versioned, bounded JSON
documents. The source is loaded before Mission submission and freezes its
producer identity, exact verifier contract and predicate, source revision, and
run/episode/scene/dataset identity. A plan requiring verifier evidence is
rejected if no configured source advertises the exact contract and predicate.
The current source produces a **final** verdict only, so a verifier-backed
Task cannot be a prerequisite of a later Task in this deployment profile. In
the one-Actor retained-world topology, a verifier-backed Task must also be
ordered after every other Task by the accepted DAG; otherwise the final world
verdict could be required before the next physical segment is dispatched.

The Habitat adapter publishes the source only after its one real reset and
authoritative semantic snapshot. For the supported exact joint goal expression,
it projects the official terminal `pddl_success` boolean into a final verdict.
It does not infer truth from local navigation completion, pose diagnostics, or
the number of goal predicates. Unsupported goal syntax or an unavailable metric
produces no verdict. The verdict names every contributing Mission/Task/Role and
the physical attempt ID supplied by Runtime through the existing Node workflow
mapping. A failed evidence write does not rewrite physical execution outcome.

Controller accepts a verdict only when its digest, frozen source, exact Task
specification, and complete current Role attempt set match. It records a
`TaskVerifierVerdictObserved` event in the same event/checkpoint transaction as
the Orchestration transition. A positive verdict follows existing
`satisfy_task_from_verifier` policy, using Controller receive time for the
declared freshness bound. A final negative verdict fails the awaiting Task; it
does not claim local execution failed. Already processed verdict/Task receipts
survive restart, and a restored Controller refuses a different source digest
for the same database. Missing or invalid evidence remains unavailable and cannot
produce `TaskSatisfied`.

B1 archival verification independently binds the source and verdict to its
frozen input, authoritative semantic digest, observed physical attempts, and
Controller events before admitting the run's provenance. Formal admission and
benchmark population rules remain separate, and Habitat remains the sole
authority for official `pddl_success`.

## Boundaries and limits

The new source/verdict artifacts are deployment evidence, not MissionPlan
fields, a live Node inventory, or a second Control commitment authority. This
profile supports one exact official terminal goal predicate per run. It does
not provide streaming verification, intermediate Task predicates, source
arbitration, or generic cross-environment truth. Deployments without a
configured source retain execution-report behavior and reject verifier-backed
plans at Controller admission. The source file is a trusted deployment-owned
local input; this ADR does not introduce a remotely authenticated verifier
protocol. Its source time is retained for attribution, not compared with the
Controller's independent clock.

ADR-0039's freshness policy and the MissionPlan schema remain unchanged.
