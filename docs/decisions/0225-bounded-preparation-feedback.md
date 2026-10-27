# ADR-0225: Bounded feedback for an unexecuted preparation rejection

- Status: Implemented; new physical validation pending
- Date: 2026-10-28

## Context

An opted-in observation acquisition can select a fresh navigation command after
an observed stationary scan. The adapter must first find a supported local
viewpoint using the original bounded geometry, route and scan checks. An empty
bounded search is unavailable preparation evidence. It does not prove global
infeasibility, a model contract violation, or an executed navigation failure.

Previously that rejection escaped the original tool mapping and terminated the
live session. The existing pre-dispatch model reselection path handled invalid
tool choices but could not return this local capability result to the model.

## Decision

Local How v0.19 retains v0.18's compound observation and initial geometry
contracts and adds `action_preparation_feedback` under the explicit
`bounded-pre-dispatch-preparation-feedback/v0.1` declaration. The existing
`contract_recovery_attempts` bound covers both contract and eligible preparation
rejections. It is never renewed per model call, physical step or search.

Only the following original chain permits reselection:

1. The Guard admits the exact model-selected navigation tool and target.
2. Original CrabAgent mapping returns the expected local navigation invocation.
3. The attempt-bound viewpoint preparation raises a typed bounded search miss
   before navigation latches or physical skill entry change.
4. Feedback retains that same invocation digest, agent, call identity, raw
   action and zero physical-step count, and replaces the optimistic tool
   acknowledgment with an explicit `preparation-rejected` observation.
5. If the original attempt still has correction budget, the same model receives
   the original task/history plus the rejection facts and may select a new tool.

The rejection remains a local capability preparation result. It is not attributed
to a hallucinated model action. No fallback action, entity replacement, automatic
wait, delegated operation or sensor truth is created. Model-selected wait retains
the existing disclosed acquisition pause bound. Every new action passes the
same Guard and original tool mapping.

Only an exact `ViewpointSearchMiss` is eligible. Missing/changed geometry,
exhausted search authority, malformed mappings, arbitrary exceptions and Provider
errors propagate without this recovery. Once a physical step follows a call,
its refusal cannot acquire pre-dispatch authority. The original two-search
quota, per-search geometry bounds, invocation lifetime and world horizon remain
unchanged. Current peer policies and accepted terminal facts remain unchanged.

Runtime source preflight and the independent evaluation reader require the
complete new declaration and exact Boolean types. Older Local How declarations
keep their original meaning and cannot carry the new preparation authority.
Ordinary navigation and relocation retain their existing paths. Original EMOS
files, model Prompts, canonical operations, MissionPlan, Core, official PDDL and
Formal population rules are unchanged.

## Verification and limits

Deterministic tests use the production Guard, feedback, original-shaped tool
mapping, navigation lifecycle and reversible viewpoint hooks. They cover a fresh
model decision after an unexecuted miss, shared and exhausted budgets, old-profile
behavior, exact rejection ownership, physical-step fencing, original search quota,
Provider/unknown errors, archival failure and successful navigation.

Offline acceptance does not establish that a model will select an effective next
action, that a supported viewpoint exists, or that a benchmark goal will succeed.
The currently frozen batch must continue on its original code and declarations;
new physical checks use a separately recorded version and never replace its
primary outcomes.

This change does not solve team-world versus executor-local completion scope.
A global positive verdict still cannot fabricate an executor's own detection,
reopen a terminal Mission or release an uncompleted operation.
