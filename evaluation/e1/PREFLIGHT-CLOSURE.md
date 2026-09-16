# Formal E1 Preflight Closure

Date: 2026-09-16

This record closes the production-correctness and measurement work completed
before Formal E1. It is an admission record, not benchmark output.

## Closed checks

- **C1-S1 status continuity: PASS.** Node Service reacquires bounded transient
  status failures through the existing durable local handle. It never repeats
  `Execute`, creates a second handle, or fabricates a phase. The post-fix F1,
  F2, F3, F4, and F8 production matrix reaches the true local terminal fact;
  budget exhaustion remains explicit physical ambiguity.
- **Controlled local policy fidelity: PASS for the Stage2 boundary.** RoboGuide
  replaces only the EMOS Stage1 `group_discussion()` assignment result. The
  checked-out EMOS `MultiLLMPolicy`, `LLMHighLevelPolicy`, `CrabAgent`,
  `HierarchicalPolicy`, and configured Oracle navigation skill then run.
- **Controlled skill smoke: PASS.** The production path submitted through the
  Controller, performed one physical dispatch, selected `nav_to_obj`, changed
  the Habitat robot position, observed `RUNNING`, and reached a true Habitat
  semantic-task terminal without hidden direct-Oracle fallback. Its explicit
  terminal basis was `habitat-pddl-success`; the stricter Oracle `skill_done`
  remained false and was not rewritten.
- **Outcome separation: PASS.** Local skill completion, Habitat PDDL success,
  episode termination, RoboGuide Mission completion, and process success are
  persisted and reduced independently. Direct-Oracle evidence demonstrates
  local skill completion without benchmark success; Controlled evidence
  demonstrates benchmark/semantic completion before the stricter skill pulse.
- **Runner and metrics plumbing: PASS.** The EMOS runner consumes official
  evaluator/token evidence. The RoboGuide runner consumes persisted production
  Controller/Node/Local-EAIOS evidence. Metrics v0.3 separates global/local
  calls and tokens, local policy activity, physical dispatch, and failure
  categories. A real EMOS runner smoke resolved episode 51 and recorded official
  PDDL success; a real RoboGuide runner invocation retained a model-driven
  `wait|wait`/step-budget failure as failure evidence rather than hiding it.

## Controlled policy boundary

The held-constant local policy starts after organization has produced an
assignment. EMOS obtains `AgentArguments` from its Leader/Stage1 path;
RoboGuide supplies committed `AgentArguments` at the same Stage1-to-Stage2
boundary. Prompting, invalid-output behavior, wait/finish, peer requests,
skill selection, pre/post conditions, local replanning, skill step budgets,
and failure propagation after that boundary remain the original EMOS source.

RoboGuide cancellation remains an intentional system-level difference and is
checked between simulator steps. Assignment wording is organization output and
is retained as evidence rather than normalized away. Provider sampling values
not exposed by the shared original EMOS model wrapper are recorded as provider
defaults rather than guessed.

## Success authority

Formal E1 benchmark success is the official Habitat `pddl_success` predicate.
None of the following independently implies benchmark success:

- a local skill reached its terminal measure;
- the Habitat episode terminated;
- RoboGuide emitted `TaskSatisfied` or `Mission Completed`;
- the runner process exited successfully.

The runners preserve these facts separately. Infrastructure, system,
local-agent, and model failures are also separate classifications; an absent
source remains unavailable rather than becoming false or zero.

## Remaining admission blocker

The frozen candidate, Habitat-MAS mobility episode 51, has two simultaneous
semantic goal predicates:

1. `any_at(any_targets|0)`;
2. `any_at(TARGET_any_targets|0)`.

The official EMOS runner executes both goals in one shared two-agent episode.
The current RoboGuide C1-S0B production Mission commits one
`mobility.navigate@v1` operation to `any_targets|0`. It therefore validates the
production boundary and held-constant Stage2 behavior, but does not execute the
same complete semantic benchmark task.

The exact machine-readable state is frozen in
[`controlled-workload-v0.1.yaml`](controlled-workload-v0.1.yaml). Formal paired
runs must not begin until a production RoboGuide workload covers both official
goal predicates in the same simulator episode (or a different candidate is
proved semantically equivalent for both runners) without bypassing
Proposal -> Commit -> Bind or the Node/Local-EAIOS boundary.

## Status

**FORMAL E1 PREFLIGHT = BLOCKED**

Exact blocker: the current RoboGuide production workload covers only one of
episode 51's two official semantic goal predicates, so the two runners do not
yet consume the same complete task.
