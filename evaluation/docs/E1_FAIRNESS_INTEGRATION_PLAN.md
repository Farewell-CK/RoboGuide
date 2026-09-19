# E1 Fairness Integration Plan (post semantic-ingress)

Date: 2026-09-19. Baseline: `main@7d35893`.
Companion artifacts: `evaluation/src/roboguide_eval/e1_fairness.py` (model +
validator, already merged into this branch),
`E1_HARNESS_READINESS_AUDIT.md` (evidence for every integration point).

This plan describes how the fairness foundation wires into the real harness
**after** the `codex/b1-semantic-ingress` branch lands. It deliberately does
not patch any of those files now. Tags:

- `INDEPENDENT` — safe to do on this branch without touching Codex-owned
  files (already done or purely additive new files).
- `REQUIRES CODEX OUTPUT` — consumes an artifact the semantic-ingress branch
  produces (or must coordinate on a shared file).
- `SAFE AFTER SEMANTIC MERGE` — modifies files owned by the semantic-ingress
  round; do only after that branch merges.

## Phase A — Population Manifest generation (`INDEPENDENT`, additive)

New file: `evaluation/src/roboguide_eval/e1_population.py` + CLI subcommand
`roboguide-eval population build`.

- Consumes: `evaluation/specs/e1/*.yaml` (dataset digest, task, model),
  `evaluation/e1/dataset-inventory.json` (census), a selector policy file
  (explicit set or future `all`/`predicate`).
- Produces: `population-manifest.json`
  (`roboguide.e1.population-manifest/v0.1`) with benchmark authority
  parameters read from the EMOS checkout task config
  (`config_spot_fetch_mobility.yaml`: `must_call_stop`,
  `robot_at_thresh`, `max_episode_steps`) and Stage2 file digests computed
  over the `stage2_surface` required list.
- `evaluation/e1/pilot-v0.1/episodes.json` becomes a generated projection of
  the manifest rows, not a frozen hand table.

## Phase B — EMOS run evidence producer (`SAFE AFTER SEMANTIC MERGE`)

Files: `evaluation/src/roboguide_eval/systems/emos.py`,
`evaluation/src/roboguide_eval/runner.py` (pairing-fragment assembly only;
metric semantics untouched).

- Extend `resolve_episode_identity` outputs (already digest-verified) into a
  `RunPairingEvidence` fragment: `dataset_identity` (runtime file digest),
  `episode_identity` (banner source), `scene_identity` (dataset lookup),
  `embodiment_profile` (from resolved config `agents_order` + agent names),
  `task_spec_identity` / `habitat_config_identity` (config chain digests,
  computed once per prepare),
  `benchmark_authority_identity` (measure module digest + resolved
  parameters), `stage2_identity` (surface digests),
  `simulator_identity` (probe commits), `model_configuration_identity`
  (`EMOS_LLM_MODEL` value + `OPENAI_BASE_URL` host fingerprint recorded,
  never the API key).
- `system_outcome` from the existing process outcome + EMOS failure
  taxonomy; `requested` from the manifest row.

## Phase C — RoboGuide run evidence producer (`SAFE AFTER SEMANTIC MERGE`)

Files: `evaluation/src/roboguide_eval/systems/roboguide.py`,
`scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh` (post-merge
version), harness arm definition in `evaluation/local.yaml` template docs.

- `dataset_identity`: run `resolve_episode_in_dataset` against
  `ROBOGUIDE_EMOS_ROOT` before the bridge starts (closes audit finding A8).
- `episode_identity` / `scene_identity`: from the bridge-written
  `evidence/shared-world-summary.json` identity block (already a strict
  fact).
- `embodiment_profile`: from the bridge identity/agent handles —
  `REQUIRES CODEX OUTPUT` only if the handle list is not already in the
  summary document; today `identity` carries episode/scene/steps only.
- `stage2_identity`: digest the same `stage2_surface` file list under
  `ROBOGUIDE_EMOS_ROOT` **inside the run script**, after the pair completes
  (proves no mid-pair checkout drift).
- `model_configuration_identity`: record `EMOS_LLM_MODEL` and
  `OPENAI_BASE_URL` values the scenario actually exported (fail the run's
  pairing evidence as `UNAVAILABLE` when unset — no silent inheritance).
- `system_outcome`: MI lifecycle + mission terminal + provenance verdict
  (already collected by the B1 collector).
- B1 input plumbing: `run-b1-roboguide.sh` gains
  `--episode-id/--seed/--input` arguments derived from the population row;
  `ROBOGUIDE_B1_INPUT` finally becomes meaningful.

## Phase D — Pair validator integration (`INDEPENDENT` module, `SAFE AFTER SEMANTIC MERGE` wiring)

New file: `evaluation/src/roboguide_eval/e1_pairs.py` + CLI
`roboguide-eval verify-pair <results-root> --pair-id ...`.

- Loads `population-manifest.json`, both run directories' pairing evidence
  fragments, and calls `e1_fairness.validate_pair`.
- Writes `pair-manifest.json` per pair into the results root with the
  canonical schema from this round.
- `REQUIRES CODEX OUTPUT`: none, but meaningful only after B+C produce
  fragments.

## Phase E — Pair-aware summarization (`SAFE AFTER SEMANTIC MERGE`)

Files: `evaluation/src/roboguide_eval/results.py`,
`evaluation/src/roboguide_eval/cli.py`.

- `summarize_results` gains a pair dimension: aggregates are emitted per
  `pair_id` keyed by `pair-manifest.json` verdicts; `PAIR_NOT_COMPARABLE`
  pairs are reported with reasons and excluded from paired statistics while
  their run evidence stays fully listed (survivorship-bias guard).
- Formal population admission (`valid_for_formal_population`) remains
  untouched and orthogonal — the summary reports both views side by side.

## Phase F — Pilot execution (`SAFE AFTER SEMANTIC MERGE`)

Files: `evaluation/e1/pilot-v0.1/run-pair.sh`, harness spec
(`evaluation/specs/e1/`).

- `run-pair.sh` is replaced by a loop over `population-manifest.json` rows:
  for each row run arm A then arm B, then `verify-pair` before the next row.
  No post-hoc pair selection; every started pair produces a manifest
  whether comparable or not (anti-survivorship requirement).
- First execution scope: the manifest's explicit rows; failure of one pair
  never stops the population loop (each pair's evidence is retained).

## Cross-cutting follow-ups owned by the semantic-ingress branch

These are recorded here so they are not lost, and are **not** implemented in
this round:

1. `INTEGRATION FOLLOW-UP (Codex-owned files)` — bridge seed plumb: the
   shared-world bridge process accepts a seed and passes
   `habitat.seed={seed}` in its `get_config` overrides
   (`integrations/habitat-local-eaios/habitat_local_eaios/backend.py:118-127`
   and the scenario scripts' bridge invocation), so
   `randomize_agent_start: 1` start states are reproducible across arms.
   Suggested patch location: `--seed` argument in
   `habitat_local_eaios/__main__.py` next to `--episode-id`, plus the two
   scenario scripts.
2. `INTEGRATION FOLLOW-UP (Codex-owned files)` — episode/agent handles in
   the shared-world summary identity block (embodiment observation for
   Phase C), same file `shared_world.py` identity construction (lines 76-84).
3. `INTEGRATION FOLLOW-UP` — pin and record `EMOS_LLM_MODEL` /
   `OPENAI_BASE_URL` for the RoboGuide arm's Stage2 inside
   `run-b1-roboguide.sh` (fail fast when unset) — small shell change, do
   together with 1.
