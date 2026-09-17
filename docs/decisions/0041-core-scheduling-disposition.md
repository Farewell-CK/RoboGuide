# ADR-0041: Typed scheduling disposition at the application boundary

- Status: Proposed for review
- Date: 2026-09-17

## Context

An accepted Mission can temporarily lack enough PhysicalEntities for a Context's distinct
Actors. Matching correctly rejects the selection, but application string matching previously
treated the rejection as a process-fatal error. A second Mission must not lose service because
the first Mission needs additional deployment evidence.

## Decision

Orchestration exposes typed scheduling dispositions: deferred, reconciliation-required,
invalid-contract, and internal-failure. A closed classification at the orchestration boundary
interprets Control and Scheduler errors. Application dispatch consumes only that disposition;
it never infers retry policy from diagnostic text. Contract decoding and implementation
preflight use an explicit invalid-contract error, while inconsistent accepted execution state
remains an internal failure. Unexpected errors are not silently made retryable.

Retryable matching shortages leave the Task Ready, unbound, and uncommitted. Orchestration
persists a typed, deduplicated deferral reason and emits the existing TaskSchedulingDeferred
evidence. Cardinality, missing grounding evidence, placement unavailability, and an unavailable
bound Actor remain distinct reasons; reconciliation still belongs to Control. Other Missions
continue, and a later application pass retries against current registry and Node evidence.
Window-missed deferrals retain their existing stop-retrying behavior.

Deadline minus duration uses checked arithmetic in Scheduler and in checkpoint timing
reconstruction. An estimate that cannot fit the activation window returns WindowMissed,
including subtraction below zero; timestamp addition overflow remains an explicit error.
Equal duration and available window permits activation at the exact inclusive boundary.
Zero duration remains invalid source evidence; absent duration remains unknown, not zero.

Checkpoint reason strings retain the existing kebab-case representation. Known historical
reason strings deserialize into the typed enum; unknown strings fail restore rather than
silently inventing a retry policy. No MissionPlan, Node Protocol, event schema, commitment
authority, Actor migration policy, or Local EAIOS execution authority changes.

## Validation

Deterministic application tests exercise two sequential Tasks with distinct Actors and one
entity alongside an unrelated Mission. Satisfaction of the first Task persists without
terminating the timer; the second Task remains unbound with observable deferral evidence.
After a second entity and eligible Node are supplied, the same Task binds and dispatches.
Classification tests distinguish waiting conditions from invalid contracts and internal faults.
