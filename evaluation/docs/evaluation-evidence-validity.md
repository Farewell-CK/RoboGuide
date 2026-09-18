# Evaluation Evidence Validity Protocol — F-06 / F-07

Defines how the Eval Harness derives benchmark outcomes and admits Formal B1
runs into statistics. Both rules are enforced by deterministic modules with
test coverage; neither changes the benchmark authority (official Habitat
`pddl_success`) or the EMOS/RoboGuide protocol definition.

## F-06 — Benchmark evidence tri-state authority

Module: `evaluation/src/roboguide_eval/benchmark_evidence.py`

- `BenchmarkOutcome` is a **tri-state**: `BENCHMARK_TRUE`,
  `BENCHMARK_FALSE`, `BENCHMARK_UNAVAILABLE`. A boolean is produced only
  when the authoritative shared-world summary explicitly carries
  `official_pddl_success` as a strict bool. Missing/malformed document,
  missing field, or non-bool field stays `UNAVAILABLE` — never `false`.
- **Local outcome completeness**: the expected shared-world agent population
  is `("0", "1")`. `local_skill_completed` / `local_agent_failure` /
  token/replan/message aggregates are computed only when **every expected
  agent** reports the relevant typed field. Empty or partial populations
  yield `None`/omitted — `all([]) == True` can never masquerade as
  "both agents completed".
- **Run validity** (`RunValidity`) is classified independently of the
  benchmark outcome: `VALID_RUN` / `INVALID_INFRA` / `SYSTEM_FAILURE` /
  `MODEL_FAILURE`, driven by explicit evidence-completeness,
  episode-start, termination-fact, infrastructure, mission-status, and
  process-status facts — not by grepping a log for `Error:`.
- `valid_for_benchmark_population` accompanies every reduced run.
  `summarize_results` counts success rate **only** over runs with
  `valid_for_benchmark_population = true`; `BENCHMARK_UNAVAILABLE` /
  invalid runs never enter the denominator, and their evidence is fully
  retained with machine-stable exclusion reasons.

## F-07 — Formal B1 provenance chain

Module: `evaluation/src/roboguide_eval/b1_provenance.py`
(+ the rewritten `scenarios/e1-shared-world-episode-51/verify-b1.py`)

A Formal B1 run binds a machine-verifiable chain:

```
frozen high-level input digest
  → MI invocation (request_id) identity
  → accepted MissionPlan canonical digest (SHA-256, sorted-key compact JSON)
  → Controller MissionId / submission identity
  → Controller-executed plan == accepted plan (digest equality)
  → Habitat benchmark evidence digest (shared-world summary)
```

- **Digest canonicalization is fixed**: UTF-8 JSON, sorted keys, compact
  separators, `ensure_ascii=False` — byte-stable across platforms.
- **Static B2 stays B2**: `mission-plan.json` remains the fixed-plan
  diagnostics fixture. The B1 anti-forgery gate digests both plans with
  only `mission.id` blanked — copying the B2 plan and changing the
  MissionId still fails (`static_b2_plan_equality`) because every other
  byte matches the fixture, while genuine MI output with completely
  different TaskIds/decomposition passes.
- **B1 verifies canonical semantic intent**, not structural equality with
  the B2 fixture: official goal coverage (both `any_targets|0` and
  `TARGET_any_targets|0` destinations), accepted-plan → Controller
  execution consistency (every accepted destination appears in Controller
  mission evidence), and the full provenance chain. MI legally producing
  different TaskIds is not a failure.
- **Population admission**: `admit_to_formal_population` combines the
  provenance verdict with the F-06 tri-state outcome and run validity.
  Formal statistics consume only `valid_for_formal_population = true`;
  excluded runs keep evidence plus machine-stable `invalid_reasons`.
  `verify-b1.py` is a consumer of this single authority, never a second
  one: it feeds canonical evidence facts (tri-state assessment, run-validity
  classification from the bridge-written `identity.episode_terminated`,
  provenance verdict) into `classify_run_validity` and
  `admit_to_formal_population` instead of re-deriving admission policy.
- **Authority separation**: the benchmark population is an evidence-validity
  classification (`valid_for_benchmark_population`) and stays independent of
  provenance; the formal population is the provenance-gated subset
  (`provenance_passed ∧ valid_for_benchmark_population`). A provenance
  failure therefore fails the B1 gate and excludes the run from formal
  statistics without mutating the benchmark-evidence classification, and an
  unavailable benchmark outcome excludes a run from both populations even
  when its provenance chain is complete.
- **Forward compatibility**: the provenance record carries
  `schema_version` (`roboguide.e1.b1-provenance/v0.1`) and stable digest
  fields so later semantic-ingress work can add canonical semantic input
  and grounding-context digests without breaking the format.

## Non-goals

Semantic ingress (authoritative Habitat/PDDL goal predicates entering MI)
is explicitly out of scope here and tracked for a later round; the
provenance format reserves versioned fields for it.
