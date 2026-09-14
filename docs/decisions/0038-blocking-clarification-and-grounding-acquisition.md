# ADR-0038: Blocking Clarification and Grounding Acquisition

- Status: Accepted for Mission Request clarification v0.1
- Date: 2026-09-14

## Context

The 29-case Mission Front-half baseline and the 14-scenario Grounding A/B canary both stopped every
Mission at Interpreter clarification. The grounded canary demonstrated that attributed evidence can
resolve referents and preserve conflicts, but the Interpreter still asked optional completion,
provider-selection, and Local How questions. The structured output already permits an empty
`open_questions` array; the missing contract was a positive rule for proceeding with a reasonable
non-blocking interpretation.

Two adjacent correctness issues affect the same boundary. A `GroundingGap` reports acquisition or
admission evidence and is not itself a user ambiguity. Also, the text-only clarification command
previously linked every answer to the latest unanswered question, even when several questions were
open and the answer addressed another one.

## Decision

### 1. `open_questions` is blocking-only

Interpreter uses four exclusive ownership classes for each possible question:

- **Blocking**: user input is required to avoid committing to a materially different target,
  desired effect, scope, obligation, permission, or safety-relevant constraint. Only this class is
  emitted in `open_questions`.
- **Defaultable**: a minimal interpretation preserves the core goal, does not add work or relax an
  explicit constraint, and does not change the Task Graph. It is recorded in `assumptions` and does
  not stop planning.
- **Control-owned**: provider, Node, Resource, placement, readiness, availability, and scheduling
  choices are not user clarification.
- **Local-EAIOS-owned**: route, pose, speed, local planning/perception, hardware action, vendor API,
  and implementation details are not user clarification. Explicit semantic user constraints remain
  Mission constraints even when Local EAIOS determines how to satisfy them.

Absence of evidence does not establish hypothetical multiplicity or danger. Dialogue answers and
admitted evidence close prior questions; Interpreter asks only for a remaining blocker. Empty
`open_questions` means Mission meaning is ready for planning, not that the Mission is schedulable,
executable, or satisfied.

### 2. Acquisition failure precedes user fallback

The Grounding reader retries only recoverable State/Memory transport failures for a configured,
strictly bounded number of attempts before constructing the immutable snapshot. Invalid schemas,
rejected records, admission exclusions, oversized payloads, redirects, and other permanent contract
failures are not retried. Independent sources remain fail-soft.

A recovered read contributes evidence and leaves no final gap. Exhausted acquisition produces one
bounded `GroundingGap`. Interpreter never asks the user to repair a source, provider, or transport.
Only when the final missing evidence leaves a blocking Mission fact unresolved and the user can
supply that semantic fact may clarification be used as fallback. `MetadataOnly` remains an explicit
content boundary, not a transient acquisition failure.

Retries occur before snapshot creation. They do not mutate an existing digest-bound snapshot or
grant Mission Intelligence State/Memory authority.

### 3. Clarification answer identity is never guessed across multiple questions

The message command accepts an optional `question_id` referencing an existing clarification
question turn. The Engine validates that it is part of the current unanswered question batch for
the same Mission Request. Unknown, stale, already superseded, or otherwise invalid identities are
rejected without changing the Request.

Without `question_id`, exactly one current unanswered question is linked automatically. With more
than one, the answer is retained as an untargeted Dialogue turn with `in_reply_to = null`; the Engine
does not select the latest question. Interpreter may use the answer content to resolve one or more
semantic blockers, but persisted reply provenance is not fabricated.

The existing `{"text": "..."}` message command remains compatible. Mission Request v0.4 persistence,
`DialogueTurn`, `IntentAssessment`, and the `open_questions != [] -> NeedsClarification` state
transition remain unchanged.

## Consequences

- Ordinary defaults can reach Planner without weakening genuine ambiguity handling.
- Fresh evidence conflicts and unresolved critical referents still require clarification.
- Provider selection and Local How no longer become user questionnaires.
- A temporary Grounding transport failure gets a bounded recovery opportunity before model input.
- Multi-question dialogue evidence retains honest reply attribution.

## Not Implemented

- semantic relevance prediction before source acquisition;
- delayed/background re-grounding or an unbounded retry loop;
- Memory content retrieval or authorization;
- State/Memory fusion, source-authority ranking, or conflict resolution;
- a new clarification-question schema or a second LLM classifier;
- changes to MissionPlan, Planner, Reviewer, Repairer, Scheduler, Control, Runtime, or Local EAIOS.
