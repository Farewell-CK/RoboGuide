# Formal B1 evidence and population protocol

This document describes the F-06/F-07 closure implementation. It changes
evaluation provenance and read-only Mission Service observability, not
MissionPlan, Control, Scheduler, Actor identity, or Habitat verification.
No real provider, Formal B1, or Pilot execution is part of its validation.

## Population authority

`roboguide_eval.b1_admission.assess_b1_run` is the sole admission policy:

```text
formal    = provenance_valid AND NOT external_infrastructure_invalid
benchmark = formal AND authoritative Habitat pddl_success available
```

Only official Habitat `pddl_success`, exported as
`official_pddl_success` in the shared-world summary, determines benchmark
success. A strict boolean false is a valid benchmark failure observation.
Missing/malformed/non-boolean authority is `BENCHMARK_UNAVAILABLE`; it is
never filled with false or derived from Mission/Task/local-skill completion.

| Case | Formal | Benchmark | Outcome | Failure owner |
| --- | --- | --- | --- | --- |
| A: authoritative true | yes | yes | true | NONE |
| B: authoritative false | yes | yes | false | NONE |
| C: attributable early SUT failure | yes | no | unavailable | SUT_SYSTEM |
| D: attributable early model failure | yes | no | unavailable | MODEL |
| E: explicit external infrastructure failure | no | no | retain available authority | EXTERNAL_INFRA |
| F: missing/tampered provenance | no | no | retain available authority | preserve observed owner |
| G: valid provenance, unavailable benchmark | yes | no | unavailable | BENCHMARK_AUTHORITY_UNAVAILABLE |

SUT and model failures are formal experiment observations. Excluding them
because Habitat never started would bias the population toward survivors.
Neither summary absence, episode-start absence, Controller death, nor a
generic nonzero shell exit establishes external infrastructure failure.
External host/harness/evaluator/environment/provider failure requires
explicit attributed evidence. Controller/node/Mission Service/Local EAIOS
process failures are SUT failures unless separate external evidence exists.

`RunValidity` records VALID_RUN, SYSTEM_FAILURE, MODEL_FAILURE,
INVALID_PROVENANCE, or INVALID_INFRA. This classification is independent of
the raw benchmark boolean and of process exit status.

## Execution provenance v0.2

`roboguide.e1.b1-provenance/v0.2` links:

1. Archived frozen input and initial User/Instruction dialogue.
2. Exact public MI request record and separately versioned observations.
3. Final generated/admitted draft revision and digest.
4. The digest of the actual bytes supplied to HTTP POST /v1/missions.
5. Controller response MissionId/GroupId and current-group Task registrations.
6. Current Mission/Group execution attempts or attributable boundary failure.

The MI digest and actual-submission digest both use
`sha256:<hex>` over sorted-key, compact UTF-8 JSON with
`ensure_ascii=False`. Artifact-link fields deliberately use unprefixed hex;
conversion is explicit by construction, not string slicing.
Production MI `_plan_digest` delegates to this same canonicalization in the
submission-observability module. Evaluation implements the identical
canonicalization and tests cross-package equality, including Unicode.

A string-valued `draft_digest` is insufficient: it must exactly hash the
final `record.plan`. If review history exists, its last approved entry must
reference this final revision and digest. Repair cannot reuse the old draft
hash. Copied static B2 fixtures receive no exception; every B1 run needs the
same complete reached-boundary evidence. Genuine MI output is not rejected
merely for using the same structure or different TaskIds.

The independent submission observation hashes `urllib.request.Request.data`
immediately at the HTTP boundary. It retains canonical body digest, raw-byte
SHA-256, submitted MissionId, timestamp, response status, response
MissionId/GroupId, and any transport error. Equal TaskIds alone never prove
equal MissionPlans: changing destination, Actor, intent, or Context constraints
breaks the plan-digest gate.

The benchmark digest is an optional, independent link. Missing benchmark
evidence does not invalidate execution provenance. A present benchmark that
disagrees with its recorded link becomes unavailable for benchmark assessment;
it does not rewrite execution identity. v0.1 is rejected rather than silently
reinterpreted under these rules.

Evidence producers and the archive collection environment are trusted.
SHA-256 detects inconsistent/tampered links; it is not a digital signature
against an actor able to replace every source artifact and all hashes.

## Read-only Mission Service observations

`GET /v1/mission-requests/{request_id}/observations` returns
`roboguide.mission-request-observations/v0.1`. It binds to the exact
public request projection using `request_record_digest` and contains:

- `roboguide.controller-submission-evidence/v0.1`, when HTTP submission was attempted.
- `roboguide.mission-request-failure/v0.1`, when a known MI/submission boundary failed.

The public Mission Request v0.4 and all MissionPlan contracts are unchanged.
Private SQLite rows now use a versioned request/observations envelope in
the existing transaction. Existing bare request rows remain readable with
observations unavailable. New envelopes are not readable by older binaries;
downgrades require an explicit storage migration, not silent observation loss.

Request and observations are fetched as a matched pair with bounded retries.
A collection race or missing sidecar fails the provenance gate; it never
assumes that the only plan in the MI record was the one sent.

A model failure before any plan exists validates frozen input -> real request
-> explicit failing MI stage. A Controller rejection/transport failure validates
the final MI plan -> actual POST attempt -> failure. A scoped accepted Mission
failure can replace missing execution attempts. A SUT startup failure before
MI exists requires a run-bound, input-digest-bound process-boundary observation;
it does not invent MI execution, accepted plans, or Controller receipts.

## One decision consumed end to end

`b1_run.assess_b1_directory` extracts scoped evidence and invokes the
single admission function. The scenario verifier persists
`roboguide.e1-b1-verdict/v0.2`, containing the
`roboguide.e1.b1-admission/v0.1` decision.

The verdict explicitly separates:

- protocol provenance validity;
- system outcome (COMPLETED / FAILURE / UNKNOWN);
- formal and benchmark population admission;
- benchmark tri-state.

Its top-level PASS/FAIL means formal protocol admission only. A valid SUT
failure can therefore have PASS admission, FAILURE system outcome, formal
admission true, benchmark admission false, and benchmark outcome unavailable.

`RoboGuideRunner.collect_result` uses the Formal B1 path for
`run-b1-roboguide.sh`, explicit `ROBOGUIDE_EVAL_PROTOCOL=B1`, or archived
B1 evidence. Custom launch wrappers must declare that environment setting.
It consumes the persisted verdict and checks it against current canonical
verification. Missing, modified, or stale admission fails closed; it never
falls back to shared-world/B2 admission.

Admission flags are registered canonical metrics, including
`valid_for_benchmark_population`. The artifact-chain test validates the
MetricsPayload before writing it, so production metric validation cannot
silently discard otherwise correct admission values. A Harness OS-level
process-launch failure is separately recorded as explicit external infrastructure.

`summarize_results` consumes metrics only. Benchmark rates count only
benchmark-admitted runs. Formal system/model-failure counts count only
formal-admitted observations. Infrastructure counts use explicit
infrastructure metrics, not absence of benchmark authority.

The scenario EXIT handler archives evidence and runs provenance/admission
before stopping its own children, including early exits. It never deletes an
existing B1 archive or kills a different run's port listener. This is evidence
collection, not an authorization to run the scenario.

## Verification

Offline regressions use the real MI engine and local HTTP Controller stub:

```text
frozen input
 -> real-shaped MI request + actual HTTP receipt
 -> scoped Controller Mission/events and attempts or failure
 -> optional Habitat evidence
 -> provenance verification
 -> canonical admission
 -> persisted verdict
 -> RoboGuideRunner.collect_result
 -> metrics.json
 -> summarize_results
```

Tests cover A-G, Controller rejection before dispatch, pre-MI SUT process
failure, placeholder/stale repaired digests, unchanged TaskIds with changed
plan content, missing/stale/tampered admission, request/observation read races,
and cross-Mission / wrong-Group contamination.

Semantic ingress of authoritative Habitat goal predicates into MI remains
separate integration work. These changes do not implement it or establish
Formal E1 benchmark results.
