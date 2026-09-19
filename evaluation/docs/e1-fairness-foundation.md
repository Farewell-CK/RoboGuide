# E1 Fairness Foundation — Design

Date: 2026-09-19. Baseline: `main@7d35893`.
Module: `evaluation/src/roboguide_eval/e1_fairness.py`.
Tests: `evaluation/tests/test_e1_fairness.py` (37 cases).
Fixtures: `evaluation/tests/fixtures/e1_fairness/`.

Purpose: turn the E1 comparison methodology ("same workload, only the
organization path differs") from prose in `FAIRNESS_LEDGER.md` into
machine-checkable artifacts that the harness, the pilot runner, and future
Formal E1 runs all share. Nothing in this module integrates with runners or
verifiers yet, and nothing here touches Formal population admission.

## The three layers

1. **PopulationManifest** (`roboguide.e1.population-manifest/v0.1`) — the
   frozen experiment definition: dataset (name/revision/sha256), task
   (benchmark/task/task-spec digest/habitat-config digest), benchmark
   authority (measure, implementation digest, evaluator parameters),
   embodiment profile, Stage2 file surface digests, simulator identity,
   model configuration, the allowed organization-axis differences, and the
   selector (`explicit_set` rows with pair id, seed, expected episode and
   scene; `all`/`predicate` are future policies the data model already
   accommodates). One content digest binds everything; changing any
   semantic field changes the population identity.
2. **RunPairingEvidence** (`roboguide.e1.run-pairing-evidence/v0.1`) — one
   arm's per-run facts. `requested` (declared seed/episode) is recorded
   intent and never compared as evidence; `observed` maps dimension ids to
   `{value, source, status}` where status is `AVAILABLE`, `UNAVAILABLE`
   (value must be null), or `INVALID` (malformed evidence kept for
   diagnosis). `system_outcome`, `environment_fingerprint`, and
   `additional_observations` are recorded verbatim and never gate.
3. **PairManifest** (`roboguide.e1.pair-manifest/v0.1`) — the verdict for
   one EMOS run + one RoboGuide run: per-dimension comparisons, overall
   `PAIR_COMPARABLE` / `PAIR_NOT_COMPARABLE`, machine-stable reasons,
   reproducibility warnings, recorded system outcomes, and unlisted
   differences.

## Dimension classification (A–D)

| Dimension | Class | Gates? |
| --- | --- | --- |
| dataset_identity | A. Pairing key | yes |
| episode_identity | A. Pairing key | yes |
| scene_identity | A. Pairing key | yes |
| task_spec_identity | A. Pairing key | yes |
| population_manifest_identity | A. Pairing key | yes |
| embodiment_profile | B. Required held constant | yes |
| habitat_config_identity | B. Required held constant | yes |
| benchmark_authority_identity | B. Required held constant | yes |
| stage2_identity | B. Required held constant | yes |
| model_configuration_identity | B. Required held constant | yes |
| simulator_identity | C. Reproducibility metadata | warn only |

D. **Allowed differences** are not dimensions: organization axis (EMOS
Stage1 vs MI+Control), global planning tokens/calls, assignment wording,
MissionPlan decomposition, scheduling details, local action trajectories,
replanning, wall time, and success/failure outcomes. They are declared on
the population manifest for the record and are never compared. Benchmark
*results* (including `BENCHMARK_UNAVAILABLE` caused by a system failure)
belong here; benchmark *authority* (which measure and parameters define
success) is held constant — the pair validator can never confuse the two.

Comparison semantics per dimension, in priority order: a missing observed
field is `OBSERVED_EVIDENCE_MISSING`; malformed evidence is
`OBSERVED_EVIDENCE_INVALID`; explicit `UNAVAILABLE` maps to the dimension's
own unavailable reason; two available values compare by canonical digest
equality; a mismatch additionally triggers a declared-vs-observed check of
each arm against the population's frozen identity, so both arms drifting
*together* away from the definition is still caught (this closes the
"both arms ran the wrong dataset" hole that pure cross-arm comparison
misses). For episode/scene the selector row's expectation is the declared
value. Equal requested seeds with different resolved episodes add the
dedicated `seed_equal_episode_different` code on top of the mismatch.

## Reason codes

All decisions come from the typed `FairnessReason` enum (27 codes); free
text exists only as detail. The protocol-required codes from the E1 brief
are all present, plus completing per-dimension unavailable/mismatch codes
so every gating dimension has an explicit pair. Unknown dimension ids in
run evidence fail the pair closed with `unlisted_required_difference` —
the loader tolerates them so the validator, not the parser, owns that
decision.

## Canonicalization and integrity

Digests are `sha256:<hex>` over sorted-key, compact, UTF-8,
`allow_nan=False` JSON (`canonical_json_bytes`), matching the existing
B1/provenance canonicalization style. A manifest's digest field is excluded
from its own digest body; `from_json` recomputes and rejects any mismatch
(`digest_mismatch`), which makes tampering and stale-replay detectable at
load time. Unsupported schema versions and selector policies fail closed
(`schema_version_unsupported`) — no silent reinterpretation.

## Fairness versus Formal population (hard separation)

Formal admission is and remains:
`valid_for_formal_population = provenance_valid AND NOT external_infrastructure_invalid`,
owned by the B1 evidence modules. The fairness validator answers only "did
both arms run the same workload?" and never reads or writes that decision.
A RoboGuide planning/control/node failure can make the *outcome* a failure
— the pair stays comparable and the failure is recorded in
`system_outcome`. Only failures that destroy workload-identity *evidence*
(unresolvable episode, unreadable dataset, missing model identity) make a
pair not comparable, and those are reported as evidence unavailability,
never as system failure.

## Fingerprint designs (recorded, not yet wired)

### Stage2 fingerprint (`stage2_surface.json`)

The held-constant Stage2 surface is the **precise file digest set**, not the
checkout commit: `habitat-mas` CrabAgent/model wrapper/text sensors,
`habitat-lab` task config + PDDL task spec + PddlSuccess measure + sim-state
predicate evaluation + oracle skill actions, `habitat-baselines` runner
config, and the navigation policy stack. The checkout commit is recorded as
reproducibility context. Excluded from the hard surface: `__pycache__`,
datasets (covered by dataset identity), run-produced artifacts. The
required-surface list lives in
`fixtures/e1_fairness/stage2-surface-example.json`; both arms digest the
same list at run time and the two maps compare under `stage2_identity`.

### Benchmark authority fingerprint

`benchmark_authority_identity` = measure name (`pddl_success`) +
implementation digest (the module file implementing `PddlSuccess` and its
`any_at` state evaluation) + resolved parameters (`must_call_stop`,
`robot_at_thresh`, `max_episode_steps`, `end_on_success`) read from the
resolved Habitat config, not from source defaults. All of it is
evaluation-plane metadata: it is recorded for fairness and never surfaces
to Mission Intelligence (no semantic-ingress leakage).

### Environment fingerprint

Recorded per run, warning-only: OS/platform, Python version, Conda
environment name, CUDA device, habitat-lab/habitat-sim/EMOS/RoboGuide
commits, and (when the provider exposes it) the relay version. Environment
drift produces reproducibility warnings for diagnosis; it does not by
itself invalidate a pair, because both arms of one pair structurally share
one machine and one checkout. A future protocol may upgrade specific
fields; that is a population-manifest revision, not a code change.

## What would make this schema wrong (guarded by tests)

Seed-as-identity, unavailable-as-equal, unknown-as-mismatch,
declared-as-observed, success-conditioned admission, system-failure-as-
unfairness, episode-51 hard-coding, and cross-population replay each have a
dedicated regression in `test_e1_fairness.py`.
