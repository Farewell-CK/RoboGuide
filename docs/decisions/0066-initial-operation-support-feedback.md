# ADR-0066: Read-only initial operation support feedback

- Status: Proposed for review
- Date: 2026-10-04

## Context

Registered capability and current resource eligibility do not establish that a
particular navigation target has support in an agent's starting static component.
ADR-0062 supplies scoped disjoint evidence, while ADR-0053 intentionally keeps
every eligible candidate. Ranking alone cannot expose a first decision where all
supported deployment combinations contain such a miss. Neither repeating the
same model request nor turning bounded search misses into permanent exclusions
addresses that distinction correctly.

## Decision

1. Add the optional read-only Controller endpoint
   `POST /v1/missions/assess-initial-support`. Its input is the exact generated
   MissionPlan HTTP body, not a submit command. Its neutral versioned response
   binds Mission/body SHA256, reset snapshot, original source projection, Local
   How identity and receive-relative lifetime. It contains logical Task/Role
   identities and support counts, never Node or Resource identifiers.
2. Assess only the existing independent one/two-endpoint deployment profile.
   Reuse current Control Matching on a private copy with an unpersisted query
   event sink. Existing deployment placement restrictions still apply; a query
   creates no Mission, ExecutionGroup, Actor binding, interval, reservation,
   checkpoint or authoritative event. Two endpoints come from the existing
   execution profile, never the number of goal predicates.
3. Inspect only initial ready Tasks. A single Actor with multiple root Tasks
   uses the same first Task ordering as Orchestration; later serial Tasks are
   not assessed from the initial position. Enumerate at most two candidates
   per assessed slot, with at most four combinations inspected. A usable
   `blocked` result requires every deployment-compatible combination to contain
   a validated complete scoped disjoint record. Witnesses, bounded search misses
   and unknowns remain distinct; the latter two never independently block.
4. `not_blocked` means absence of that scoped shortage, not route feasibility,
   resource commitment, Controller admission or official success. No eligible
   combination, unsupported plan scope, missing evidence, restore, prior Mission
   admission or expiry produces `unavailable`. A fresh Controller's first
   admission scope lasts at most ten minutes from source receipt. Restore,
   retry and service restart cannot renew the evidence.
5. The Mission Service separately opts into required preflight **after** normal
   planning, validation, semantic review and user approval. A blocked or
   unavailable reply holds the unchanged Request in `Blocked` with typed
   `controller_preflight` SUT_SYSTEM failure evidence. Persist the preflight
   boundary before HTTP; an interrupted query restores the hold rather than
   restarting deliberation or treating it as an ambiguous Mission submission.
6. An explicit existing Request retry performs one new read-only assessment of
   the same reviewed plan/context. It does not call Interpreter, Planner,
   Reviewer or Repairer by default. ADR-0067 separately opts into bounded,
   reviewed model-proposed reconsideration without changing this default.
   Passing preflight proceeds through the existing sole
   Mission POST and its admission/ambiguity fence. Disabling configuration
   cannot bypass a saved hold. Existing cancel remains available before submit.
7. Recovery evidence v0.2 preserves the full response; outer observations v0.3
   and public Request v0.4 remain unchanged for this profile. ADR-0067 uses
   observations v0.4 only when a separate deployment recovery session exists.
   Legacy recovery v0.1 remains valid
   for old boundaries, but cannot encode preflight or conceal assessment fields.
   Exact current draft/context/body/slot checks reject detached feedback.
8. B1 archives this reached boundary using the existing early-failure protocol.
   Attributable preflight failures remain Formal population system outcomes,
   with physical benchmark unavailable. Rejection requires typed, exact-plan
   recovery evidence and no submission receipt. It cannot hide missing genuine
   execution registrations. Population and official PDDL rules stay unchanged.
9. Default-off configuration is explicit at both boundaries. The B1 launcher
   requires reset geometry, initial preferences and spatial-arrival Local How,
   freezes its service configuration and forwards the Controller flag. The
   theoretical MI budget adds one Controller timeout only when opted in; the
   actual observation deadline is not automatically extended.

## Implementation and validation

The Controller application owns the bounded assessment projection; Core still
owns Matching, Schedule, Commit and Bind. MI's Request Engine owns the durable
hold and explicit retry. Without ADR-0067 opt-in, models receive no new input;
neither profile grants live inventory or executor selection authority. This slice
leaves Local EAIOS, prompts, official goals, initial states,
robot abilities, skill budgets and external EMOS code are unchanged.

Deterministic checks cover joint shortages, retained candidates, unknown/bounded
misses, serial scope, source expiry/restore/admission, an actual loopback HTTP
query with unchanged Controller checkpoint/event log, transport faults, exact
body binding, crash recovery, one subsequent submit and B1 failure archival.
These are conformance checks, not physical execution or success-rate evidence.

## Limitations and future work

The Controller now emits assessment v0.2; archived v0.1 remains readable.
Bounded candidate diagnostics partition the query's registrations by the first
failed shared Control predicate, with exact logical slot attribution and no
Node/resource identities. Static support counts must agree with eligible counts.
A zero paired combination explicitly records `endpoint_cardinality`; a rejected
deployment contract remains a gap rather than a guessed placement subcause.
This adds observations only. Eligibility, static evidence admission, reservation
and benchmark rules are unchanged. Later healthy snapshots cannot rewrite the
original exclusion evidence. See the [v0.2 contract](../../contracts/mission/initial-operation-assessment-v0.2/README.md).

A static miss is scoped to the observed starting component and declared Local
How. It is not proof that physical tasks are globally impossible or that dynamic
motion could never connect components. Required preflight is a disclosed
deployment readiness policy; the underlying observer and preference consumer do
not become hard exclusion authorities. Assessment and admission are separate
operations: normal admission revalidates current Control facts and may still fail.

This change does not synthesize routes, reassign an Actor, add executors, change
goals, reset a world or automatically rewrite a plan. Grounded reorganization of
a held plan uses the separately configured bounded proposal boundary in ADR-0067.
Real validation of the opted-in profile is still required; this ADR does not
claim Episode3 is solved or authorize bulk experiments.
